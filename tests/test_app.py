import re
from contextlib import nullcontext
from unittest.mock import MagicMock

import bcrypt
import pymysql
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import app as app_module
from api import auth, db, purchases, schemas
from api.parsing import parse_bool


def test_alive_payload():
    assert TestClient(app_module.app).get("/alive").json() == {
        "ok": True,
        "service": "iot-sim-ops",
        "version": "0.4.0",
    }


def test_expected_routes_are_registered():
    paths = app_module.app.openapi()["paths"]
    routes = {(method.upper(), path) for path, operations in paths.items() for method in operations}
    assert routes == {
        ("GET", "/alive"),
        ("POST", "/auth/login"),
        ("GET", "/sims/search"),
        ("GET", "/sims/{iccid}/status"),
        ("PATCH", "/sims/{iccid}/status"),
        ("GET", "/sims/{iccid}/usage"),
        ("POST", "/sims/{iccid}/purchase"),
        ("GET", "/sims/{iccid}/purchases"),
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/sims/search?iccid=test", None),
        ("GET", "/sims/test/status", None),
        ("PATCH", "/sims/test/status", {"action": "SUSPEND"}),
        ("GET", "/sims/test/usage?month=2031-07", None),
        ("POST", "/sims/test/purchase", {"month": "2031-07", "package_mb": 1}),
        ("GET", "/sims/test/purchases", None),
    ],
)
def test_protected_routes_reach_auth_without_database_access(monkeypatch, method, path, body):
    def unexpected_connection():
        pytest.fail("missing bearer token must not access the database")

    monkeypatch.setattr(db, "get_conn", unexpected_connection)
    with TestClient(app_module.create_app()) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json() == {"detail": "missing bearer token"}


def test_missing_token_precedes_invalid_body(monkeypatch):
    def unexpected_connection():
        pytest.fail("missing bearer token must not access the database")

    monkeypatch.setattr(db, "get_conn", unexpected_connection)
    with TestClient(app_module.create_app()) as client:
        response = client.post("/sims/test/purchase", json={"month": "2031-07", "package_mb": 0})
    assert response.status_code == 401
    assert response.json() == {"detail": "missing bearer token"}


def test_cors_origin_parser_trims_and_drops_empty_values():
    assert app_module.parse_cors_origins("https://one.example, ,https://two.example") == [
        "https://one.example",
        "https://two.example",
    ]
    assert app_module.parse_cors_origins("") == ["*"]


@pytest.mark.parametrize("value", ["true", "1", "YES", "on"])
def test_boolean_parser_accepts_true_values(value):
    assert parse_bool(value) is True


@pytest.mark.parametrize("value", ["false", "0", "NO", "off"])
def test_boolean_parser_accepts_false_values(value):
    assert parse_bool(value) is False


def test_boolean_parser_rejects_unknown_value():
    with pytest.raises(ValueError):
        parse_bool("sometimes")


