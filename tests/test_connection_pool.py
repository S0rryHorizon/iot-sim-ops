"""Pool boundaries that protect transactions and bounded database capacity."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pymysql
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import app as app_module
from api import db


class Connection:
    def __init__(self):
        self.rollbacks = 0
        self.closed = False
        self.broken = False

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def ping(self, reconnect=False):
        assert not reconnect  # Never silently reconnect in an existing transaction.
        if self.broken:
            raise pymysql.OperationalError(2006, "synthetic disconnect")


@pytest.fixture
def pool(monkeypatch):
    db.dispose_pool()
    created = []

    def connect():
        connection = Connection()
        created.append(connection)
        return connection

    monkeypatch.setattr(db, "_open_connection", connect)
    for name, value in {
        "DB_POOL_ENABLED": "true",
        "DB_POOL_SIZE": "1",
        "DB_POOL_OVERFLOW": "0",
        "DB_POOL_TIMEOUT": "0.01",
    }.items():
        monkeypatch.setenv(name, value)
    yield created
    db.dispose_pool()


def test_reuse_resets_transaction_even_after_exception(pool):
    with pytest.raises(RuntimeError):
        with db.get_conn() as first:
            original = first.driver_connection
            raise RuntimeError("synthetic request failure")
    assert original.rollbacks == 1
    with db.get_conn() as second:
        assert second.driver_connection is original
    assert len(pool) == 1 and original.rollbacks == 2


def test_dead_idle_connection_is_replaced(pool):
    with db.get_conn() as first:
        original = first.driver_connection
    original.broken = True
    with db.get_conn() as second:
        assert second.driver_connection is not original
    assert original.closed and len(pool) == 2


def test_pool_exhaustion_is_bounded(pool):
    with db.get_conn():
        with pytest.raises(HTTPException) as error:
            with db.get_conn():
                pytest.fail("pool size limit was ignored")
        assert error.value.status_code == 503
        assert error.value.detail == "DB_BUSY"
    assert len(pool) == 1


def test_concurrent_first_init_builds_one_pool(pool, monkeypatch):
    workers = 8
    start = threading.Barrier(workers)
    built = []
    queue_pool = db.QueuePool

    def build(*args, **kwargs):
        instance = queue_pool(*args, **kwargs)
        built.append(instance)
        time.sleep(0.02)  # Keep initialization open while other callers arrive.
        return instance

    def initialize(_):
        start.wait(timeout=5)
        return db._connection_pool()

    monkeypatch.setattr(db, "QueuePool", build)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        instances = list(executor.map(initialize, range(workers)))
    assert len(built) == 1
    assert all(instance is built[0] for instance in instances)
    assert pool == []


def test_lifespan_exit_disposes_pool(pool):
    with TestClient(app_module.create_app()) as client:
        assert client.get("/alive").status_code == 200
        with db.get_conn() as connection:
            physical = connection.driver_connection
        previous_pool = db._connection_pool()
        assert not physical.closed
    assert physical.closed
    assert db._connection_pool() is not previous_pool


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_connection_open_failure_returns_503(pool, monkeypatch, enabled):
    attempts = []

    def fail_to_open():
        attempts.append(1)
        raise pymysql.OperationalError(2003, "synthetic connect failure")

    monkeypatch.setenv("DB_POOL_ENABLED", enabled)
    monkeypatch.setattr(db, "_open_connection", fail_to_open)
    with TestClient(app_module.create_app()) as client:
        response = client.get("/sims/search?iccid=test", headers={"Authorization": "Bearer token"})
    assert response.status_code == 503
    assert response.json() == {"detail": "DB_UNAVAILABLE"}
    assert len(attempts) == 1


def test_dispose_rereads_pool_parameters(pool, monkeypatch):
    previous_pool = db._connection_pool()
    assert previous_pool.size() == 1
    db.dispose_pool()
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    rebuilt = db._connection_pool()
    assert rebuilt is not previous_pool
    assert rebuilt.size() == 3


def test_started_transaction_is_not_retried(pool):
    with pytest.raises(pymysql.OperationalError):
        with db.get_conn() as connection:
            physical = connection.driver_connection
            raise pymysql.OperationalError(2013, "synthetic transaction failure")
    assert len(pool) == 1
    assert physical.rollbacks == 1
