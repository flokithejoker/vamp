from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any

MAX_SQLITE_VARIABLES = 900


def _default_db_path() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "database" / "feedback.sqlite3"


def _resolve_db_path() -> Path:
    configured_path = os.getenv("FEEDBACK_DB_PATH", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return _default_db_path()


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    _ensure_schema(connection)
    return connection


@contextmanager
def _managed_connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
    finally:
        connection.close()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS call_feedback (
            call_id TEXT PRIMARY KEY,
            rating INTEGER CHECK (rating BETWEEN 1 AND 5),
            comment TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            rating_updated_at TEXT,
            comment_updated_at TEXT
        )
        """
    )
    connection.commit()


def _feedback_row_to_record(row: sqlite3.Row | None, *, call_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "callId": call_id,
            "rating": None,
            "comment": None,
            "createdAt": None,
            "updatedAt": None,
            "ratingUpdatedAt": None,
            "commentUpdatedAt": None,
        }

    return {
        "callId": row["call_id"],
        "rating": row["rating"],
        "comment": row["comment"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "ratingUpdatedAt": row["rating_updated_at"],
        "commentUpdatedAt": row["comment_updated_at"],
    }


def submit_call_rating(*, call_id: str, rating: int) -> dict[str, Any]:
    normalized_call_id = call_id.strip()
    now_iso = _utc_now_iso()

    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO call_feedback (
                call_id,
                rating,
                comment,
                created_at,
                updated_at,
                rating_updated_at,
                comment_updated_at
            )
            VALUES (?, ?, NULL, ?, ?, ?, NULL)
            ON CONFLICT(call_id) DO UPDATE SET
                rating = excluded.rating,
                updated_at = excluded.updated_at,
                rating_updated_at = excluded.rating_updated_at
            """,
            (normalized_call_id, int(rating), now_iso, now_iso, now_iso),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT
                call_id,
                rating,
                comment,
                created_at,
                updated_at,
                rating_updated_at,
                comment_updated_at
            FROM call_feedback
            WHERE call_id = ?
            """,
            (normalized_call_id,),
        ).fetchone()

    return _feedback_row_to_record(row, call_id=normalized_call_id)


def submit_call_feedback(*, call_id: str, comment: str) -> dict[str, Any]:
    normalized_call_id = call_id.strip()
    normalized_comment = comment.strip()
    now_iso = _utc_now_iso()

    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO call_feedback (
                call_id,
                rating,
                comment,
                created_at,
                updated_at,
                rating_updated_at,
                comment_updated_at
            )
            VALUES (?, NULL, ?, ?, ?, NULL, ?)
            ON CONFLICT(call_id) DO UPDATE SET
                comment = excluded.comment,
                updated_at = excluded.updated_at,
                comment_updated_at = excluded.comment_updated_at
            """,
            (normalized_call_id, normalized_comment, now_iso, now_iso, now_iso),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT
                call_id,
                rating,
                comment,
                created_at,
                updated_at,
                rating_updated_at,
                comment_updated_at
            FROM call_feedback
            WHERE call_id = ?
            """,
            (normalized_call_id,),
        ).fetchone()

    return _feedback_row_to_record(row, call_id=normalized_call_id)


def list_call_feedback(*, limit: int, offset: int) -> list[dict[str, Any]]:
    with _managed_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                call_id,
                rating,
                comment,
                created_at,
                updated_at,
                rating_updated_at,
                comment_updated_at
            FROM call_feedback
            ORDER BY updated_at DESC, call_id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return [_feedback_row_to_record(row, call_id=row["call_id"]) for row in rows]


def get_call_feedback(*, call_id: str) -> dict[str, Any]:
    normalized_call_id = call_id.strip()
    with _managed_connection() as connection:
        row = connection.execute(
            """
            SELECT
                call_id,
                rating,
                comment,
                created_at,
                updated_at,
                rating_updated_at,
                comment_updated_at
            FROM call_feedback
            WHERE call_id = ?
            """,
            (normalized_call_id,),
        ).fetchone()

    return _feedback_row_to_record(row, call_id=normalized_call_id)


def _chunk_values(values: list[str], chunk_size: int) -> list[list[str]]:
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def get_ratings_for_call_ids(call_ids: list[str]) -> dict[str, int]:
    unique_ids = list(dict.fromkeys(call_id.strip() for call_id in call_ids if call_id and call_id.strip()))
    if not unique_ids:
        return {}

    ratings: dict[str, int] = {}
    with _managed_connection() as connection:
        for chunk in _chunk_values(unique_ids, MAX_SQLITE_VARIABLES):
            placeholders = ",".join("?" for _ in chunk)
            query = (
                "SELECT call_id, rating "
                f"FROM call_feedback WHERE call_id IN ({placeholders}) AND rating IS NOT NULL"
            )
            rows = connection.execute(query, chunk).fetchall()
            for row in rows:
                raw_call_id = row["call_id"]
                raw_rating = row["rating"]
                if not isinstance(raw_call_id, str):
                    continue
                if isinstance(raw_rating, bool):
                    continue
                if isinstance(raw_rating, int):
                    ratings[raw_call_id] = raw_rating
                elif isinstance(raw_rating, float) and raw_rating.is_integer():
                    ratings[raw_call_id] = int(raw_rating)

    return ratings
