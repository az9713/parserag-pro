"""
SQLite-backed co-citation graph.
Each time a set of document filenames are cited together in an answer,
their pairwise co-citation weights are incremented.
"""
import itertools
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "graph.db")

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    weight INTEGER DEFAULT 1,
                    last_seen TEXT,
                    PRIMARY KEY (source, target)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


_init_db()


def record_co_citations(docs: list[str]) -> None:
    """
    Given a list of document filenames cited in one answer, increment
    the co-citation weight for every unique pair.
    Pairs are stored in canonical (min, max) order.
    """
    # Deduplicate while preserving some order
    seen = set()
    unique_docs = []
    for d in docs:
        if d and d not in seen:
            seen.add(d)
            unique_docs.append(d)

    if len(unique_docs) < 2:
        return

    now = datetime.now(timezone.utc).isoformat()
    pairs = [
        (min(a, b), max(a, b))
        for a, b in itertools.combinations(unique_docs, 2)
    ]

    with _lock:
        conn = _get_conn()
        try:
            conn.executemany(
                """
                INSERT INTO edges (source, target, weight, last_seen)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(source, target) DO UPDATE SET
                    weight = weight + 1,
                    last_seen = excluded.last_seen
                """,
                [(src, tgt, now) for src, tgt in pairs],
            )
            conn.commit()
        finally:
            conn.close()


def get_graph() -> dict:
    """
    Return the full co-citation graph as:
    {"nodes": [{"id": "..."}], "edges": [{"source": "...", "target": "...", "weight": N}]}
    """
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT source, target, weight FROM edges ORDER BY weight DESC"
            ).fetchall()
        finally:
            conn.close()

    node_ids = set()
    edges = []
    for source, target, weight in rows:
        node_ids.add(source)
        node_ids.add(target)
        edges.append({"source": source, "target": target, "weight": weight})

    nodes = [{"id": nid} for nid in sorted(node_ids)]
    return {"nodes": nodes, "edges": edges}
