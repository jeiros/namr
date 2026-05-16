from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_token (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    athlete_id INTEGER,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    scope TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_activity (
    activity_id INTEGER PRIMARY KEY,
    processed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,         -- renamed | skipped_manual | skipped_filter | error | dry_run
    detail TEXT
);

CREATE TABLE IF NOT EXISTS title_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    original_title TEXT NOT NULL,
    new_title TEXT NOT NULL,
    sport_type TEXT,
    written_at TEXT NOT NULL,
    model TEXT,
    latency_ms INTEGER,
    UNIQUE(activity_id, new_title)
);
CREATE INDEX IF NOT EXISTS idx_title_log_written_at ON title_log(written_at);

CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT PRIMARY KEY,          -- YYYY-MM-DD UTC
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_token(
    db_path: Path,
    *,
    athlete_id: Optional[int],
    access_token: str,
    refresh_token: str,
    expires_at: int,
    scope: Optional[str],
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO oauth_token (id, athlete_id, access_token, refresh_token, expires_at, scope, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                athlete_id=excluded.athlete_id,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at,
                scope=excluded.scope,
                updated_at=excluded.updated_at
            """,
            (athlete_id, access_token, refresh_token, expires_at, scope, _now_iso()),
        )


def load_token(db_path: Path) -> Optional[sqlite3.Row]:
    with connect(db_path) as conn:
        cur = conn.execute("SELECT * FROM oauth_token WHERE id = 1")
        return cur.fetchone()


def is_processed(db_path: Path, activity_id: int) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            "SELECT 1 FROM processed_activity WHERE activity_id = ?", (activity_id,)
        )
        return cur.fetchone() is not None


def mark_processed(
    db_path: Path, activity_id: int, outcome: str, detail: Optional[str] = None
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO processed_activity (activity_id, processed_at, outcome, detail)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(activity_id) DO UPDATE SET
                processed_at=excluded.processed_at,
                outcome=excluded.outcome,
                detail=excluded.detail
            """,
            (activity_id, _now_iso(), outcome, detail),
        )


def log_rename(
    db_path: Path,
    *,
    activity_id: int,
    original_title: str,
    new_title: str,
    sport_type: Optional[str],
    model: Optional[str],
    latency_ms: Optional[int],
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO title_log
            (activity_id, original_title, new_title, sport_type, written_at, model, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (activity_id, original_title, new_title, sport_type, _now_iso(), model, latency_ms),
        )


def recent_titles(db_path: Path, limit: int = 30) -> list[str]:
    with connect(db_path) as conn:
        cur = conn.execute(
            "SELECT new_title FROM title_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [r["new_title"] for r in cur.fetchall()]


def get_today_usage(db_path: Path) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with connect(db_path) as conn:
        cur = conn.execute("SELECT calls FROM llm_usage WHERE day = ?", (day,))
        row = cur.fetchone()
        return int(row["calls"]) if row else 0


def bump_usage(db_path: Path, *, input_tokens: int, output_tokens: int) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (day, calls, input_tokens, output_tokens)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                calls = calls + 1,
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens
            """,
            (day, input_tokens, output_tokens),
        )


def rename_log_in_range(
    db_path: Path, since_iso: str, until_iso: str
) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM title_log
            WHERE written_at >= ? AND written_at <= ?
            ORDER BY id ASC
            """,
            (since_iso, until_iso),
        )
        return cur.fetchall()
