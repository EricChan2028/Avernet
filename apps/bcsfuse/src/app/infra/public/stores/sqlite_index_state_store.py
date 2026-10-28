"""SQLite durable owner and revision state for development and smoke modes."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from app.domain.models.index_state import IndexStateRecord

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_index_states (
    collection_pair TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_expires_at REAL NOT NULL,
    index_version INTEGER NOT NULL DEFAULT 0,
    index_updated_at REAL NOT NULL
)
"""


class SQLiteIndexStateStore:
    """Coordinate one owner per collection pair in one SQLite database file."""

    def __init__(self, path: str):
        if not path:
            raise ValueError("path is required")
        self.db_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._execute(lambda cursor: cursor.executescript(_CREATE_TABLE))

    def claim_owner(
        self, collection_pair: str, owner_id: str, *, lease_seconds: float
    ) -> IndexStateRecord:
        self._validate_identity(collection_pair, owner_id)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        now = time.time()
        expires_at = now + lease_seconds
        result = self._transaction(
            lambda cursor: self._claim(
                cursor,
                collection_pair=collection_pair,
                owner_id=owner_id,
                now=now,
                expires_at=expires_at,
            )
        )
        return result

    def read_owner(self, collection_pair: str) -> IndexStateRecord:
        if not collection_pair:
            raise ValueError("collection_pair is required")
        now = time.time()
        record = self._execute(
            lambda cursor: self._read(cursor, collection_pair, now)
        )
        if record is None:
            raise FileNotFoundError("index state row does not exist")
        return record

    def publish_index_version(
        self, collection_pair: str, owner_id: str, index_version: int
    ) -> IndexStateRecord | None:
        self._validate_identity(collection_pair, owner_id)
        if index_version < 0:
            raise ValueError("index_version must not be negative")
        now = time.time()
        record = self._transaction(
            lambda cursor: self._publish(cursor, collection_pair, owner_id, index_version, now)
        )
        if record is None:
            logger.warning(
                "[INDEX-STATE] rejected stale owner=%s for collection_pair=%s",
                owner_id, collection_pair,
            )
        return record

    def release_owner(self, collection_pair: str, owner_id: str) -> None:
        self._validate_identity(collection_pair, owner_id)
        self._execute(
            lambda cursor: cursor.execute(
                "UPDATE bcsfuse_index_states SET owner_expires_at = ? "
                "WHERE collection_pair = ? AND owner_id = ?",
                (time.time() - 0.001, collection_pair, owner_id),
            )
        )

    @staticmethod
    def _validate_identity(collection_pair: str, owner_id: str) -> None:
        if not collection_pair:
            raise ValueError("collection_pair is required")
        if not owner_id:
            raise ValueError("owner_id is required")

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None, check_same_thread=True
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _execute(self, operation):
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                return operation(cursor)
            finally:
                cursor.close()

    def _transaction(self, operation):
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                result = operation(cursor)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def _claim(
        self, cursor, *, collection_pair: str, owner_id: str,
        now: float, expires_at: float,
    ):
        cursor.execute(
            """
            SELECT collection_pair, owner_id, owner_expires_at, index_version,
                   index_updated_at
            FROM bcsfuse_index_states
            WHERE collection_pair = ?
            """,
            (collection_pair,),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                """
                INSERT INTO bcsfuse_index_states
                    (collection_pair, owner_id, owner_expires_at,
                     index_version, index_updated_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (collection_pair, owner_id, expires_at, now),
            )
            return self._record(now, row=(collection_pair, owner_id, expires_at, 0, now))
        if row[2] <= now:
            cursor.execute(
                """
                UPDATE bcsfuse_index_states
                SET owner_id = ?, owner_expires_at = ?
                WHERE collection_pair = ?
                """,
                (owner_id, expires_at, collection_pair),
            )
            return self._record(
                now, row=(collection_pair, owner_id, expires_at, row[3], row[4])
            )
        if row[1] == owner_id:
            cursor.execute(
                "UPDATE bcsfuse_index_states SET owner_expires_at = ? WHERE collection_pair = ?",
                (expires_at, collection_pair),
            )
            return self._record(
                now, row=(collection_pair, owner_id, expires_at, row[3], row[4])
            )
        return self._record(now, row=row)

    def _publish(
        self, cursor, collection_pair: str, owner_id: str,
        index_version: int, now: float,
    ):
        cursor.execute(
            "SELECT collection_pair, owner_id, owner_expires_at, index_version, "
            "index_updated_at FROM bcsfuse_index_states WHERE collection_pair = ?",
            (collection_pair,),
        )
        row = cursor.fetchone()
        if row is None or row[1] != owner_id or row[2] <= now:
            return None
        if int(row[3]) >= index_version:
            return None
        cursor.execute(
            """
            UPDATE bcsfuse_index_states
            SET index_version = ?, index_updated_at = ?
            WHERE collection_pair = ? AND owner_id = ?
            """,
            (index_version, now, collection_pair, owner_id),
        )
        return self._record(now, row=(row[0], row[1], row[2], index_version, now))

    def _read(self, cursor, collection_pair: str, now: float):
        cursor.execute(
            "SELECT collection_pair, owner_id, owner_expires_at, index_version, "
            "index_updated_at FROM bcsfuse_index_states WHERE collection_pair = ?",
            (collection_pair,),
        )
        row = cursor.fetchone()
        return None if row is None else self._record(now, row=row)

    @staticmethod
    def _record(now: float, *, row):
        if row is None:
            raise ValueError("index state row is required")
        return IndexStateRecord(
            collection_pair=row[0],
            owner_id=row[1],
            owner_expires_at=float(row[2]),
            index_version=int(row[3]),
            index_updated_at=float(row[4]),
            observed_at=now,
        )


__all__ = ["SQLiteIndexStateStore"]
