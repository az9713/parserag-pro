"""
SQLite-backed session store for multi-turn conversation memory.
Sessions expire after a configurable TTL via purge_expired().
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
import uuid

DB_PATH = str(Path(__file__).parent.parent / "sessions.db")

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Open a connection with WAL mode for concurrent access."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    messages TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


_init_db()


def _to_serializable(obj):
    """
    Recursively convert SDK/Pydantic objects to plain dicts/lists
    so the result is JSON-serialisable.
    Handles Pydantic v2 (model_dump), v1 (dict), and __dict__ fallback.
    Also walks plain list/dict containers.
    """
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return _to_serializable(obj.model_dump())
    # Pydantic v1
    if hasattr(obj, "dict") and callable(obj.dict):
        return _to_serializable(obj.dict())
    # Generic object with __dict__
    if hasattr(obj, "__dict__"):
        return _to_serializable(obj.__dict__)
    return obj


def new_session() -> str:
    """Create a new session row and return its UUID."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (id, messages, updated_at) VALUES (?, ?, ?)",
                (session_id, "[]", now),
            )
            conn.commit()
        finally:
            conn.close()
    return session_id


def load_messages(session_id: str) -> list[dict]:
    """Return message history for a session, or [] if not found."""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT messages FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return []
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return []


def save_messages(session_id: str, messages: list) -> None:
    """
    Serialise messages to plain dicts and upsert into the session store.
    Uses ON CONFLICT (id) DO UPDATE so it works whether the row exists or not.
    """
    serialisable = _to_serializable(messages)
    payload = json.dumps(serialisable)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO sessions (id, messages, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    messages = excluded.messages,
                    updated_at = excluded.updated_at
                """,
                (session_id, payload, now),
            )
            conn.commit()
        finally:
            conn.close()


def purge_expired(ttl_minutes: int = 30) -> int:
    """
    Delete sessions not updated within ttl_minutes.
    Returns the number of rows deleted.
    """
    from datetime import timedelta
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)
    ).isoformat()
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
