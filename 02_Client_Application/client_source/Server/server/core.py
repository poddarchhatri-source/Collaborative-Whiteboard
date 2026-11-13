"""Protocol dispatcher: the authoritative brain of the SynCanvas server.

The transport layer calls :meth:`ServerCore.dispatch` with a decoded message.
All permission checks, validation, sequencing, broadcasting and persistence
decisions happen here.  Keeping this layer independent from asyncio's socket
objects makes it directly unit-testable with fake connections.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from shared.constants import (
    ACK,
    ACTIVITY,
    BOARD_IMPORT,
    CANVAS_STATE,
    CHAT,
    CHAT_MESSAGE,
    CLEAR_CANVAS,
    CREATE_OBJECT,
    CREATE_SESSION,
    CURSOR,
    CURSOR_UPDATE,
    DELETE_OBJECTS,
    ERROR,
    ERR_AUTH_REQUIRED,
    ERR_BAD_REQUEST,
    ERR_BOARD_LOCKED,
    ERR_FORBIDDEN,
    ERR_NOT_FOUND,
    ERR_REVISION_CONFLICT,
    ERR_SESSION_NOT_FOUND,
    ERR_TOO_LARGE,
    ERR_UNAUTHORIZED,
    ERR_UNDO_CONFLICT,
    EVENT,
    GET_SERVER_STATS,
    HELLO,
    JOIN_SESSION,
    KICK_USER,
    KICKED,
    LASER,
    LASER_POINT,
    LEAVE_SESSION,
    LEFT,
    MAX_CANVAS_STATE_BYTES,
    MAX_CLIENTS_PER_SESSION,
    MAX_MESSAGE_BYTES,
    PING,
    PONG,
    PRESENCE,
    PRESENTATION_START,
    PRESENTATION_STATE,
    PRESENTATION_STOP,
    PRESENTATION_UPDATE,
    PROTOCOL_VERSION,
    REDO,
    REQUEST_SYNC,
    RESUME_SESSION,
    ROLE_EDITOR,
    ROLE_HOST,
    ROLE_VIEWER,
    ROLES,
    SERVER_STATS,
    SET_BOARD_LOCK,
    SET_PROFILE,
    SET_ROLE,
    SNAPSHOT_CREATE,
    SNAPSHOT_GET,
    SNAPSHOT_LIST,
    SNAPSHOT_LIST_RESULT,
    SNAPSHOT_PREVIEW,
    SNAPSHOT_RESTORE,
    SNAPSHOT_SAVED,
    STROKE_APPEND,
    STROKE_END,
    SYNC_COMPLETE,
    SYSTEM,
    UNDO,
    UPDATE_OBJECT,
    VERSION_RESTORED,
    WELCOME,
    ACTION_BOARD_LOCKED,
    ACTION_CANVAS_CLEARED,
    ACTION_CANVAS_REPLACED,
    ACTION_CHAT,
    ACTION_OBJECT_CREATED,
    ACTION_OBJECTS_DELETED,
    ACTION_OBJECT_UPDATED,
    ACTION_PRESENTATION,
    ACTION_REDO,
    ACTION_STROKE_APPENDED,
    ACTION_UNDO,
    ACTION_USER_JOINED,
    ACTION_USER_LEFT,
    ACTION_VERSION_RESTORED,
)
from shared.events import activity_for_event, build_event, event_to_message, has_permission
from shared.models import (
    ValidationFailure,
    make_activity,
    make_chat_message,
    validate_session_code,
)
from shared.protocol import make_server_message, utc_now_iso
from server.config import ServerConfig
from server.database import Database, load_json
from server.models import AuthResult, Participant, Session
from server.monitoring.metrics import ServerMetrics
from server.sessions.manager import SessionError, SessionManager
from server.state.board import BoardState, StateError

logger = logging.getLogger("syncanvas.server.core")


class ConnectionLike:
    """Structural protocol implemented by the real asyncio connection."""

    id: str
    participant: Participant | None
    session: Session | None
    last_rx: float

    def send(self, message: dict[str, Any]) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self, reason: str = "") -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ServerCore:
    def __init__(self, config: ServerConfig, db: Database, metrics: ServerMetrics | None = None) -> None:
        self.config = config
        self.db = db
        self.metrics = metrics or ServerMetrics()
        self.sessions = SessionManager(db, config)
        self.started_at = time.time()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def on_connect(self, conn: ConnectionLike) -> None:
        self.metrics.connection_opened()
        self.metrics.event("connect", f"Client connected from {getattr(conn, 'peer', 'unknown')}")

    def on_disconnect(self, conn: ConnectionLike) -> None:
        self.metrics.connection_closed()
        session = getattr(conn, "session", None)
        participant = getattr(conn, "participant", None)
        if not session or not participant:
            return
        # A stale connection replaced by a successful resume must not mark the
        # participant offline.
        if session.connections.get(participant.id) is not conn:
            return
        self.sessions.mark_left(session, participant)
        if participant.role == ROLE_HOST and session.presentation.get("active"):
            session.presentation = {"active": False}
            self._broadcast(
                session,
                make_server_message(
                    PRESENTATION_STATE, {"active": False}, session_id=session.id, server_seq=session.seq
                ),
            )
            self._emit(
                session,
                action=ACTION_PRESENTATION,
                actor=participant,
                body={"active": False, "reason": "presenter_disconnected"},
            )
        self._broadcast_presence(session)
        self._emit(
            session,
            action=ACTION_USER_LEFT,
            actor=participant,
            body={"id": participant.id, "name": participant.name},
        )
        self.sessions.save_session(session)
        self.metrics.event("disconnect", f"{participant.name} disconnected")

    def tick(self) -> None:
        try:
            self.sessions.tick()
        except Exception:
            logger.exception("Session tick failed")

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------
    def _send(self, conn: ConnectionLike, message: dict[str, Any]) -> bool:
        try:
            return bool(conn.send(message))
        except Exception:
            logger.debug("Send failed", exc_info=True)
            return False

    def _broadcast(self, session: Session, message: dict[str, Any], *, exclude: str | None = None) -> int:
        sent = 0
        for participant in list(session.participants.values()):
            if not participant.online or participant.id == exclude:
                continue
            conn = participant.connection
            if conn is None:
                continue
            if self._send(conn, message):
                sent += 1
        return sent

    def _broadcast_presence(self, session: Session) -> None:
        self._broadcast(
            session,
            make_server_message(
                PRESENCE,
                {"participants": session.online_participants()},
                session_id=session.id,
                server_seq=session.seq,
            ),
        )

    def _ack(
        self,
        conn: ConnectionLike,
        reply_to: str,
        *,
        session: Session | None = None,
        event: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": "ok",
            "received_at": utc_now_iso(),
        }
        if event:
            payload["event_id"] = event["event_id"]
            payload["server_seq"] = event["server_seq"]
        if session is not None and getattr(conn, "participant", None) is not None:
            payload["can_undo"] = session.board.can_undo(conn.participant.id)
            payload["can_redo"] = session.board.can_redo(conn.participant.id)
        if extra:
            payload.update(extra)
        self._send(
            conn,
            make_server_message(
                ACK,
                payload,
                session_id=session.id if session else None,
                server_seq=session.seq if session else None,
                reply_to=reply_to,
                event_id=event["event_id"] if event else None,
            ),
        )

    def _error(
        self,
        conn: ConnectionLike,
        reply_to: str,
        code: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> None:
        self.metrics.incr("errors")
        self._send(
            conn,
            make_server_message(
                ERROR,
                {"code": code, "message": message, "details": data or {}},
                session_id=session.id if session else None,
                reply_to=reply_to,
            ),
        )

    def _emit(
        self,
        session: Session,
        *,
        action: str,
        actor: Participant,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        seq = session.next_seq()
        event = build_event(
            session_id=session.id,
            seq=seq,
            action=action,
            actor_id=actor.id,
            actor_name=actor.name,
            payload=body or {},
        )
        session.record_event(event)
        session.dirty = True
        session.updated_at = utc_now_iso()
        self.metrics.incr("board_events")
        self._broadcast(session, event_to_message(event))
        detail = activity_for_event(action, actor.name, body or {})
        if detail:
            self._log_activity(session, action, actor, detail)
        return event

    def _log_activity(
        self, session: Session, kind: str, actor: Participant | None, detail: str
    ) -> dict[str, Any]:
        entry = make_activity(
            kind,
            actor.id if actor else "system",
            actor.name if actor else "System",
            detail,
        )
        session.add_activity(entry)
        try:
            self.db.add_activity(session.id, entry)
        except Exception:
            logger.exception("Could not persist activity")
        self._broadcast(
            session,
            make_server_message(
                ACTIVITY,
                {"activity": entry},
                session_id=session.id,
            ),
        )
        return entry

    def _broadcast_chat(self, session: Session, message: dict[str, Any], seq: int) -> None:
        self._broadcast(
            session,
            make_server_message(
                CHAT_MESSAGE,
                {"message": message},
                session_id=session.id,
                server_seq=seq,
            ),
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _attach(self, conn: ConnectionLike, auth: AuthResult) -> None:
        session = auth.session
        participant = auth.participant
        old = session.connections.get(participant.id)
        if old is not None and old is not conn:
            try:
                old.close("Replaced by a newer reconnect")
            except Exception:
                pass
        conn.session = session
        conn.participant = participant
        participant.connection = conn
        participant.online = True
        session.connections[participant.id] = conn
        session.dirty = True

    def _welcome(
        self,
        conn: ConnectionLike,
        auth: AuthResult,
        *,
        resumed: bool,
        has_state: bool,
        last_seq: int | None,
        join_event: bool = True,
    ) -> None:
        session = auth.session
        participant = auth.participant
        gap_mode = bool(
            resumed
            and has_state
            and isinstance(last_seq, int)
            and session.event_gap_available(max(0, last_seq))
        )
        board_payload = None if gap_mode else session.board.snapshot()
        if not gap_mode and session.board.serialized_json_size() > MAX_CANVAS_STATE_BYTES:
            self._error(conn, "", ERR_TOO_LARGE, "Board is too large to synchronize")
            return
        welcome_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "server_name": "SynCanvas Server",
            "server_version": "1.0.0",
            "motd": self.config.motd,
            "session": session.summary(),
            "self": participant.public(),
            "token": auth.token,
            "participants": session.online_participants(),
            "board": board_payload,
            "seq": session.seq,
            "chat": list(session.chat_history)[-150:],
            "activity": list(session.activity)[-200:],
            "locked": session.locked,
            "presentation": session.presentation,
            "sync_mode": "gap" if gap_mode else "full",
            "resumed": resumed,
            "can_undo": session.board.can_undo(participant.id),
            "can_redo": session.board.can_redo(participant.id),
            "stats": self._stats_payload(session),
        }
        self._send(
            conn,
            make_server_message(
                WELCOME, welcome_payload, session_id=session.id, server_seq=session.seq
            ),
        )
        if gap_mode and last_seq is not None:
            for event in session.events_since(last_seq):
                self._send(conn, event_to_message(event))
            self._send(
                conn,
                make_server_message(
                    SYNC_COMPLETE,
                    {"seq": session.seq, "mode": "gap", "replayed": len(session.events_since(last_seq))},
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
        elif resumed:
            self._send(
                conn,
                make_server_message(
                    SYNC_COMPLETE,
                    {"seq": session.seq, "mode": "full"},
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
        if join_event:
            self._emit(
                session,
                action=ACTION_USER_JOINED,
                actor=participant,
                body={"id": participant.id, "name": participant.name, "role": participant.role},
            )
            self._broadcast_presence(session)
        if not resumed:
            self.sessions.save_session(session)

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------
    def dispatch(self, conn: ConnectionLike, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        msg_id = message.get("msg_id", "")
        payload = message.get("payload", {}) or {}

        # Duplicate message rejection (at-least-once retry safety).
        seen = getattr(conn, "seen_message_ids", None)
        if isinstance(seen, set):
            if msg_id in seen:
                self.metrics.incr("rejected_messages")
                self._ack(conn, msg_id, session=getattr(conn, "session", None))
                return
            seen.add(msg_id)
            if len(seen) > 2500:
                seen.clear()

        if msg_type == PING:
            self._send(
                conn,
                make_server_message(
                    PONG,
                    {"t": payload.get("t"), "server_time": time.time()},
                    server_seq=getattr(conn.session, "seq", None) if conn.session else None,
                ),
            )
            return
        if msg_type == PONG:
            conn.last_rx = time.monotonic()
            return
        if msg_type == HELLO:
            self._send(
                conn,
                make_server_message(
                    SYSTEM,
                    {
                        "motd": self.config.motd,
                        "protocol_version": PROTOCOL_VERSION,
                        "server_version": "1.0.0",
                        "requires_password": self.config.require_password,
                    },
                ),
            )
            return

        if conn.session is None or conn.participant is None:
            self._handle_auth(conn, msg_type, payload, msg_id)
            return

        session: Session = conn.session
        participant: Participant = conn.participant
        conn.last_rx = time.monotonic()

        if message.get("session_id") and message["session_id"] != session.id:
            self._error(conn, msg_id, ERR_UNAUTHORIZED, "Session mismatch", session=session)
            return
        if msg_type in (CREATE_SESSION, JOIN_SESSION, RESUME_SESSION):
            self._error(conn, msg_id, ERR_BAD_REQUEST, "Already authenticated", session=session)
            return

        try:
            self._dispatch_authenticated(conn, session, participant, msg_type, payload, msg_id)
        except ValidationFailure as exc:
            self._error(conn, msg_id, exc.code, str(exc), session=session)
        except StateError as exc:
            self._error(
                conn, msg_id, exc.code, str(exc), data=exc.data, session=session
            )
        except SessionError as exc:
            self._error(conn, msg_id, exc.code, str(exc), session=session)
        except Exception:
            logger.exception("Unhandled protocol error for %s", msg_type)
            self._error(conn, msg_id, "INTERNAL", "Server could not process the request", session=session)

    def _handle_auth(self, conn: ConnectionLike, msg_type: str, payload: dict[str, Any], msg_id: str) -> None:
        try:
            if msg_type == CREATE_SESSION:
                auth = self.sessions.create_session(
                    session_name=payload.get("session_name"),
                    username=payload.get("username"),
                    password=payload.get("password", ""),
                )
                self.metrics.incr("sessions_created")
                self._attach(conn, auth)
                self.sessions.save_session(auth.session)
                self._welcome(conn, auth, resumed=False, has_state=False, last_seq=0)
                self.metrics.event("session", f"Session {auth.session.code} created")
                self._ack(conn, msg_id, session=auth.session)
            elif msg_type == JOIN_SESSION:
                auth = self.sessions.join_session(
                    code=payload.get("code"),
                    username=payload.get("username"),
                    password=payload.get("password", ""),
                    role=payload.get("role", ROLE_EDITOR),
                )
                self._attach(conn, auth)
                self._welcome(
                    conn,
                    auth,
                    resumed=False,
                    has_state=False,
                    last_seq=0,
                )
                self._ack(conn, msg_id, session=auth.session)
            elif msg_type == RESUME_SESSION:
                code = validate_session_code(payload.get("code"))
                auth = self.sessions.resume_session(code=code, token=payload.get("token"))
                self.metrics.incr("reconnects")
                self._attach(conn, auth)
                last_seq = payload.get("last_seq")
                if not isinstance(last_seq, int):
                    last_seq = None
                self._welcome(
                    conn,
                    auth,
                    resumed=True,
                    has_state=bool(payload.get("has_state")),
                    last_seq=last_seq,
                    join_event=False,
                )
                self._ack(conn, msg_id, session=auth.session)
                self.metrics.event("reconnect", f"{auth.participant.name} reconnected")
            elif msg_type == PING:
                self._send(conn, make_server_message(PONG, {"t": payload.get("t"), "server_time": time.time()}))
            else:
                self._error(conn, msg_id, ERR_AUTH_REQUIRED, "Create or join a session first")
        except ValidationFailure as exc:
            self._error(conn, msg_id, exc.code, str(exc))
        except SessionError as exc:
            self._error(conn, msg_id, exc.code, str(exc))
        except Exception:
            logger.exception("Authentication failure")
            self._error(conn, msg_id, "INTERNAL", "Could not complete authentication")

    # ------------------------------------------------------------------
    # Authenticated message routing
    # ------------------------------------------------------------------
    def _dispatch_authenticated(
        self,
        conn: ConnectionLike,
        session: Session,
        participant: Participant,
        msg_type: str,
        payload: dict[str, Any],
        msg_id: str,
    ) -> None:
        if msg_type == CREATE_OBJECT:
            self._require_edit(session, participant)
            body, _ = session.board.create_object(
                payload.get("object"), actor_id=participant.id, actor_name=participant.name
            )
            event = self._emit(
                session,
                action=ACTION_OBJECT_CREATED,
                actor=participant,
                body={"object": body["object"]},
            )
            self._ack(conn, msg_id, session=session, event=event)
            return

        if msg_type == STROKE_APPEND:
            self._require_edit(session, participant)
            body = session.board.append_stroke(
                str(payload.get("object_id", "")), payload.get("points"), actor_id=participant.id
            )
            event = self._emit(
                session,
                action=ACTION_STROKE_APPENDED,
                actor=participant,
                body=body,
            )
            # Stroke batches do not need an ACK for every 50 ms packet; send a
            # compact ACK only for the final batch. This keeps overhead low.
            self._ack(conn, msg_id, session=session, event=event, extra={"quiet": True})
            return

        if msg_type == STROKE_END:
            self._require_edit(session, participant)
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == UPDATE_OBJECT:
            self._require_edit(session, participant)
            object_id = payload.get("object_id")
            if not isinstance(object_id, str):
                raise ValidationFailure("object_id is required")
            base_rev = payload.get("base_rev")
            try:
                base_rev_int = int(base_rev) if base_rev is not None else None
            except (TypeError, ValueError):
                raise ValidationFailure("base_rev must be an integer")
            body = session.board.update_object(
                object_id,
                payload.get("patch"),
                base_rev=base_rev_int,
                actor_id=participant.id,
                actor_name=participant.name,
            )
            event = self._emit(
                session,
                action=ACTION_OBJECT_UPDATED,
                actor=participant,
                body=body,
            )
            self._ack(conn, msg_id, session=session, event=event)
            return

        if msg_type == DELETE_OBJECTS:
            self._require_edit(session, participant)
            body = session.board.delete_objects(
                payload.get("ids"), actor_id=participant.id, actor_name=participant.name
            )
            event = self._emit(
                session,
                action=ACTION_OBJECTS_DELETED,
                actor=participant,
                body={"ids": body["ids"]},
            )
            self._ack(conn, msg_id, session=session, event=event)
            return

        if msg_type == UNDO:
            self._require_edit(session, participant)
            body = session.board.undo(participant.id)
            event = self._emit(session, action=ACTION_UNDO, actor=participant, body=body)
            self._ack(
                conn,
                msg_id,
                session=session,
                event=event,
                extra={
                    "can_undo": session.board.can_undo(participant.id),
                    "can_redo": session.board.can_redo(participant.id),
                },
            )
            return

        if msg_type == REDO:
            self._require_edit(session, participant)
            body = session.board.redo(participant.id)
            event = self._emit(session, action=ACTION_REDO, actor=participant, body=body)
            self._ack(
                conn,
                msg_id,
                session=session,
                event=event,
                extra={
                    "can_undo": session.board.can_undo(participant.id),
                    "can_redo": session.board.can_redo(participant.id),
                },
            )
            return

        if msg_type == CLEAR_CANVAS:
            if not has_permission(participant.role, "clear"):
                raise StateError("Only the host can clear the canvas", ERR_FORBIDDEN)
            self._require_edit(session, participant, allow_when_locked=True)
            self.sessions.create_snapshot(
                session,
                name="Before clear",
                description="Safety snapshot captured before clearing the board",
                kind="safety",
                actor_id=participant.id,
            )
            body = session.board.clear(actor_id=participant.id, actor_name=participant.name)
            event = self._emit(
                session, action=ACTION_CANVAS_CLEARED, actor=participant, body={"count": body["count"]}
            )
            self._ack(conn, msg_id, session=session, event=event)
            self.sessions.save_session(session)
            return

        if msg_type == CHAT:
            if not has_permission(participant.role, "chat"):
                raise StateError("You do not have permission to chat", ERR_FORBIDDEN)
            chat = make_chat_message(participant.id, participant.name, payload.get("text"))
            seq = session.next_seq()
            chat["seq"] = seq
            session.add_chat(chat)
            try:
                self.db.add_chat_message(session.id, chat)
            except Exception:
                logger.exception("Could not persist chat message")
            self._broadcast_chat(session, chat, seq)
            participant.typing = False
            self._ack(conn, msg_id, session=session, extra={"chat_id": chat["id"], "server_seq": seq})
            return

        if msg_type == CURSOR_UPDATE:
            if not has_permission(participant.role, "draw") and participant.role != ROLE_VIEWER:
                return
            cursor = payload.get("cursor")
            point = None
            if isinstance(cursor, dict):
                x = cursor.get("x")
                y = cursor.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    if abs(x) < 200_000 and abs(y) < 200_000:
                        point = {"x": float(x), "y": float(y)}
            if point is None:
                return
            self.sessions.update_presence(
                session,
                participant,
                cursor=point,
                tool=payload.get("tool"),
                typing=bool(payload.get("typing", False)),
            )
            self._broadcast(
                session,
                make_server_message(
                    CURSOR,
                    {
                        "participant_id": participant.id,
                        "name": participant.name,
                        "color": participant.color,
                        "cursor": point,
                        "tool": participant.tool,
                        "typing": participant.typing,
                    },
                    session_id=session.id,
                ),
                exclude=participant.id,
            )
            return

        if msg_type == SET_PROFILE:
            tool = payload.get("tool")
            role = payload.get("role")
            if role is not None and role != participant.role:
                self._error(conn, msg_id, ERR_FORBIDDEN, "Role changes require the host", session=session)
                return
            self.sessions.update_presence(
                session, participant, tool=tool, cursor=participant.cursor
            )
            self._broadcast_presence(session)
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == REQUEST_SYNC:
            since = payload.get("since_seq")
            since_int = int(since) if isinstance(since, int) else 0
            force_full = bool(payload.get("full", False))
            if not force_full and since_int > 0 and session.event_gap_available(since_int):
                events = session.events_since(since_int)
                for event in events:
                    self._send(conn, event_to_message(event))
                self._send(
                    conn,
                    make_server_message(
                        SYNC_COMPLETE,
                        {"seq": session.seq, "mode": "gap", "replayed": len(events)},
                        session_id=session.id,
                        server_seq=session.seq,
                    ),
                )
            else:
                self._send(
                    conn,
                    make_server_message(
                        CANVAS_STATE,
                        {
                            "board": session.board.snapshot(),
                            "seq": session.seq,
                            "participants": session.online_participants(),
                        },
                        session_id=session.id,
                        server_seq=session.seq,
                    ),
                )
                self._send(
                    conn,
                    make_server_message(
                        SYNC_COMPLETE,
                        {"seq": session.seq, "mode": "full"},
                        session_id=session.id,
                        server_seq=session.seq,
                    ),
                )
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == GET_SERVER_STATS:
            self._send(
                conn,
                make_server_message(
                    SERVER_STATS,
                    self._stats_payload(session),
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == SET_ROLE:
            if participant.role != ROLE_HOST:
                raise StateError("Only the host can change roles", ERR_FORBIDDEN)
            target = self._target_participant(session, payload.get("participant_id"))
            new_role = payload.get("role")
            if new_role not in (ROLE_EDITOR, ROLE_VIEWER):
                raise ValidationFailure("Role must be editor or viewer")
            if target.role == ROLE_HOST:
                raise StateError("The host role cannot be reassigned", ERR_FORBIDDEN)
            self.sessions.set_role(session, target, new_role)
            self._broadcast_presence(session)
            self._broadcast(
                session,
                make_server_message(
                    SYSTEM,
                    {"message": f"{target.name} is now {new_role}", "level": "info"},
                    session_id=session.id,
                ),
            )
            self._log_activity(session, "role", participant, f"{participant.name} made {target.name} a {new_role}")
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == KICK_USER:
            if participant.role != ROLE_HOST:
                raise StateError("Only the host can remove participants", ERR_FORBIDDEN)
            target = self._target_participant(session, payload.get("participant_id"))
            if target.id == session.host_id:
                raise StateError("The host cannot be removed", ERR_FORBIDDEN)
            if target.connection:
                self._send(
                    target.connection,
                    make_server_message(
                        KICKED,
                        {"reason": "Removed by the host"},
                        session_id=session.id,
                    ),
                )
                try:
                    target.connection.close("Removed by host")
                except Exception:
                    pass
            self.sessions.remove_participant(session, target)
            self._broadcast_presence(session)
            self._emit(
                session,
                action=ACTION_USER_LEFT,
                actor=participant,
                body={"id": target.id, "name": target.name, "kicked": True},
            )
            self._log_activity(session, "kick", participant, f"{participant.name} removed {target.name}")
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == SNAPSHOT_CREATE:
            if not has_permission(participant.role, "snapshot"):
                raise StateError("You do not have permission to save versions", ERR_FORBIDDEN)
            name = payload.get("name") or f"Version {time.strftime('%Y-%m-%d %H:%M')}"
            description = payload.get("description") or ""
            snap = self.sessions.create_snapshot(
                session,
                name=str(name)[:120],
                description=str(description)[:500],
                kind="manual",
                actor_id=participant.id,
            )
            self.sessions.save_session(session)
            self._send(
                conn,
                make_server_message(
                    SNAPSHOT_SAVED,
                    {
                        "snapshot": {
                            "id": snap["id"], "name": snap["name"],
                            "description": snap["description"], "kind": snap["kind"],
                            "created_at": snap["created_at"], "created_by": snap["created_by"],
                        }
                    },
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
            self._log_activity(session, "snapshot", participant, f"{participant.name} saved '{snap['name']}'")
            self._ack(conn, msg_id, session=session, extra={"snapshot_id": snap["id"]})
            return

        if msg_type == SNAPSHOT_LIST:
            snapshots = self.sessions.list_snapshots(session)
            self._send(
                conn,
                make_server_message(
                    SNAPSHOT_LIST_RESULT,
                    {"snapshots": snapshots},
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == SNAPSHOT_GET:
            snapshot_id = payload.get("snapshot_id")
            if not isinstance(snapshot_id, str):
                raise ValidationFailure("snapshot_id is required")
            snapshot = self.db.get_snapshot(snapshot_id)
            if snapshot is None or snapshot["session_id"] != session.id:
                raise StateError("Snapshot not found", ERR_NOT_FOUND)
            state = load_json(snapshot["state_json"], {})
            self._send(
                conn,
                make_server_message(
                    SNAPSHOT_PREVIEW,
                    {
                        "snapshot": {
                            "id": snapshot["id"],
                            "name": snapshot["name"],
                            "description": snapshot["description"],
                            "kind": snapshot["kind"],
                            "created_at": snapshot["created_at"],
                            "created_by": snapshot["created_by"],
                            "state_bytes": len(snapshot["state_json"]),
                        },
                        "board": state,
                    },
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == SNAPSHOT_RESTORE:
            if participant.role != ROLE_HOST:
                raise StateError("Only the host can restore versions", ERR_FORBIDDEN)
            if session.presentation.get("active"):
                raise StateError("Cannot restore a version during presentation", ERR_BOARD_LOCKED)
            snapshot_id = payload.get("snapshot_id")
            if not isinstance(snapshot_id, str):
                raise ValidationFailure("snapshot_id is required")
            snap = self.sessions.restore_snapshot(session, snapshot_id, participant.id)
            seq = session.next_seq()
            self._broadcast(
                session,
                make_server_message(
                    CANVAS_STATE,
                    {
                        "board": session.board.snapshot(),
                        "seq": seq,
                        "reason": "version_restore",
                        "version": snap["name"],
                    },
                    session_id=session.id,
                    server_seq=seq,
                ),
            )
            self._broadcast(
                session,
                make_server_message(
                    VERSION_RESTORED,
                    {"name": snap["name"], "created_at": snap["created_at"], "by": participant.name},
                    session_id=session.id,
                    server_seq=seq,
                ),
            )
            self._emit(
                session,
                action=ACTION_VERSION_RESTORED,
                actor=participant,
                body={"name": snap["name"], "snapshot_id": snapshot_id},
            )
            self._ack(conn, msg_id, session=session, extra={"snapshot_id": snapshot_id})
            return

        if msg_type == BOARD_IMPORT:
            if participant.role != ROLE_HOST:
                raise StateError("Only the host can replace the board", ERR_FORBIDDEN)
            if session.presentation.get("active"):
                raise StateError("Cannot import a board during presentation", ERR_BOARD_LOCKED)
            if session.locked and participant.role != ROLE_HOST:
                raise StateError("Board is locked", ERR_BOARD_LOCKED)
            objects = payload.get("objects")
            if not isinstance(objects, list):
                raise ValidationFailure("objects must be an array")
            self.sessions.create_snapshot(
                session,
                name="Before import",
                description="Safety snapshot captured before importing a board",
                kind="safety",
                actor_id=participant.id,
            )
            body = session.board.replace_all(
                objects, actor_id=participant.id, actor_name=participant.name
            )
            event = self._emit(
                session,
                action=ACTION_CANVAS_REPLACED,
                actor=participant,
                body={"objects": body["objects"], "count": body["count"]},
            )
            self.sessions.save_session(session)
            self._ack(conn, msg_id, session=session, event=event)
            return

        if msg_type in (PRESENTATION_START, PRESENTATION_UPDATE, PRESENTATION_STOP):
            if participant.role != ROLE_HOST:
                raise StateError("Only the host can control presentation mode", ERR_FORBIDDEN)
            if msg_type == PRESENTATION_STOP:
                session.presentation = {"active": False}
                self._broadcast(
                    session,
                    make_server_message(
                        PRESENTATION_STATE, {"active": False}, session_id=session.id, server_seq=session.seq
                    ),
                )
                self._emit(
                    session,
                    action=ACTION_PRESENTATION,
                    actor=participant,
                    body={"active": False},
                )
                self.sessions.save_session(session)
                self._ack(conn, msg_id, session=session)
                return
            viewport = payload.get("viewport")
            if not isinstance(viewport, dict):
                raise ValidationFailure("viewport is required")
            clean_viewport = {
                "x": max(-1e6, min(1e6, float(viewport.get("x", 0)))),
                "y": max(-1e6, min(1e6, float(viewport.get("y", 0)))),
                "w": max(50.0, min(1e6, float(viewport.get("w", 1200)))),
                "h": max(50.0, min(1e6, float(viewport.get("h", 800)))),
            }
            if msg_type == PRESENTATION_START:
                session.presentation = {
                    "active": True,
                    "presenter_id": participant.id,
                    "presenter_name": participant.name,
                    "viewport": clean_viewport,
                    "started_at": utc_now_iso(),
                }
                self._emit(
                    session,
                    action=ACTION_PRESENTATION,
                    actor=participant,
                    body={"active": True, "presenter": participant.name},
                )
            else:
                session.presentation["viewport"] = clean_viewport
                session.presentation["active"] = True
            self.sessions.save_session(session)
            self._broadcast(
                session,
                make_server_message(
                    PRESENTATION_STATE,
                    session.presentation,
                    session_id=session.id,
                    server_seq=session.seq,
                ),
            )
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == SET_BOARD_LOCK:
            if participant.role != ROLE_HOST:
                raise StateError("Only the host can lock the board", ERR_FORBIDDEN)
            session.locked = bool(payload.get("locked"))
            self._emit(
                session,
                action=ACTION_BOARD_LOCKED,
                actor=participant,
                body={"locked": session.locked},
            )
            self.sessions.save_session(session)
            self._ack(conn, msg_id, session=session)
            return

        if msg_type == LASER:
            if not has_permission(participant.role, "laser"):
                return
            cursor = payload.get("cursor")
            if not isinstance(cursor, dict):
                return
            try:
                x, y = float(cursor.get("x", 0)), float(cursor.get("y", 0))
            except (TypeError, ValueError):
                return
            if abs(x) > 200_000 or abs(y) > 200_000:
                return
            self._broadcast(
                session,
                make_server_message(
                    LASER_POINT,
                    {
                        "participant_id": participant.id,
                        "name": participant.name,
                        "color": participant.color,
                        "x": x,
                        "y": y,
                        "ttl_ms": 900,
                    },
                    session_id=session.id,
                ),
                exclude=participant.id,
            )
            return

        if msg_type == LEAVE_SESSION:
            self._send(
                conn,
                make_server_message(
                    LEFT, {"reason": "left"}, session_id=session.id
                ),
            )
            self.sessions.mark_left(session, participant)
            self._broadcast_presence(session)
            self._emit(
                session,
                action=ACTION_USER_LEFT,
                actor=participant,
                body={"id": participant.id, "name": participant.name},
            )
            self.sessions.save_session(session)
            try:
                conn.close("Client left")
            except Exception:
                pass
            return

        self._error(conn, msg_id, ERR_BAD_REQUEST, f"Unknown message type: {msg_type}", session=session)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_edit(
        self, session: Session, participant: Participant, *, allow_when_locked: bool = False
    ) -> None:
        if not has_permission(participant.role, "draw"):
            raise StateError("Viewers cannot modify the board", ERR_FORBIDDEN)
        if session.presentation.get("active"):
            raise StateError(
                "The board is read-only while presentation mode is active", ERR_BOARD_LOCKED
            )
        if session.locked and participant.role != ROLE_HOST and not allow_when_locked:
            raise StateError("The host has locked this board", ERR_BOARD_LOCKED)

    def _target_participant(self, session: Session, participant_id: Any) -> Participant:
        if not isinstance(participant_id, str):
            raise ValidationFailure("participant_id is required")
        target = session.participants.get(participant_id)
        if target is None:
            raise StateError("Participant not found", ERR_NOT_FOUND)
        return target

    def _stats_payload(self, session: Session | None) -> dict[str, Any]:
        extra = {
            "server_host": self.config.display_host,
            "server_port": self.config.port,
            "active_sessions": len(self.sessions.sessions),
            "online_clients": self.sessions.total_online(),
            "total_objects": self.sessions.total_objects(),
            "db": self.db.stats(),
            "session": session.summary() if session else None,
        }
        return self.metrics.snapshot(extra)

    def shutdown(self) -> None:
        self.sessions.shutdown()
