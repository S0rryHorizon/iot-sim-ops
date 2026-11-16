import logging
import os
from contextlib import contextmanager
from threading import Lock

import pymysql
from fastapi import HTTPException
from pymysql.cursors import DictCursor
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool

from .parsing import parse_bool

_pool = None
_pool_lock = Lock()
logger = logging.getLogger("biz")


def http_error(error: pymysql.MySQLError) -> HTTPException:
    # Deadlock victims and lock-wait timeouts were rolled back; the caller may retry.
    busy = bool(error.args) and error.args[0] in (1213, 1205)
    return HTTPException(status_code=503 if busy else 500, detail="DB_BUSY" if busy else "DB_ERROR")


@contextmanager
def rollback_on_error(connection):
    try:
        yield
    except Exception as error:
        try:
            connection.rollback()
        except pymysql.MySQLError:
            # A dropped connection cannot roll back; the server already discarded the
            # transaction, and the original error still decides the response.
            logger.warning("Rollback failed on a broken connection")
        if isinstance(error, pymysql.MySQLError):
            logger.exception("Database transaction failed")
            raise http_error(error) from error
        raise


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
        # Lock waits must end before read_timeout, so a busy row is reported as 1205
        # (503 DB_BUSY) instead of a dropped connection.
        init_command="SET SESSION innodb_lock_wait_timeout=5",
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
