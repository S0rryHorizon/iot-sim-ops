"""Pool boundaries that protect transactions and bounded database capacity."""
import sys
from pathlib import Path

import pytest
import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api/mock-fastapi"))
import app


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
    app._connection_pool.cache_clear()
    created = []
    def connect():
        connection = Connection()
        created.append(connection)
        return connection
    monkeypatch.setattr(app, "_open_connection", connect)
    for name, value in {"DB_POOL_ENABLED":"true", "DB_POOL_SIZE":"1",
                        "DB_POOL_OVERFLOW":"0", "DB_POOL_TIMEOUT":"0.01"}.items():
        monkeypatch.setenv(name, value)
    yield created
    app._connection_pool().dispose()
    app._connection_pool.cache_clear()


def test_reuse_resets_transaction_even_after_exception(pool):
    with pytest.raises(RuntimeError):
        with app.get_conn() as first:
            original = first.driver_connection
            raise RuntimeError("synthetic request failure")
    assert original.rollbacks == 1
    with app.get_conn() as second:
        assert second.driver_connection is original
    assert len(pool) == 1 and original.rollbacks == 2


def test_dead_idle_connection_is_replaced(pool):
    with app.get_conn() as first:
        original = first.driver_connection
    original.broken = True
    with app.get_conn() as second:
        assert second.driver_connection is not original
    assert original.closed and len(pool) == 2


def test_pool_exhaustion_is_bounded(pool):
    with app.get_conn():
        with pytest.raises(app.HTTPException) as error:
            with app.get_conn():
                pytest.fail("pool size limit was ignored")
        assert error.value.status_code == 503
        assert error.value.detail == "DB_BUSY"
    assert len(pool) == 1
