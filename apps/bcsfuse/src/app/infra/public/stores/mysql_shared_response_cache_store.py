"""MySQL-backed cross-pod L2 response cache."""

from __future__ import annotations

import logging
import threading
from typing import Any

from mysql.connector import Error

from app.infra.public.database.mysql_connection_pool import (
    MySQLConnectionPoolProvider,
)

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_response_cache_l2 (
    cache_key VARCHAR(128) NOT NULL,
    payload LONGBLOB NOT NULL,
    index_version BIGINT NOT NULL,
    expires_at DOUBLE NOT NULL,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cache_key),
    KEY idx_bcsfuse_response_cache_l2_expiry (expires_at),
    KEY idx_bcsfuse_response_cache_l2_version (index_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_SELECT = """
SELECT payload
FROM bcsfuse_response_cache_l2
WHERE cache_key = %s
  AND index_version = %s
  AND expires_at > %s
"""

_UPSERT = """
INSERT INTO bcsfuse_response_cache_l2
    (cache_key, payload, index_version, expires_at)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    payload = VALUES(payload),
    index_version = VALUES(index_version),
    expires_at = VALUES(expires_at)
"""


class MySQLSharedResponseCacheStore:
    """Share final route projections across BCSFuse pods through MySQL."""

    def __init__(
        self,
        *,
        connection_pool: MySQLConnectionPoolProvider,
        max_payload_bytes: int = 1_000_000,
    ) -> None:
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self._pool = connection_pool
        self._max_payload_bytes = max_payload_bytes
        self._schema_initialized = False
        self._schema_lock = threading.Lock()

    def get(self, key: str, *, index_version: int) -> bytes | None:
        self._validate_key(key)
        self.ensure_schema()
        self._validate_index_version(index_version)
        connection = self._pool.get_connection()
        try:
            now = self._server_now(connection)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    _SELECT,
                    (key, index_version, now),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                payload = row[0]
                return payload if isinstance(payload, bytes) else None
            finally:
                cursor.close()
        finally:
            connection.close()

    def store(
        self,
        key: str,
        payload: bytes,
        *,
        index_version: int,
        ttl_seconds: float,
    ) -> bool:
        self._validate_key(key)
        self._validate_index_version(index_version)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not isinstance(payload, bytes):
            raise ValueError("payload must be bytes")
        if len(payload) > self._max_payload_bytes:
            raise ValueError("payload exceeds max_payload_bytes")
        self.ensure_schema()

        connection = self._pool.get_connection()
        try:
            now = self._server_now(connection)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    _UPSERT,
                    (key, payload, index_version, now + ttl_seconds),
                )
                connection.commit()
                return cursor.rowcount > 0
            except Error as error:
                connection.rollback()
                raise RuntimeError(
                    f"MySQL response cache store failed: {type(error).__name__}"
                ) from error
            finally:
                cursor.close()
        finally:
            connection.close()

    def ensure_schema(self) -> None:
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
            except Error as error:
                connection.rollback()
                raise RuntimeError(
                    f"MySQL response cache schema initialization failed: {type(error).__name__}"
                ) from error
            finally:
                cursor.close()
                connection.close()

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key:
            raise ValueError("cache_key is required")
        if len(key) > 128:
            raise ValueError("cache_key exceeds 128 characters")

    @staticmethod
    def _validate_index_version(index_version: int) -> None:
        if index_version < 0:
            raise ValueError("index_version must not be negative")

    @staticmethod
    def _server_now(connection: Any) -> float:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT COALESCE(UNIX_TIMESTAMP(NOW(6)), 0)")
            row = cursor.fetchone()
            return float(row[0]) if row is not None else 0.0
        finally:
            cursor.close()


__all__ = ["MySQLSharedResponseCacheStore"]
