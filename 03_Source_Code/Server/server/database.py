"""SQLite persistence layer.

All SQL uses parameter binding.  The database stores:
* sessions and their authoritative board JSON,
* participant identities for secure reconnects across server restarts,
* version snapshots,
* chat and activity history.

SQLite is configured with WAL mode so the server can write autosaves while
reads (version list, history) remain responsive.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    salt TEXT NOT NULL DEFAULT '',
    host_id TEXT NOT NULL,
    host_name TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    seq INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    presentation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_code ON sessions(code);

CREATE TABLE IF NOT EXISTS participants (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(session_id, name),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_participants_session ON participants(session_id);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'manual',
    state_json TEXT NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_activity_session ON activity(session_id, timestamp DESC);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def save_session(self, record: dict[str, Any]) -> None:
        state_json = record.get("state_json", "{}")
        if not isinstance(state_json, str):
            state_json = json.dumps(state_json, ensure_ascii=False, separators=(",", ":"))
        presentation_json = record.get("presentation_json", "{}")
        if not isinstance(presentation_json, str):
            presentation_json = json.dumps(presentation_json, ensure_ascii=False, separators=(",", ":"))
        values = (
            record["id"], record["code"], record["name"], record.get("password_hash", ""),
            record.get("salt", ""), record["host_id"], record["host_name"], state_json,
            int(record.get("seq", 0)), int(bool(record.get("locked", False))),
            presentation_json, record["created_at"], record["updated_at"],
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (id, code, name, password_hash, salt, host_id,
                    host_name, state_json, seq, locked, presentation_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    code=excluded.code, name=excluded.name,
                    password_hash=excluded.password_hash, salt=excluded.salt,
                    host_id=excluded.host_id, host_name=excluded.host_name,
                    state_json=excluded.state_json, seq=excluded.seq,
                    locked=excluded.locked, presentation_json=excluded.presentation_json,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            self._conn.commit()

    def get_session_by_code(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE code=?", (code.upper(),)).fetchone()
        return dict(row) if row else None

    def get_session_by_id(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._conn.commit()

    def count_sessions(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Participants / reconnect identities
    # ------------------------------------------------------------------
    def upsert_participant(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO participants (id, session_id, name, color, role, token_hash, created_at, last_seen)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, color=excluded.color, role=excluded.role,
                    token_hash=excluded.token_hash, last_seen=excluded.last_seen
                """,
                (
                    row["id"], row["session_id"], row["name"], row.get("color", "#6366f1"),
                    row.get("role", "editor"), row["token_hash"], row["created_at"],
                    row["last_seen"],
                ),
            )
            self._conn.commit()

    def update_participant(self, participant_id: str, **fields: Any) -> None:
        allowed = {"name", "color", "role", "token_hash", "last_seen"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE participants SET {assignments} WHERE id=?",
                (*updates.values(), participant_id),
            )
            self._conn.commit()

    def list_participants(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM participants WHERE session_id=? ORDER BY created_at", (session_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_participant_by_name(self, session_id: str, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM participants WHERE session_id=? AND name=?",
                (session_id, name),
            ).fetchone()
        return dict(row) if row else None

    def get_participant_by_token(self, session_id: str, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM participants WHERE session_id=? AND token_hash=?",
                (session_id, token_hash),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------
    def save_snapshot(self, row: dict[str, Any]) -> None:
        state_json = row["state_json"] if isinstance(row["state_json"], str) else json.dumps(
            row["state_json"], ensure_ascii=False, separators=(",", ":")
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO snapshots (id, session_id, name, description, kind, state_json,
                    seq, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["id"], row["session_id"], row["name"], row.get("description", ""),
                    row.get("kind", "manual"), state_json, int(row.get("seq", 0)),
                    row["created_at"], row.get("created_by", ""),
                ),
            )
            self._conn.commit()
            self._prune_snapshots(row["session_id"])

    def _prune_snapshots(self, session_id: str, keep: int = 100) -> None:
        self._conn.execute(
            """
            DELETE FROM snapshots WHERE id IN (
                SELECT id FROM snapshots WHERE session_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?
            )
            """,
            (session_id, keep),
        )

    def list_snapshots(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, name, description, kind, seq, created_at, created_by,
                          length(state_json) AS state_bytes
                   FROM snapshots WHERE session_id=? ORDER BY created_at DESC LIMIT ?""",
                (session_id, min(limit, 500)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Chat / activity
    # ------------------------------------------------------------------
    def add_chat_message(self, session_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO chat_messages (id, session_id, sender_id, sender, text, timestamp)"
                " VALUES (?,?,?,?,?,?)",
                (
                    message["id"], session_id, message["sender_id"], message["sender"],
                    message["text"], message["timestamp"],
                ),
            )
            self._conn.execute(
                """DELETE FROM chat_messages WHERE session_id=? AND id NOT IN (
                       SELECT id FROM chat_messages WHERE session_id=? ORDER BY timestamp DESC LIMIT 250
                   )""",
                (session_id, session_id),
            )
            self._conn.commit()

    def list_chat_messages(self, session_id: str, limit: int = 150) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_messages WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
                (session_id, min(limit, 250)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_activity(self, session_id: str, activity: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO activity (id, session_id, kind, actor_id, actor, detail, timestamp)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    activity["id"], session_id, activity["kind"], activity.get("actor_id", ""),
                    activity.get("actor", ""), activity.get("detail", ""), activity["timestamp"],
                ),
            )
            self._conn.execute(
                """DELETE FROM activity WHERE session_id=? AND id NOT IN (
                       SELECT id FROM activity WHERE session_id=? ORDER BY timestamp DESC LIMIT 400
                   )""",
                (session_id, session_id),
            )
            self._conn.commit()

    def list_activity(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM activity WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
                (session_id, min(limit, 400)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            sessions = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
            snapshots = self._conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
            chats = self._conn.execute("SELECT COUNT(*) AS n FROM chat_messages").fetchone()["n"]
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return {
            "sessions": int(sessions),
            "snapshots": int(snapshots),
            "chat_messages": int(chats),
            "db_bytes": size,
        }

    def checkpoint(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass


class PersistenceBatcher:
    """Simple debounce helper for board autosave."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self.last_saved = 0.0
        self.dirty = False

    def mark(self) -> None:
        self.dirty = True

    def should_save(self, now: float | None = None) -> bool:
        if not self.dirty:
            return False
        current = now if now is not None else time.monotonic()
        return current - self.last_saved >= self.interval

    def saved(self, now: float | None = None) -> None:
        self.dirty = False
        self.last_saved = now if now is not None else time.monotonic()


def load_json(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
