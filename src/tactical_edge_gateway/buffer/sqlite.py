"""Persistent SQLite storage buffer operating in WAL mode for DIL edge resilience."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path

from tactical_edge_gateway.buffer.models import BufferedMessage, BufferState, BufferStats

logger = logging.getLogger(__name__)


class SqliteBuffer:
    """Thread-safe, persistent SQLite buffer configured for high-concurrency WAL mode."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Initializes the SQLite buffer.

        Args:
            db_path: Path to the SQLite file or ':memory:' for transient storage.
        """
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Configures database connection, WAL pragmas, and schema."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode; transactions managed explicitly
        )
        conn.row_factory = sqlite3.Row

        # Optimize for high write throughput and crash resilience in DIL environments
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA temp_store = MEMORY;")

        table_sql = (
            "CREATE TABLE IF NOT EXISTS telemetry_buffer ("
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "    uid TEXT NOT NULL,"
            "    cot_type TEXT NOT NULL,"
            "    timestamp REAL NOT NULL,"
            "    stale_timestamp REAL NOT NULL,"
            "    payload BLOB NOT NULL,"
            "    state TEXT NOT NULL DEFAULT 'PENDING',"
            "    retry_count INTEGER NOT NULL DEFAULT 0,"
            "    created_at REAL NOT NULL,"
            "    synced_at REAL"
            ");"
        )
        with conn:
            conn.execute(table_sql)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_buffer_state_id "
                "ON telemetry_buffer(state, id ASC);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_buffer_stale "
                "ON telemetry_buffer(stale_timestamp);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_buffer_created "
                "ON telemetry_buffer(created_at ASC);"
            )

        self._conn = conn
        logger.info("SqliteBuffer initialized at %s", self.db_path)

    async def put(
        self,
        payload: bytes,
        uid: str,
        cot_type: str,
        timestamp: float,
        stale_timestamp: float,
    ) -> int:
        """Persists a telemetry payload into the buffer in PENDING state."""
        created_at = time.time()

        def _insert() -> int:
            assert self._conn is not None
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT INTO telemetry_buffer
                        (uid, cot_type, timestamp, stale_timestamp, payload, state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        uid,
                        cot_type,
                        timestamp,
                        stale_timestamp,
                        payload,
                        BufferState.PENDING.value,
                        created_at,
                    ),
                )
                return int(cursor.lastrowid or 0)

        async with self._lock:
            return await asyncio.to_thread(_insert)

    async def put_batch(
        self,
        items: list[tuple[bytes, str, str, float, float]],
    ) -> list[int]:
        """Persists a batch of telemetry payloads in a single transaction."""
        if not items:
            return []

        created_at = time.time()

        def _insert_batch() -> list[int]:
            assert self._conn is not None
            ids: list[int] = []
            with self._conn:
                for payload, uid, cot_type, ts, stale_ts in items:
                    cursor = self._conn.execute(
                        """
                        INSERT INTO telemetry_buffer
                            (uid, cot_type, timestamp, stale_timestamp, payload, state, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            uid,
                            cot_type,
                            ts,
                            stale_ts,
                            payload,
                            BufferState.PENDING.value,
                            created_at,
                        ),
                    )
                    ids.append(int(cursor.lastrowid or 0))
            return ids

        async with self._lock:
            return await asyncio.to_thread(_insert_batch)

    async def fetch_pending(self, limit: int = 50) -> list[BufferedMessage]:
        """Retrieves oldest pending messages in chronological order."""

        def _fetch() -> list[BufferedMessage]:
            assert self._conn is not None
            cursor = self._conn.execute(
                """
                SELECT id, uid, cot_type, timestamp, stale_timestamp, payload,
                       state, retry_count, created_at, synced_at
                FROM telemetry_buffer
                WHERE state = ?
                ORDER BY id ASC
                LIMIT ?;
                """,
                (BufferState.PENDING.value, limit),
            )
            rows = cursor.fetchall()
            messages: list[BufferedMessage] = []
            for row in rows:
                messages.append(
                    BufferedMessage(
                        id=row["id"],
                        uid=row["uid"],
                        cot_type=row["cot_type"],
                        timestamp=row["timestamp"],
                        stale_timestamp=row["stale_timestamp"],
                        payload=row["payload"],
                        state=BufferState(row["state"]),
                        retry_count=row["retry_count"],
                        created_at=row["created_at"],
                        synced_at=row["synced_at"],
                    )
                )
            return messages

        async with self._lock:
            return await asyncio.to_thread(_fetch)

    async def mark_transmitting(self, ids: list[int]) -> int:
        """Marks a set of record IDs as TRANSMITTING."""
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)

        def _update() -> int:
            assert self._conn is not None
            with self._conn:
                cursor = self._conn.execute(
                    f"UPDATE telemetry_buffer SET state = ? "
                    f"WHERE id IN ({placeholders});",  # nosec B608
                    [BufferState.TRANSMITTING.value, *ids],
                )
                return cursor.rowcount

        async with self._lock:
            return await asyncio.to_thread(_update)

    async def mark_synced(self, ids: list[int]) -> int:
        """Marks a set of record IDs as SYNCED with timestamp."""
        if not ids:
            return 0

        now = time.time()
        placeholders = ",".join("?" for _ in ids)

        def _update() -> int:
            assert self._conn is not None
            with self._conn:
                cursor = self._conn.execute(
                    f"UPDATE telemetry_buffer SET state = ?, synced_at = ? "
                    f"WHERE id IN ({placeholders});",  # nosec B608
                    [BufferState.SYNCED.value, now, *ids],
                )
                return cursor.rowcount

        async with self._lock:
            return await asyncio.to_thread(_update)

    async def mark_failed(self, ids: list[int], max_retries: int = 5) -> int:
        """Reverts transmitting records to PENDING, or FAILED if max_retries exceeded."""
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)

        def _update() -> int:
            assert self._conn is not None
            with self._conn:
                # Increment retry count
                self._conn.execute(
                    f"UPDATE telemetry_buffer SET retry_count = retry_count + 1 "
                    f"WHERE id IN ({placeholders});",  # nosec B608
                    ids,
                )
                # If retry_count >= max_retries, set to FAILED; else revert to PENDING
                cursor = self._conn.execute(
                    f"""
                    UPDATE telemetry_buffer
                    SET state = CASE
                        WHEN retry_count >= ? THEN ?
                        ELSE ?
                    END
                    WHERE id IN ({placeholders});
                    """,  # nosec B608
                    [max_retries, BufferState.FAILED.value, BufferState.PENDING.value, *ids],
                )
                return cursor.rowcount

        async with self._lock:
            return await asyncio.to_thread(_update)

    async def prune_stale(self, now: float | None = None) -> int:
        """Marks pending records older than their CoT stale threshold as EXPIRED."""
        current_time = time.time() if now is None else now

        def _prune() -> int:
            assert self._conn is not None
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE telemetry_buffer
                    SET state = ?
                    WHERE state = ? AND stale_timestamp <= ?;
                    """,
                    (BufferState.EXPIRED.value, BufferState.PENDING.value, current_time),
                )
                return cursor.rowcount

        async with self._lock:
            return await asyncio.to_thread(_prune)

    async def prune_fifo(self, max_records: int) -> int:
        """Deletes oldest records to ensure total database rows do not exceed max_records."""
        if max_records <= 0:
            return 0

        def _prune() -> int:
            assert self._conn is not None
            with self._conn:
                cursor = self._conn.execute("SELECT COUNT(*) FROM telemetry_buffer;")
                total = cursor.fetchone()[0]
                if total <= max_records:
                    return 0

                excess = total - max_records
                delete_cursor = self._conn.execute(
                    """
                    DELETE FROM telemetry_buffer
                    WHERE id IN (
                        SELECT id FROM telemetry_buffer
                        ORDER BY id ASC
                        LIMIT ?
                    );
                    """,
                    (excess,),
                )
                return delete_cursor.rowcount

        async with self._lock:
            return await asyncio.to_thread(_prune)

    async def get_stats(self) -> BufferStats:
        """Calculates current buffer metrics and file size."""

        def _stats() -> BufferStats:
            assert self._conn is not None
            query = (
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN state = 'PENDING' THEN 1 ELSE 0 END) as pending, "
                "SUM(CASE WHEN state = 'SYNCED' THEN 1 ELSE 0 END) as synced, "
                "SUM(CASE WHEN state = 'FAILED' THEN 1 ELSE 0 END) as failed, "
                "SUM(CASE WHEN state = 'EXPIRED' THEN 1 ELSE 0 END) as expired "
                "FROM telemetry_buffer;"
            )
            cursor = self._conn.execute(query)
            row = cursor.fetchone()
            total = row["total"] or 0
            pending = row["pending"] or 0
            synced = row["synced"] or 0
            failed = row["failed"] or 0
            expired = row["expired"] or 0

            size_bytes = 0
            if self.db_path != ":memory:" and os.path.exists(self.db_path):
                size_bytes = os.path.getsize(self.db_path)
                wal_path = f"{self.db_path}-wal"
                if os.path.exists(wal_path):
                    size_bytes += os.path.getsize(wal_path)

            return BufferStats(
                total_records=total,
                pending_records=pending,
                synced_records=synced,
                failed_records=failed,
                expired_records=expired,
                db_size_bytes=size_bytes,
            )

        async with self._lock:
            return await asyncio.to_thread(_stats)

    async def clear(self) -> None:
        """Deletes all records from the telemetry buffer."""

        def _clear() -> None:
            assert self._conn is not None
            with self._conn:
                self._conn.execute("DELETE FROM telemetry_buffer;")

        async with self._lock:
            await asyncio.to_thread(_clear)

    async def close(self) -> None:
        """Closes the underlying SQLite database connection."""
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                logger.info("SqliteBuffer connection closed for %s", self.db_path)
