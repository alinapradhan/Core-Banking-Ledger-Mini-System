from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import settings


def _normalize_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "", 1)
    return database_url


def get_connection(database_url: str | None = None) -> sqlite3.Connection:
    path = _normalize_path(database_url or settings.database_url)
    connection = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection):
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