def test_missing_bearer_token_is_rejected_without_database_access(monkeypatch):
    def unexpected_connection():
        pytest.fail("missing bearer token must not access the database")

    monkeypatch.setattr(db, "get_conn", unexpected_connection)
    with pytest.raises(HTTPException) as exc_info:
        auth.require_auth_user(None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing bearer token"


@pytest.fixture
def cursor_connection(monkeypatch):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(db, "get_conn", lambda: nullcontext(connection))
    return cursor, connection


def test_login_uses_local_token_and_empty_trace(cursor_connection):
    cursor, connection = cursor_connection
    cursor.fetchone.return_value = {
        "user_id": 1,
        "is_active": True,
        "password_hash": bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode(),
    }
    with TestClient(app_module.create_app()) as client:
        response = client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert response.status_code == 200
    payload = response.json()
    token = payload["data"]["token"]
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    assert payload == {
        "code": "0",
        "msg": "ok",
        "data": {"token": token, "token_type": "Bearer", "expires_in": 3600},
        "trace": {},
    }
    statements = cursor.execute.call_args_list
    assert len(statements) == 3
    assert statements[0].args[0].startswith("SELECT user_id, password_hash, is_active")
    assert statements[1].args[0].startswith("UPDATE user_account SET last_login_at")
    assert statements[2].args[0].strip().startswith("INSERT INTO auth_token")
    assert statements[2].args[1] == (token, 1, None)
    connection.commit.assert_called_once_with()
    connection.rollback.assert_not_called()


def test_search_keeps_owner_filter_and_empty_trace(cursor_connection):
    cursor, _ = cursor_connection
    row = {"iccid": "sim", "status": "ACTIVE"}
    cursor.fetchone.return_value = row
    application = app_module.create_app()
    application.dependency_overrides[auth.current_user] = lambda: {"user_id": 1}
    with TestClient(application) as client:
        response = client.get("/sims/search?iccid=sim")
    assert response.status_code == 200
    assert response.json() == {"code": "0", "msg": "ok", "data": row, "trace": {}}
    statement = cursor.execute.call_args
    assert "WHERE owner_user_id=%s AND iccid=%s" in statement.args[0]
    assert statement.args[1] == [1, "sim"]


@pytest.mark.parametrize(
    ("row", "status", "detail"),
    [
        (None, 404, "not found"),
        ({"owner_user_id": 2}, 403, "forbidden"),
    ],
)
def test_ownership_helper_rejects_missing_or_foreign_sim(cursor_connection, row, status, detail):
    cursor, connection = cursor_connection
    cursor.fetchone.return_value = row
    with pytest.raises(HTTPException) as error:
        auth.require_owned_sim(cursor, "sim", 1)
    assert (error.value.status_code, error.value.detail) == (status, detail)
    cursor.execute.assert_called_once_with(
        "SELECT owner_user_id FROM sim_card WHERE iccid=%s", ("sim",)
    )
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def prepare_purchase(cursor):
    order = {"order_id": "PO-test", "package_mb": 7}
    cursor.fetchone.side_effect = [{"owner_user_id": 1}, None, order]
    cursor.rowcount = 1
    return order


def call_purchase():
    return purchases.purchase(
        "sim", schemas.PurchaseBody(month="2031-07", package_mb=7), {"user_id": 1}, "key"
    )


def test_purchase_capacity_rejection_rolls_back(cursor_connection):
    cursor, connection = cursor_connection
    prepare_purchase(cursor)
    cursor.rowcount = 0
    with pytest.raises(HTTPException) as error:
        call_purchase()
    assert (error.value.status_code, error.value.detail) == (409, "package_mb capacity exceeded")
    update = cursor.execute.call_args
    assert update.args[0].strip().startswith("UPDATE sim_usage")
    assert update.args[1] == (7, "sim", "2031-07", purchases.INT_MAX - 7)
    assert cursor.fetchone.call_count == 2
    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_purchase_readback_and_encoding_precede_commit(cursor_connection, monkeypatch):
    cursor, connection = cursor_connection
    order = prepare_purchase(cursor)
    events = []
    cursor.execute.side_effect = lambda query, args: events.append(" ".join(query.split()))
    connection.commit.side_effect = lambda: events.append("commit")
    encode = schemas.jsonable_encoder

    def record_encoding(data):
        events.append("encode")
        return encode(data)

    monkeypatch.setattr(schemas, "jsonable_encoder", record_encoding)
    response = call_purchase()
    assert response["data"] == order and response["trace"] == {"transid": "key"}
    assert events[-3:] == ["SELECT * FROM sim_purchase WHERE transid=%s", "encode", "commit"]
    assert all("FOR UPDATE" not in event for event in events)
    connection.commit.assert_called_once_with()
    connection.rollback.assert_not_called()


@pytest.mark.parametrize("failure", ["readback", "missing", "encoding"])
def test_purchase_precommit_failure_rolls_back(cursor_connection, monkeypatch, failure):
    cursor, connection = cursor_connection
    prepare_purchase(cursor)
    if failure == "encoding":

        def fail_encoding(data):
            raise ValueError("synthetic encoding failure")

        monkeypatch.setattr(schemas, "jsonable_encoder", fail_encoding)
    else:
        readback = None if failure == "missing" else pymysql.OperationalError(1644, "read failed")
        cursor.fetchone.side_effect = [{"owner_user_id": 1}, None, readback]
    with pytest.raises(ValueError if failure == "encoding" else HTTPException) as error:
        call_purchase()
    if failure != "encoding":
        assert (error.value.status_code, error.value.detail) == (500, "DB_ERROR")
    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "code,status,detail",
    [(1213, 503, "DB_BUSY"), (1205, 503, "DB_BUSY"), (1062, 500, "DB_ERROR")],
)
@pytest.mark.parametrize("endpoint", ["purchase", "status", "login"])
def test_transaction_commit_errors_are_mapped(
    cursor_connection, monkeypatch, code, status, detail, endpoint
):
    cursor, connection = cursor_connection
    connection.commit.side_effect = pymysql.OperationalError(code, "synthetic commit failure")
    log = MagicMock()
    monkeypatch.setattr(auth.logger, "info", log)
    if endpoint == "purchase":
        prepare_purchase(cursor)
        invoke = call_purchase
    elif endpoint == "status":
        cursor.fetchone.return_value = {"owner_user_id": 1, "status": "ACTIVE"}

        def invoke():
            return app_module.change_status(
                "sim", schemas.ChangeStatus(action="SUSPEND"), {"user_id": 1, "username": "alice"}
            )
    else:
        cursor.fetchone.return_value = {
            "user_id": 1,
            "is_active": True,
            "password_hash": bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode(),
        }

        def invoke():
            return auth.auth_login(schemas.Login(username="alice", password="secret"))

    with pytest.raises(HTTPException) as error:
        invoke()
    assert (error.value.status_code, error.value.detail) == (status, detail)
    connection.rollback.assert_called_once_with()
    log.assert_not_called()


