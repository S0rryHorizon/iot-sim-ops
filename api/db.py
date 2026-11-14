import os
from contextlib import contextmanager
from threading import Lock

import pymysql
from fastapi import HTTPException
from pymysql.cursors import DictCursor
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool

from .schemas import parse_bool

_pool = None
_pool_lock = Lock()


def _open_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS", ""),
        db=os.getenv("DB_NAME", "iot_sim_ops"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=DictCursor,
        unix_socket=os.getenv("DB_SOCKET") or None,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def _connection_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            # A pool belongs to one server process. Returned connections are rolled back;
            # an authentication SELECT must not leak its transaction into the next request.
            _pool = QueuePool(
                _open_connection,
                pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
                max_overflow=int(os.getenv("DB_POOL_OVERFLOW", "10")),
                timeout=float(os.getenv("DB_POOL_TIMEOUT", "5")),
                recycle=1800,
                reset_on_return="rollback",
            )
        return _pool


def dispose_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.dispose()
            _pool = None


@contextmanager
def get_conn():
    try:
        if not parse_bool(os.getenv("DB_POOL_ENABLED", "true")):
            connection = _open_connection()
        else:
            for attempt in range(2):
                try:
                    connection = _connection_pool().connect()
                except PoolTimeout:
                    raise HTTPException(status_code=503, detail="DB_BUSY") from None
                try:
                    connection.ping(reconnect=False)
                    break
                except pymysql.MySQLError:
                    connection.invalidate()
                    if attempt:
                        raise HTTPException(status_code=503, detail="DB_UNAVAILABLE") from None
    except pymysql.MySQLError:
        raise HTTPException(status_code=503, detail="DB_UNAVAILABLE") from None
    try:
        yield connection
    finally:
        connection.close()
