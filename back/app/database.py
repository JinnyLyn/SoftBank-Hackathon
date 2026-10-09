from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os

import pymysql
from pymysql.connections import Connection

from app.settings import database_settings


@contextmanager
def connect(*, autocommit: bool = False) -> Iterator[Connection]:
    config = database_settings()
    options = {}
    ssl_ca = os.getenv("DB_SSL_CA", "").strip()
    if ssl_ca:
        options["ssl"] = {"ca": ssl_ca, "check_hostname": True}
    connection = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=15,
        write_timeout=15,
        autocommit=autocommit,
        init_command="SET time_zone = '+00:00'",
        **options,
    )
    try:
        yield connection
    finally:
        connection.close()


def check_database() -> None:
    with connect(autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1")
