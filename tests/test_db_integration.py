"""Synthetic HTTP + real MySQL checks. IOT_TEST_DB_NAME must be disposable."""

import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pymysql
import pytest
from fastapi.testclient import TestClient
from pymysql.cursors import DictCursor


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api" / "mock-fastapi"))
import app as app_module  # noqa: E402


@pytest.fixture
def db_case(monkeypatch):
    name = os.environ.get("IOT_TEST_DB_NAME")
    if os.environ.get("IOT_TEST_DB_ISOLATED") != "1" or not name:
        pytest.fail("real-DB tests require IOT_TEST_DB_ISOLATED=1 and IOT_TEST_DB_NAME")
    config = {
        "host": os.environ.get("IOT_TEST_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("IOT_TEST_DB_PORT", "3306")),
        "user": os.environ.get("IOT_TEST_DB_USER", "root"),
        "password": os.environ.get("IOT_TEST_DB_PASS", ""),
        "database": name,
        "charset": "utf8mb4",
        "autocommit": False,
    }

    def connect(cursorclass=DictCursor):
        return pymysql.connect(**config, cursorclass=cursorclass)

    # Force every application request to the exact opt-in synthetic database.
    monkeypatch.setattr(app_module, "get_conn", connect)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DATABASE() AS db")
        assert cur.fetchone()["db"] == name
        cur.execute("SHOW TABLES LIKE 'sim_purchase'")
        assert cur.fetchone(), "run all seven migrations on the isolated database first"
        cur.execute("SHOW COLUMNS FROM sim_purchase LIKE 'price_cent'")
        assert cur.fetchone(), "V009 is required"

    cards = []
    tokens = []

    def make_card(owner="alice", seed_usage=False):
        iccid = "TEST" + uuid.uuid4().hex[:24].upper()
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM user_account WHERE username=%s", (owner,))
            user = cur.fetchone()
            assert user, "V006 demo users are required"
            cur.execute(
                "INSERT INTO sim_card(iccid, status, owner_user_id) VALUES(%s, 'ACTIVE', %s)",
                (iccid, user["user_id"]),
            )
            if seed_usage:
                cur.execute(
                    "INSERT INTO sim_usage(iccid, month, used_mb, package_mb) VALUES(%s, '2031-07', 200, 1024)",
                    (iccid,),
                )
            conn.commit()
        cards.append(iccid)
        return iccid

    def scalar(sql, params=()):
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return next(iter(cur.fetchone().values()))

    with TestClient(app_module.app) as client:
        def login(username, password):
            response = client.post("/auth/login", json={"username": username, "password": password})
            assert response.status_code == 200, response.text
            token = response.json()["data"]["token"]
            tokens.append(token)
            return token

        case = SimpleNamespace(
            client=client, connect=connect, make_card=make_card, scalar=scalar,
            alice=login("alice", "user1pass"), bob=login("bob", "user2pass"),
        )
        try:
            yield case
        finally:
            with connect() as conn, conn.cursor() as cur:
                for iccid in cards:
                    cur.execute("DELETE FROM sim_card WHERE iccid=%s", (iccid,))
                for token in tokens:
                    cur.execute("DELETE FROM auth_token WHERE token=%s", (token,))
                conn.commit()


def headers(token, transid=None):
    result = {"Authorization": f"Bearer {token}"}
    if transid is not None:
        result["X-TransId"] = transid
    return result


def purchase(case, iccid, body, key=None, token=None):
    return case.client.post(
        f"/sims/{iccid}/purchase", json=body,
        headers=headers(token or case.alice, key),
    )


def test_http_flow_replay_conflict_and_ownership(db_case):
    case = db_case
    iccid = case.make_card(seed_usage=True)
    other_alice = case.make_card()
    bob_card = case.make_card("bob")
    key = "synthetic-" + uuid.uuid4().hex
    body = {"month": "2031-07", "package_mb": 77, "product_id": "pkg_77", "pay_amount_cent": 123}
    search = case.client.get("/sims/search", params={"iccid": iccid}, headers=headers(case.alice))
    assert search.status_code == 200 and search.json()["data"]["iccid"] == iccid
    usage = case.client.get(f"/sims/{iccid}/usage", params={"month": "2031-07"}, headers=headers(case.alice))
    assert usage.status_code == 200 and usage.json()["data"]["package_mb"] == 1024

    first = purchase(case, iccid, body, key)
    assert first.status_code == 200, first.text
    order = first.json()["data"]
    assert (order["product_id"], order["price_cent"]) == ("pkg_77", 123)
    same = purchase(case, iccid, body, key)
    assert same.status_code == 200 and same.json()["data"]["order_id"] == order["order_id"]
    listing = case.client.get(f"/sims/{iccid}/purchases", params={"month": "2031-07"}, headers=headers(case.alice))
    assert listing.status_code == 200
    assert (listing.json()["data"]["items"][0]["product_id"], listing.json()["data"]["items"][0]["price_cent"]) == ("pkg_77", 123)

    variations = [
        (iccid, {**body, "month": "2031-08"}),
        (iccid, {**body, "package_mb": 78}),
        (iccid, {**body, "product_id": "other"}),
        (iccid, {**body, "pay_amount_cent": 124}),
        (other_alice, body),
    ]
    for target, changed in variations:
        conflict = purchase(case, target, changed, key)
        assert conflict.status_code == 409 and conflict.json() == {"detail": "X-TransId conflict"}
    forbidden = purchase(case, iccid, body, key, case.bob)
    assert forbidden.status_code == 403 and "data" not in forbidden.json()
    cross_owner_key = purchase(case, bob_card, body, key, case.bob)
    assert cross_owner_key.status_code == 409 and "data" not in cross_owner_key.json()
    assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (key,)) == 1
    assert case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2031-07'", (iccid,)) == 1101

    null_key = "synthetic-" + uuid.uuid4().hex
    null_body = {"month": "2031-07", "package_mb": 1}
    assert purchase(case, iccid, null_body, null_key).status_code == 200
    assert purchase(case, iccid, {**null_body, "product_id": None, "pay_amount_cent": None}, null_key).status_code == 200
    assert purchase(case, iccid, {**null_body, "pay_amount_cent": 0}, null_key).status_code == 409


