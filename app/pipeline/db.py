"""
db.py
-----
Shared MySQL connection, reused across calls instead of opening a fresh
TCP+auth connection every time. One connection per worker thread (RQ runs
single-threaded per job, but threading.local keeps this safe even if a
caller ever runs DB calls from multiple threads).

Required env vars: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
"""

import os
import logging
import threading

import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)

_local = threading.local()


def _connect() -> pymysql.Connection:
    return pymysql.connect(
        host            = os.environ["DB_HOST"],
        port            = int(os.environ.get("DB_PORT", 3306)),
        user            = os.environ["DB_USER"],
        password        = os.environ["DB_PASSWORD"],
        database        = os.environ["DB_NAME"],
        charset         = "utf8mb4",
        cursorclass     = pymysql.cursors.DictCursor,
        connect_timeout = 10,
    )


def get_connection() -> pymysql.Connection:
    """Returns a live connection, reusing the cached one if it still pings."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            logger.warning("Cached MySQL connection is dead, reconnecting")

    conn = _connect()
    _local.conn = conn
    return conn
