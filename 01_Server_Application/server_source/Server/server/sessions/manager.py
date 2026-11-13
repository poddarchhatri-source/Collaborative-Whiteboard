"""Session creation, secure join/resume, persistence and version snapshots."""

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from shared.constants import (
    ERR_NAME_TAKEN,
    ERR_ROOM_FULL,
    ERR_SESSION_NOT_FOUND,
    ERR_WRONG_PASSWORD,
    MAX_SESSIONS,
    ROLE_EDITOR,
    ROLE_HOST,
    ROLE_VIEWER,
    USER_COLORS,
)
from shared.models import (
    ValidationFailure,
    generation_code,
    new_object_id,
    validate_password,
    validate_role,
    validate_session_code,
    validate_session_name,
    validate_username,
)
from shared.protocol import utc_now_iso
from server.config import ServerConfig
from server.database import Database, PersistenceBatcher, load_json
from server.models import AuthResult, Participant, Session, new_participant
from server.security import hash_password, hash_token, make_salt, random_token, verify_password

logger = logging.getLogger("syncanvas.server.sessions")


class SessionError(Exception):
    def __init__(self, message: str, code: str = "BAD_REQUEST") -> None:
        super().__init__(message)
        self.code = code


class SessionManager:
    def __init__(self, db: Database, config: ServerConfig) -> None:
        self.db = db
        self.config = config
        self.sessions: dict[str, Session] = {}
        self.by_code: dict[str, Session] = {}
        self.created_at = time.time()
        self._save_timer = PersistenceBatcher(interval=config.autosave_seconds)
        self._load_sessions()

    # ------------------------------------------------------------------
    # Loading and persistence
    # ------------------------------------------------------------------
    def _load_sessions(self) -> None:
        for record in self.db.list_sessions():
            try:
                session = self._session_from_record(record)
            except Exception:
                logger.exception("Could not load session %s", record.get("id"))
                continue
            self.sessions[session.id] = session
            self.by_code[session.code] = session
        logger.info("Loaded %d persisted session(s)", len(self.sessions))

    def _session_from_record(self, record: dict[str, Any]) -> Session:
        payload = load_json(record.get("state_json", "{}"), {})
        board = __import__("server.state.board", fromlist=["BoardState"]).BoardState()
        board.load(payload)
        presentation = load_json(record.get("presentation_json", "{}"), {})
        session = Session(
            id=record["id"],
            code=record["code"],
            name=record["name"],
            password_hash=record.get("password_hash", ""),
            salt=record.get("salt", ""),
            host_id=record["host_id"],
            host_name=record["host_name"],
            board=board,
            seq=int(record.get("seq", 0)),
            locked=bool(record.get("locked", False)),
            presentation=presentation if isinstance(presentation, dict) else {},
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )
        for row in self.db.list_participants(session.id):
            participant = Participant(
                id=row["id"],
                name=row["name"],
                color=row["color"],
                role=row["role"],
                token_hash=row["token_hash"],
                online=False,
                joined_at=row["created_at"],
                last_seen=row["last_seen"],
            )
            session.participants[participant.id] = participant
        for chat in self.db.list_chat_messages(session.id):
            session.chat_history.append(chat)
        for activity in self.db.list_activity(session.id):
            session.activity.append(activity)
        # Ensure the host identity exists even if an older database did not
        # contain a participants row.
        if session.host_id not in session.participants:
            host = Participant(
                id=session.host_id,
                name=session.host_name,
                color=USER_COLORS[0],
                role=ROLE_HOST,
                token_hash="",
                online=False,
                joined_at=session.created_at,
                last_seen=session.created_at,
            )
            session.participants[host.id] = host
        return session

    def _record_from_session(self, session: Session) -> dict[str, Any]:
        return {
            "id": session.id,
            "code": session.code,
            "name": session.name,
            "password_hash": session.password_hash,
            "salt": session.salt,
            "host_id": session.host_id,
            "host_name": session.host_name,
            "state_json": json.dumps(session.board.snapshot(), ensure_ascii=False, separators=(",", ":")),
            "seq": session.seq,
            "locked": session.locked,
            "presentation_json": json.dumps(session.presentation, ensure_ascii=False, separators=(",", ":")),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def save_session(self, session: Session) -> None:
        session.updated_at = utc_now_iso()
        self.db.save_session(self._record_from_session(session))
        session.dirty = False
        session.last_saved_at = time.monotonic()

    def save_all(self, force: bool = False) -> int:
        saved = 0
        for session in self.sessions.values():
            if force or session.dirty:
                self.save_session(session)
                saved += 1
        return saved

    def tick(self, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        for session in self.sessions.values():
            if session.dirty and current - session.last_saved_at >= self.config.autosave_seconds:
                try:
                    self.save_session(session)
                except Exception:
                    logger.exception("Autosave failed for %s", session.code)
            interval = self.config.snapshot_interval_seconds
            if (
                interval > 0
                and session.board.objects
                and current - session.last_snapshot_at >= interval
            ):
                try:
                    self.create_snapshot(
                        session,
                        name=f"Auto snapshot {time.strftime('%H:%M')}",
                        description=f"{len(session.board.objects)} objects",
                        kind="auto",
                        actor_id="system",
                    )
                    session.last_snapshot_at = current
                except Exception:
                    logger.exception("Automatic snapshot failed for %s", session.code)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------
    def get_by_code(self, code: str) -> Session | None:
        return self.by_code.get(code.upper())

    def get_by_id(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def active_sessions(self) -> list[dict[str, Any]]:
        return [session.summary() for session in self.sessions.values()]

    def total_online(self) -> int:
        return sum(len(session.online_participants()) for session in self.sessions.values())

    def total_objects(self) -> int:
        return sum(session.board.object_count() for session in self.sessions.values())

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def _unique_code(self) -> str:
        for _ in range(100):
            code = generation_code(6)
            if code not in self.by_code:
                return code
        raise SessionError("Could not allocate a unique room code", "SERVER_BUSY")

    def _choose_color(self, session: Session) -> str:
        used = {p.color for p in session.participants.values()}
        for color in USER_COLORS:
            if color not in used:
                return color
        return USER_COLORS[len(session.participants) % len(USER_COLORS)]

    def create_session(
        self,
        *,
        session_name: Any,
        username: Any,
        password: Any = "",
        motd: str | None = None,
    ) -> AuthResult:
        if len(self.sessions) >= min(self.config.max_sessions, MAX_SESSIONS):
            raise SessionError("Server session limit reached", "SERVER_BUSY")
        name = validate_session_name(session_name)
        user = validate_username(username)
        pwd = validate_password(password)
        if self.config.require_password and not pwd:
            raise ValidationFailure("This server requires a password for new sessions")
        code = self._unique_code()
        session_id = new_object_id()
        host_id = new_object_id()
        token = random_token()
        now = utc_now_iso()
        salt = make_salt()
        password_hash = hash_password(pwd, salt)
        session = Session(
            id=session_id,
            code=code,
            name=name,
            password_hash=password_hash,
            salt=salt,
            host_id=host_id,
            host_name=user,
            created_at=now,
            updated_at=now,
        )
        host = Participant(
            id=host_id,
            name=user,
            color=USER_COLORS[0],
            role=ROLE_HOST,
            token_hash=hash_token(token),
            online=True,
            joined_at=now,
            last_seen=now,
        )
        session.participants[host.id] = host
        self.sessions[session.id] = session
        self.by_code[session.code] = session
        self.save_session(session)
        self.db.upsert_participant(
            {
                "id": host.id,
                "session_id": session.id,
                "name": host.name,
                "color": host.color,
                "role": host.role,
                "token_hash": host.token_hash,
                "created_at": host.joined_at,
                "last_seen": host.last_seen,
            }
        )
        logger.info("Session %s created by %s (%d total)", session.code, user, len(self.sessions))
        return AuthResult(session=session, participant=host, token=token)

    def join_session(
        self,
        *,
        code: Any,
        username: Any,
        password: Any = "",
        role: Any = ROLE_EDITOR,
    ) -> AuthResult:
        normalized = validate_session_code(code)
        session = self.get_by_code(normalized)
        if session is None:
            raise SessionError("No session with that room code", ERR_SESSION_NOT_FOUND)
        pwd = validate_password(password)
        if not verify_password(pwd, session.salt, session.password_hash):
            raise SessionError("Incorrect session password", ERR_WRONG_PASSWORD)
        if not session.can_accept():
            raise SessionError("Session is full", ERR_ROOM_FULL)
        user = validate_username(username)
        target_role = validate_role(role, allow_host=False)
        if target_role not in (ROLE_EDITOR, ROLE_VIEWER):
            target_role = ROLE_EDITOR
        for participant in session.participants.values():
            if participant.name.lower() == user.lower():
                raise SessionError(
                    "That username is already registered in this session",
                    ERR_NAME_TAKEN,
                )
        token = random_token()
        now = utc_now_iso()
        participant = new_participant(
            user,
            self._choose_color(session),
            target_role,
            hash_token(token),
            participant_id=new_object_id(),
        )
        participant.joined_at = now
        participant.last_seen = now
        participant.online = True
        session.participants[participant.id] = participant
        self.db.upsert_participant(
            {
                "id": participant.id,
                "session_id": session.id,
                "name": participant.name,
                "color": participant.color,
                "role": participant.role,
                "token_hash": participant.token_hash,
                "created_at": participant.joined_at,
                "last_seen": participant.last_seen,
            }
        )
        session.dirty = True
        self.save_session(session)
        logger.info("%s joined %s as %s", user, session.code, target_role)
        return AuthResult(session=session, participant=participant, token=token)

    def resume_session(self, *, code: Any, token: Any) -> AuthResult:
        normalized = validate_session_code(code)
        session = self.get_by_code(normalized)
        if session is None:
            raise SessionError("Session no longer exists", ERR_SESSION_NOT_FOUND)
        if not isinstance(token, str) or not token:
            raise SessionError("Reconnect token is required", "AUTH_REQUIRED")
        hashed = hash_token(token)
        participant = next(
            (p for p in session.participants.values() if p.token_hash == hashed), None
        )
        if participant is None:
            raise SessionError("Reconnect token is invalid or expired", "AUTH_REQUIRED")
        replaced = session.connections.get(participant.id)
        participant.online = True
        participant.last_seen = utc_now_iso()
        session.dirty = True
        logger.info("%s resumed session %s", participant.name, session.code)
        return AuthResult(
            session=session, participant=participant, token=token, replaced_connection=replaced
        )

    def mark_left(self, session: Session, participant: Participant) -> None:
        participant.online = False
        participant.connection = None
        participant.cursor = None
        participant.typing = False
        participant.last_seen = utc_now_iso()
        session.connections.pop(participant.id, None)
        session.dirty = True
        self.db.update_participant(participant.id, last_seen=participant.last_seen)
        logger.info("%s left session %s", participant.name, session.code)

    def remove_participant(self, session: Session, participant: Participant) -> None:
        self.mark_left(session, participant)

    def set_role(self, session: Session, participant: Participant, role: str) -> None:
        participant.role = role
        session.dirty = True
        self.db.update_participant(participant.id, role=role)
        self.save_session(session)

    def update_presence(
        self,
        session: Session,
        participant: Participant,
        *,
        cursor: dict[str, float] | None = None,
        tool: str | None = None,
        typing: bool | None = None,
    ) -> None:
        if cursor is not None:
            participant.cursor = cursor
        if tool is not None and isinstance(tool, str) and len(tool) <= 32:
            participant.tool = tool
        if typing is not None:
            participant.typing = typing
        participant.last_seen = utc_now_iso()
        session.dirty = True

    # ------------------------------------------------------------------
    # Version history
    # ------------------------------------------------------------------
    def create_snapshot(
        self,
        session: Session,
        *,
        name: str,
        description: str,
        kind: str = "manual",
        actor_id: str = "",
    ) -> dict[str, Any]:
        state = session.board.snapshot()
        snapshot = {
            "id": new_object_id(),
            "session_id": session.id,
            "name": (name or "Snapshot")[:120],
            "description": (description or "")[:500],
            "kind": kind,
            "state_json": json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            "seq": session.seq,
            "created_at": utc_now_iso(),
            "created_by": actor_id,
        }
        self.db.save_snapshot(snapshot)
        logger.info("Snapshot '%s' saved for %s", snapshot["name"], session.code)
        return snapshot

    def list_snapshots(self, session: Session) -> list[dict[str, Any]]:
        return self.db.list_snapshots(session.id)

    def restore_snapshot(self, session: Session, snapshot_id: str, actor_id: str) -> dict[str, Any]:
        snapshot = self.db.get_snapshot(snapshot_id)
        if snapshot is None or snapshot["session_id"] != session.id:
            raise SessionError("Snapshot not found", "NOT_FOUND")
        # Protect the current version before restoring anything.
        self.create_snapshot(
            session,
            name="Before restore",
            description=f"Safety copy captured before restoring {snapshot['name']}",
            kind="safety",
            actor_id=actor_id,
        )
        state = load_json(snapshot["state_json"], {})
        session.board = __import__("server.state.board", fromlist=["BoardState"]).BoardState()
        session.board.load(state)
        # Undo entries referring to the old lineage could be destructive.
        session.board.undo_stacks.clear()
        session.board.redo_stacks.clear()
        session.dirty = True
        self.save_session(session)
        return snapshot

    def delete_session(self, session: Session) -> None:
        self.db.delete_session(session.id)
        self.sessions.pop(session.id, None)
        self.by_code.pop(session.code, None)

    def shutdown(self) -> None:
        self.save_all(force=True)
        self.db.checkpoint()