@pytest.mark.parametrize("different", [False, True])
def test_insert_race_uses_real_unique_key(db_case, monkeypatch, different):
    case = db_case
    iccid = case.make_card()
    key = "race-" + uuid.uuid4().hex
    barrier = threading.Barrier(2)
    observed = []
    lock = threading.Lock()

    class RacingCursor(DictCursor):
        def execute(self, query, args=None):
            is_insert = "INSERT INTO sim_purchase" in query and args and args[-1] == key
            if is_insert:
                barrier.wait(timeout=10)  # Both initial transid SELECTs missed.
            try:
                return super().execute(query, args)
            except pymysql.err.IntegrityError as error:
                if is_insert:
                    with lock:
                        observed.append(error)
                raise

    monkeypatch.setattr(app_module, "get_conn", lambda: case.connect(cursorclass=RacingCursor))
    body = {"month": "2031-07", "package_mb": 100, "product_id": None, "pay_amount_cent": None}
    other_body = {**body, "pay_amount_cent": 1} if different else body
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(purchase, case, iccid, body, key)
        second = pool.submit(purchase, case, iccid, other_body, key)
        responses = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(response.status_code for response in responses) == ([200, 409] if different else [200, 200])
    assert len(observed) == 1 and app_module.transid_duplicate(observed[0])
    assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (key,)) == 1
    assert case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2031-07'", (iccid,)) == 100
    if different:
        assert next(response.json() for response in responses if response.status_code == 409) == {"detail": "X-TransId conflict"}
    else:
        assert responses[0].json()["data"]["order_id"] == responses[1].json()["data"]["order_id"]


def test_non_transid_integrity_error_is_not_a_replay(db_case, monkeypatch):
    case = db_case
    iccid = case.make_card()
    fixed_order_id = "PO203001010000001234"
    with case.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sim_purchase(order_id, iccid, month, package_mb, status, transid) VALUES(%s,%s,'2031-07',1,'SUCCESS',%s)",
            (fixed_order_id, iccid, "existing-" + uuid.uuid4().hex),
        )
        conn.commit()

    class FixedDatetime:
        @staticmethod
        def now():
            return datetime(2030, 1, 1, 0, 0, 0)

    monkeypatch.setattr(app_module, "datetime", FixedDatetime)
    monkeypatch.setattr(app_module.random, "randint", lambda _start, _end: 1234)
    key = "other-" + uuid.uuid4().hex
    response = purchase(case, iccid, {"month": "2031-07", "package_mb": 4}, key)
    assert response.status_code == 500 and response.json() == {"detail": "DB_ERROR"}
    assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (key,)) == 0
    assert case.scalar("SELECT COUNT(*) FROM sim_usage WHERE iccid=%s", (iccid,)) == 0