def test_repeated_status_writes_noop_without_update(cursor_connection):
    cursor, connection = cursor_connection
    cursor.fetchone.return_value = {"owner_user_id": 1, "status": "SUSPENDED"}
    response = app_module.change_status(
        "sim", schemas.ChangeStatus(action="SUSPEND"), {"user_id": 1, "username": "alice"}
    )
    assert response["data"] == {"iccid": "sim", "status": "SUSPENDED"}
    statements = cursor.execute.call_args_list
    assert len(statements) == 2
    assert statements[0].args == (
        "SELECT owner_user_id, status FROM sim_card WHERE iccid=%s FOR UPDATE",
        ("sim",),
    )
    assert statements[1].args[0].strip().startswith("INSERT INTO sim_op_log")
    assert statements[1].args[1] == ("sim", "SUSPEND", "NOOP", "alice")
    connection.commit.assert_called_once_with()
    connection.rollback.assert_not_called()


def test_deactivated_status_rolls_back_without_audit(cursor_connection):
    cursor, connection = cursor_connection
    cursor.fetchone.return_value = {"owner_user_id": 1, "status": "DEACTIVATED"}
    with pytest.raises(HTTPException) as error:
        app_module.change_status(
            "sim", schemas.ChangeStatus(action="RESUME"), {"user_id": 1, "username": "alice"}
        )
    assert (error.value.status_code, error.value.detail) == (409, "sim deactivated")
    assert cursor.execute.call_count == 1
    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_status_audit_failure_rolls_back(cursor_connection):
    cursor, connection = cursor_connection
    cursor.fetchone.return_value = {"owner_user_id": 1, "status": "ACTIVE"}

    def fail_audit(query, args):
        if "INSERT INTO sim_op_log" in query:
            raise pymysql.OperationalError(1644, "synthetic audit failure")

    cursor.execute.side_effect = fail_audit
    with pytest.raises(HTTPException) as error:
        app_module.change_status(
            "sim", schemas.ChangeStatus(action="SUSPEND"), {"user_id": 1, "username": "alice"}
        )
    assert (error.value.status_code, error.value.detail) == (500, "DB_ERROR")
    assert cursor.execute.call_args_list[1].args[0].startswith("UPDATE sim_card")
    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_transaction_error_helper_preserves_http_exception(cursor_connection):
    _, connection = cursor_connection
    original = HTTPException(status_code=403, detail="forbidden")
    with pytest.raises(HTTPException) as error:
        with db.rollback_on_error(connection):
            raise original
    assert error.value is original
    connection.rollback.assert_called_once_with()


def test_broken_connection_keeps_the_original_error_mapping(cursor_connection):
    _, connection = cursor_connection
    connection.rollback.side_effect = pymysql.err.InterfaceError(0, "")
    with pytest.raises(HTTPException) as error:
        with db.rollback_on_error(connection):
            raise pymysql.err.OperationalError(2013, "Lost connection")
    assert (error.value.status_code, error.value.detail) == (500, "DB_ERROR")


def test_read_route_database_error_uses_the_json_error_shape(monkeypatch):
    def broken_connection():
        raise pymysql.err.OperationalError(1205, "Lock wait timeout exceeded")

    monkeypatch.setattr(db, "get_conn", broken_connection)
    client = TestClient(app_module.app, raise_server_exceptions=False)
    response = client.get("/sims/search?iccid=1", headers={"Authorization": "Bearer x"})
    assert (response.status_code, response.json()) == (503, {"detail": "DB_BUSY"})
