"""SQLite persistence for EshanAI: conversations, messages, memories, events.

Design notes
- WAL journal for reliable concurrent access from UI + worker threads.
- conversations carry a `title` and `title_generated` (0 = provisional default).
- messages: role in {user, assistant, tool, event}; tool rows carry meta JSON.
- memories: short facts; source in {user, auto}; hits/last_used for ranking.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

from . import paths

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT 'New conversation',
  title_generated INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  model TEXT,
  meta TEXT,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_txt  ON messages(content COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL DEFAULT 'user',
  created REAL NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  last_used REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,          -- 'fallback','error','info'
  detail TEXT,
  created REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    """Thread-local connection is expensive; keep one shared conn + lock (writes are tiny)."""
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        path: Path = paths.db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _conn = conn
        return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created by older builds."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "model" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN model TEXT")
    if "meta" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
    ccols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "title_generated" not in ccols:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN title_generated INTEGER NOT NULL DEFAULT 0")
    mcols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
    if "source" not in mcols:
        conn.execute("ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")
    if "hits" not in mcols:
        conn.execute("ALTER TABLE memories ADD COLUMN hits INTEGER NOT NULL DEFAULT 0")
    if "last_used" not in mcols:
        conn.execute("ALTER TABLE memories ADD COLUMN last_used REAL NOT NULL DEFAULT 0")


def _now() -> float:
    return time.time()


# ---------------- conversations ----------------

def create_conversation(title: str = "New conversation") -> int:
    conn = connect()
    with _lock:
        t = _now()
        cur = conn.execute(
            "INSERT INTO conversations(title, title_generated, created, updated) VALUES(?,0,?,?)",
            (title, t, t),
        )
        conn.commit()
        return int(cur.lastrowid)


def rename_conversation(conv_id: int, title: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE conversations SET title=?, title_generated=0 WHERE id=?", (title.strip() or "Untitled", conv_id)
        )
        conn.commit()


def set_title(conv_id: int, title: str, generated: bool = True) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE conversations SET title=?, title_generated=? WHERE id=?", (title, 1 if generated else 0, conv_id)
        )
        conn.commit()


def delete_conversation(conv_id: int) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        conn.commit()


def get_conversation(conv_id: int) -> dict | None:
    conn = connect()
    row = conn.execute(
        "SELECT id,title,title_generated,created,updated FROM conversations WHERE id=?", (conv_id,)
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "title_generated": row[2], "created": row[3], "updated": row[4]}


def list_conversations(limit: int = 500) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT id,title,title_generated,created,updated FROM conversations ORDER BY updated DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"id": r[0], "title": r[1], "title_generated": r[2], "created": r[3], "updated": r[4]} for r in rows]


def touch_conversation(conv_id: int) -> None:
    conn = connect()
    with _lock:
        conn.execute("UPDATE conversations SET updated=? WHERE id=?", (_now(), conv_id))
        conn.commit()


# ---------------- messages ----------------

EVENT = "event"      # status lines: fallback notices etc.
TOOL = "tool"


def add_message(conv_id: int, role: str, content: str, model: str | None = None,
                meta: dict | None = None, created: float | None = None) -> int:
    conn = connect()
    with _lock:
        cur = conn.execute(
            "INSERT INTO messages(conv_id, role, content, model, meta, created) VALUES(?,?,?,?,?,?)",
            (conv_id, role, content, model, json.dumps(meta, ensure_ascii=False) if meta else None,
             created if created is not None else _now()),
        )
        conn.execute("UPDATE conversations SET updated=? WHERE id=?", (_now(), conv_id))
        conn.commit()
        return int(cur.lastrowid)


def set_message_meta(msg_id: int, meta: dict | None) -> None:
    conn = connect()
    with _lock:
        conn.execute("UPDATE messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False) if meta else None, msg_id))
        conn.commit()


def update_message_content(msg_id: int, content: str) -> None:
    conn = connect()
    with _lock:
        conn.execute("UPDATE messages SET content=? WHERE id=?", (content, msg_id))
        conn.commit()


def get_messages(conv_id: int, limit: int = 2000) -> list[dict]:
    """Oldest→newest list of message dicts."""
    conn = connect()
    rows = conn.execute(
        "SELECT id,role,content,model,meta,created FROM messages WHERE conv_id=? ORDER BY id LIMIT ?",
        (conv_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        meta = {}
        if r[4]:
            try:
                meta = json.loads(r[4])
            except Exception:
                meta = {}
        out.append({"id": r[0], "conv_id": conv_id, "role": r[1], "content": r[2],
                    "model": r[3], "meta": meta, "created": r[5]})
    return out


def last_user_turns(conv_id: int) -> list[dict]:
    msgs = get_messages(conv_id)
    return msgs


def search_messages(query: str, limit: int = 30) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT m.id, m.conv_id, m.role, m.content, m.created, c.title FROM messages m "
        "JOIN conversations c ON c.id=m.conv_id WHERE m.content LIKE ? "
        "ORDER BY m.id DESC LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    return [{"id": r[0], "conv_id": r[1], "role": r[2], "content": r[3], "created": r[4], "conversation": r[5]}
            for r in rows]


# ---------------- memories ----------------

def add_memory(content: str, source: str = "user") -> int | None:
    content = content.strip()
    if not content:
        return None
    conn = connect()
    with _lock:
        row = conn.execute("SELECT id FROM memories WHERE content=? COLLATE NOCASE", (content,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO memories(content, source, created, last_used) VALUES(?,?,?,?)",
            (content, source, _now(), _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_memories(limit: int = 500) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT id,content,source,created,hits,last_used FROM memories ORDER BY last_used DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"id": r[0], "content": r[1], "source": r[2], "created": r[3], "hits": r[4], "last_used": r[5]}
            for r in rows]


def delete_memory(mem_id: int) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        conn.commit()


def update_memory(mem_id: int, content: str) -> None:
    conn = connect()
    with _lock:
        conn.execute("UPDATE memories SET content=? WHERE id=?", (content.strip(), mem_id))
        conn.commit()


def mark_memory_used(ids: Iterable[int]) -> None:
    ids = list(ids)
    if not ids:
        return
    conn = connect()
    q = ",".join("?" * len(ids))
    with _lock:
        conn.execute(f"UPDATE memories SET hits=hits+1, last_used=? WHERE id IN ({q})", (_now(), *ids))
        conn.commit()


def find_memories(substr: str, limit: int = 20) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT id,content,source,created FROM memories WHERE content LIKE ? ORDER BY last_used DESC LIMIT ?",
        (f"%{substr}%", limit),
    ).fetchall()
    return [{"id": r[0], "content": r[1], "source": r[2], "created": r[3]} for r in rows]


# ---------------- events ----------------

def add_event(kind: str, detail: str) -> None:
    conn = connect()
    with _lock:
        conn.execute("INSERT INTO events(kind, detail, created) VALUES(?,?,?)", (kind, detail, _now()))
        conn.commit()


# ---------------- search conversations by title ----------------

def search_conversations(query: str) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT id,title,title_generated,created,updated FROM conversations WHERE title LIKE ? "
        "ORDER BY updated DESC LIMIT 50",
        (f"%{query}%",),
    ).fetchall()
    return [{"id": r[0], "title": r[1], "title_generated": r[2], "created": r[3], "updated": r[4]} for r in rows]