def test_usage_write_error_and_overflow_rollback_order(db_case):
    case = db_case
    iccid = case.make_card()
    trigger = "test_usage_fail_" + uuid.uuid4().hex[:12]
    with case.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"CREATE TRIGGER `{trigger}` BEFORE INSERT ON sim_usage FOR EACH ROW "
            f"BEGIN IF NEW.iccid = '{iccid}' THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'synthetic usage failure'; "
            "END IF; END"
        )
        conn.commit()
    try:
        key = "usage-fail-" + uuid.uuid4().hex
        failed = purchase(case, iccid, {"month": "2031-07", "package_mb": 1}, key)
        assert failed.status_code == 500 and failed.json() == {"detail": "DB_ERROR"}
        assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (key,)) == 0
        assert case.scalar("SELECT COUNT(*) FROM sim_usage WHERE iccid=%s", (iccid,)) == 0
    finally:
        with case.connect() as conn, conn.cursor() as cur:
            cur.execute(f"DROP TRIGGER `{trigger}`")
            conn.commit()

    with case.connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sim_usage(iccid, month, used_mb, package_mb) VALUES(%s,'2031-07',0,2147483646)", (iccid,))
        conn.commit()
    overflow_key = "overflow-" + uuid.uuid4().hex
    overflow = purchase(case, iccid, {"month": "2031-07", "package_mb": 2}, overflow_key)
    assert overflow.status_code == 409 and overflow.json() == {"detail": "package_mb capacity exceeded"}
    assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (overflow_key,)) == 0
    assert case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2031-07'", (iccid,)) == 2147483646


def test_input_bounds_and_shared_month_validation(db_case):
    case = db_case
    iccid = case.make_card()
    for month in ["2031-00", "2031-13", "2031-1", "0000-01", "2031-01extra", "２０３１-０１"]:
        assert purchase(case, iccid, {"month": month, "package_mb": 1}).status_code == 400
        assert case.client.get(f"/sims/{iccid}/usage", params={"month": month}, headers=headers(case.alice)).status_code == 400
        assert case.client.get(f"/sims/{iccid}/purchases", params={"month": month}, headers=headers(case.alice)).status_code == 400
    for value in [0, -1, 2_147_483_648, True, 1.5]:
        assert purchase(case, iccid, {"month": "2031-07", "package_mb": value}).status_code == 422
    for value in [-1, 2_147_483_648, True]:
        assert purchase(case, iccid, {"month": "2031-07", "package_mb": 1, "pay_amount_cent": value}).status_code == 422
    assert purchase(case, iccid, {"month": "2031-07", "package_mb": 1, "product_id": "x" * 65}).status_code == 422
    for key in ["", " ", "x" * 65]:
        assert purchase(case, iccid, {"month": "2031-07", "package_mb": 1}, key).status_code == 400
    converted = purchase(case, iccid, {"month": "2031-07", "package_mb": "2", "pay_amount_cent": "0"})
    assert converted.status_code == 200 and converted.json()["data"]["package_mb"] == 2
    assert converted.json()["data"]["price_cent"] == 0
    assert converted.json()["trace"]["transid"]


def test_repeating_safe_seed_keeps_new_usage(db_case):
    case = db_case
    iccid = "8986001200000000001"
    before = case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2025-08'", (iccid,))
    key = "seed-check-" + uuid.uuid4().hex
    try:
        added = purchase(case, iccid, {"month": "2025-08", "package_mb": 7}, key)
        assert added.status_code == 200, added.text
        updated = case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2025-08'", (iccid,))
        assert updated == before + 7
        source = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
        source = source.replace("USE iot_sim_ops;", f"USE `{os.environ['IOT_TEST_DB_NAME']}`;")
        with case.connect() as conn, conn.cursor() as cur:
            for statement in source.split(";"):
                if statement.strip():
                    cur.execute(statement)
            conn.commit()
        assert case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2025-08'", (iccid,)) == updated
    finally:
        with case.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sim_purchase WHERE transid=%s", (key,))
            cur.execute("UPDATE sim_usage SET package_mb=%s WHERE iccid=%s AND month='2025-08'", (before, iccid))
            conn.commit()
