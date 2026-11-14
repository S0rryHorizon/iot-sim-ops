import re
from contextlib import nullcontext
from unittest.mock import MagicMock

import bcrypt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import app as app_module
from api import auth, db


def test_alive_payload():
    assert app_module.alive() == {
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
    assert app_module.parse_bool(value) is True


@pytest.mark.parametrize("value", ["false", "0", "NO", "off"])
def test_boolean_parser_accepts_false_values(value):
    assert app_module.parse_bool(value) is False


def test_boolean_parser_rejects_unknown_value():
    with pytest.raises(ValueError):
        app_module.parse_bool("sometimes")


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
    assert statements[1].args[0].strip().startswith("INSERT INTO auth_token")
    assert statements[1].args[1] == (token, 1, None)
    assert statements[2].args[0].startswith("UPDATE user_account SET last_login_at")
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
