"""MySQL durable owner and revision state for multi-replica production mode."""

from __future__ import annotations

import logging
import threading
from typing import Any

from mysql.connector import Error

from app.domain.models.index_state import IndexStateRecord
from app.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_index_states (
    collection_pair VARCHAR(255) NOT NULL,
    owner_id VARCHAR(255) NOT NULL,
    owner_expires_at DOUBLE NOT NULL,
    index_version BIGINT NOT NULL DEFAULT 0,
    index_updated_at DOUBLE NOT NULL,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_pair),
    KEY idx_bcsfuse_index_states_owner (owner_id),
    KEY idx_bcsfuse_index_states_version (index_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class MySQLIndexStateStore:
    """Coordinate one collection-pair owner through a shared MySQL table."""

    def __init__(self, connection_pool: MySQLConnectionPoolProvider):
        self._pool = connection_pool
        self._schema_initialized = False
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if self._schema_initialized:
            return
        with self._schema_lock:
            if self._schema_initialized:
                return
            connection = self._pool.get_connection()
            cursor = connection.cursor()
            try:
                cursor.execute(_CREATE_TABLE)
                connection.commit()
                self._schema_initialized = True
            finally:
                cursor.close()
                connection.close()

    def claim_owner(
        self, collection_pair: str, owner_id: str, *, lease_seconds: float
    ) -> IndexStateRecord:
        self._validate_call(collection_pair, owner_id)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        connection = self._pool.get_connection()
        try:
            now = self._server_now(connection)
            expires_at = now + lease_seconds
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO bcsfuse_index_states
                        (collection_pair, owner_id, owner_expires_at,
                         index_version, index_updated_at)
                    VALUES (%s, %s, %s, 0, %s)
                    ON DUPLICATE KEY UPDATE
                        owner_id = IF(
                            owner_expires_at <= %s OR owner_id = VALUES(owner_id),
                            VALUES(owner_id), owner_id
                        ),
                        owner_expires_at = IF(
                            owner_expires_at <= %s OR owner_id = VALUES(owner_id),
                            VALUES(owner_expires_at), owner_expires_at
                        )
                    """,
                    (
                        collection_pair, owner_id, expires_at, now,
                        now, now,
                    ),
                )
                cursor.execute(
                    """
                    SELECT collection_pair, owner_id, owner_expires_at,
                           index_version, index_updated_at
                    FROM bcsfuse_index_states
                    WHERE collection_pair = %s
                    """,
                    (collection_pair,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("index owner claim did not persist")
                connection.commit()
                return self._record(now, row)
            except Error as error:
                connection.rollback()
                raise RuntimeError(f"MySQL index state claim failed: {error}") from error
            finally:
                cursor.close()
        finally:
            connection.close()

    def read_owner(self, collection_pair: str) -> IndexStateRecord:
        if not collection_pair:
            raise ValueError("collection_pair is required")
        connection = self._pool.get_connection()
        try:
            now = self._server_now(connection)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT collection_pair, owner_id, owner_expires_at,
                           index_version, index_updated_at
                    FROM bcsfuse_index_states
                    WHERE collection_pair = %s
                    """,
                    (collection_pair,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise FileNotFoundError("index state row does not exist")
                return self._record(now, row)
            finally:
                cursor.close()
        finally:
            connection.close()

    def publish_index_version(
        self, collection_pair: str, owner_id: str, index_version: int
    ) -> IndexStateRecord | None:
        self._validate_call(collection_pair, owner_id)
        if index_version < 0:
            raise ValueError("index_version must not be negative")
        connection = self._pool.get_connection()
        try:
            now = self._server_now(connection)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE bcsfuse_index_states
                    SET index_version = %s, index_updated_at = %s
                    WHERE collection_pair = %s
                      AND owner_id = %s
                      AND owner_expires_at > %s
                    """,
                    (index_version, now, collection_pair, owner_id, now),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return None
                cursor.execute(
                    """
                    SELECT collection_pair, owner_id, owner_expires_at,
                           index_version, index_updated_at
                    FROM bcsfuse_index_states
                    WHERE collection_pair = %s
                    """,
                    (collection_pair,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.rollback()
                    raise RuntimeError("MySQL index version publish lost its row")
                connection.commit()
                return self._record(now, row)
            except Error as error:
                connection.rollback()
                raise RuntimeError(f"MySQL index version publish failed: {error}") from error
            finally:
                cursor.close()
        finally:
            connection.close()

    def release_owner(self, collection_pair: str, owner_id: str) -> None:
        self._validate_call(collection_pair, owner_id)
        connection = self._pool.get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE bcsfuse_index_states
                SET owner_expires_at = COALESCE(UNIX_TIMESTAMP(NOW(6)), 0) - 0.001
                WHERE collection_pair = %s AND owner_id = %s
                """,
                (collection_pair, owner_id),
            )
            connection.commit()
        except Error as error:
            connection.rollback()
            raise RuntimeError(f"MySQL index owner release failed: {error}") from error
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _validate_call(collection_pair: str, owner_id: str) -> None:
        if not collection_pair:
            raise ValueError("collection_pair is required")
        if not owner_id:
            raise ValueError("owner_id is required")

    @staticmethod
    def _server_now(connection: Any) -> float:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT COALESCE(UNIX_TIMESTAMP(NOW(6)), 0)")
            row = cursor.fetchone()
            value = float(row[0]) if row is not None and row[0] is not None else 0.0
            if value <= 0:
                raise RuntimeError("MySQL did not provide a valid clock timestamp")
            return value
        finally:
            cursor.close()

    @staticmethod
    def _record(now: float, row: tuple) -> IndexStateRecord:
        return IndexStateRecord(
            collection_pair=str(row[0]),
            owner_id=str(row[1]),
            owner_expires_at=float(row[2]),
            index_version=int(row[3]),
            index_updated_at=float(row[4]),
            observed_at=now,
        )


__all__ = ["MySQLIndexStateStore"]
