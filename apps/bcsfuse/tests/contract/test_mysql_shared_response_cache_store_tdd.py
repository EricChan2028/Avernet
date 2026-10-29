"""TDD contract for the MySQL cross-pod response-cache backend."""

from __future__ import annotations

from typing import Any

import pytest


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount = 1

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        self.executed.append((sql.strip(), tuple(args)))
        if sql.strip().startswith("SELECT COALESCE(UNIX_TIMESTAMP"):
            self.rows = [(123.456,)]
        elif sql.strip().startswith("SELECT payload"):
            self.rows = [(b'{"cached":true}', 423.456)]

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        self.connection.closed_cursors += 1


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_object = FakeCursor(self)
        self.commit_count = 0
        self.rollback_count = 0
        self.closed_cursors = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_object

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        pass

    def get_connection(self) -> FakeConnection:
        return self.connection


def test_mysql_store_creates_versioned_bounded_payload_table() -> None:
    from app.infra.public.stores.mysql_shared_response_cache_store import (
        MySQLSharedResponseCacheStore,
    )

    pool = FakePool()
    MySQLSharedResponseCacheStore(connection_pool=pool).ensure_schema()

    create_sql, args = pool.connection.cursor_object.executed[0]
    assert "CREATE TABLE IF NOT EXISTS bcsfuse_response_cache_l2" in create_sql
    assert args == ()
    assert "cache_key VARCHAR(128) NOT NULL" in create_sql
    assert "payload LONGBLOB NOT NULL" in create_sql
    assert "index_version BIGINT NOT NULL" in create_sql
    assert "expires_at DOUBLE NOT NULL" in create_sql
    assert "PRIMARY KEY (cache_key)" in create_sql


def test_mysql_store_persists_l2_payload_with_ttl_and_index_version() -> None:
    from app.infra.public.stores.mysql_shared_response_cache_store import (
        MySQLSharedResponseCacheStore,
    )

    pool = FakePool()
    store = MySQLSharedResponseCacheStore(connection_pool=pool)
    stored = store.store(
        "key-1",
        b'{"cached":true}',
        index_version=7,
        ttl_seconds=30.0,
    )

    sql_statements = pool.connection.cursor_object.executed
    now_sql, now_args = next(
        item for item in sql_statements if item[0].startswith("SELECT COALESCE")
    )
    assert now_args == ()
    upsert_sql, upsert_args = next(
        item
        for item in sql_statements
        if "INSERT INTO bcsfuse_response_cache_l2" in item[0]
    )
    assert "INSERT INTO bcsfuse_response_cache_l2" in upsert_sql
    assert "ON DUPLICATE KEY UPDATE" in upsert_sql
    assert upsert_args[:3] == ("key-1", b'{"cached":true}', 7)
    assert upsert_args[3] == pytest.approx(153.456)
    assert stored is True
    assert pool.connection.commit_count == 2
    assert pool.connection.rollback_count == 0
    assert pool.connection.closed_cursors == 3


def test_mysql_store_rejects_oversized_payload() -> None:
    from app.infra.public.stores.mysql_shared_response_cache_store import (
        MySQLSharedResponseCacheStore,
    )

    store = MySQLSharedResponseCacheStore(
        connection_pool=FakePool(), max_payload_bytes=8
    )
    try:
        store.store("key", b"123456789", index_version=7, ttl_seconds=30.0)
    except ValueError as error:
        assert str(error) == "payload exceeds max_payload_bytes"
    else:
        raise AssertionError("oversized payload must fail")


def test_mysql_store_rejects_invalid_cache_parameters() -> None:
    from app.infra.public.stores.mysql_shared_response_cache_store import (
        MySQLSharedResponseCacheStore,
    )

    store = MySQLSharedResponseCacheStore(connection_pool=FakePool())
    try:
        store.store("", b"payload", index_version=7, ttl_seconds=30.0)
    except ValueError as error:
        assert str(error) == "cache_key is required"
    else:
        raise AssertionError("empty cache_key must fail")

    try:
        store.store("key", b"payload", index_version=-1, ttl_seconds=30.0)
    except ValueError as error:
        assert str(error) == "index_version must not be negative"
    else:
        raise AssertionError("negative index_version must fail")

    try:
        store.store("key", b"payload", index_version=7, ttl_seconds=0)
    except ValueError as error:
        assert str(error) == "ttl_seconds must be positive"
    else:
        raise AssertionError("non-positive ttl_seconds must fail")


def test_mysql_store_returns_only_current_version_and_unexpired_payload() -> None:
    from app.infra.public.stores.mysql_shared_response_cache_store import (
        MySQLSharedResponseCacheStore,
    )

    pool = FakePool()
    store = MySQLSharedResponseCacheStore(connection_pool=pool)
    payload = store.get("key-1", index_version=7)
    select_sql, args = pool.connection.cursor_object.executed[-1]
    assert "cache_key = %s" in select_sql
    assert "index_version = %s" in select_sql
    assert "expires_at >" in select_sql
    assert args == ("key-1", 7, 123.456)
    assert payload == b'{"cached":true}'
