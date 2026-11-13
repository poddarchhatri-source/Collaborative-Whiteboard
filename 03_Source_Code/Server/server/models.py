"""Runtime session/participant models for the authoritative server."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Deque

from collections import deque

from shared.constants import (
    MAX_ACTIVITY_HISTORY,
    MAX_CHAT_HISTORY,
    MAX_CLIENTS_PER_SESSION,
    MAX_EVENT_HISTORY,
    ROLE_EDITOR,
    ROLE_HOST,
)
from shared.models import iso_now, new_object_id
from shared.protocol import utc_now_iso
from server.state.board import BoardState


@dataclass
class Participant:
    id: str
    name: str
    color: str
    role: str
    token_hash: str = ""
    online: bool = False
    tool: str = "select"
    cursor: dict[str, float] | None = None
    typing: bool = False
    joined_at: str = field(default_factory=utc_now_iso)
    last_seen: str = field(default_factory=utc_now_iso)
    connection: Any = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "role": self.role,
            "online": self.online,
            "tool": self.tool,
            "cursor": self.cursor,
            "typing": self.typing,
            "joined_at": self.joined_at,
            "last_seen": self.last_seen,
        }

    def touch(self) -> None:
        self.last_seen = utc_now_iso()


@dataclass
class Session:
    id: str
    code: str
    name: str
    password_hash: str
    salt: str
    host_id: str
    host_name: str
    board: BoardState = field(default_factory=BoardState)
    seq: int = 0
    locked: bool = False
    presentation: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    participants: dict[str, Participant] = field(default_factory=dict)
    connections: dict[str, Any] = field(default_factory=dict)
    chat_history: Deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_CHAT_HISTORY))
    activity: Deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_ACTIVITY_HISTORY))
    event_history: Deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_EVENT_HISTORY))
    last_event_seq: int = 0
    dirty: bool = False
    last_saved_at: float = 0.0
    last_snapshot_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    total_events: int = 0
    snapshot_lock: Any = None

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def online_participants(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.participants.values() if p.online]

    def all_participants(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.participants.values()]

    def get_participant(self, participant_id: str) -> Participant | None:
        return self.participants.get(participant_id)

    def host(self) -> Participant | None:
        return self.participants.get(self.host_id)

    def can_accept(self) -> bool:
        return sum(1 for p in self.participants.values() if p.online) < MAX_CLIENTS_PER_SESSION

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "host_name": self.host_name,
            "participants": len(self.online_participants()),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "locked": self.locked,
            "seq": self.seq,
        }

    def add_chat(self, message: dict[str, Any]) -> None:
        self.chat_history.append(message)
        self.updated_at = utc_now_iso()
        self.dirty = True

    def add_activity(self, entry: dict[str, Any]) -> None:
        self.activity.append(entry)

    def record_event(self, event: dict[str, Any]) -> None:
        self.event_history.append(event)
        self.last_event_seq = int(event["server_seq"])
        self.total_events += 1

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        return [event for event in self.event_history if int(event["server_seq"]) > seq]

    def event_gap_available(self, since_seq: int) -> bool:
        if since_seq >= self.seq:
            return True
        if not self.event_history:
            return False
        return int(self.event_history[0]["server_seq"]) <= since_seq + 1


@dataclass
class AuthResult:
    session: Session
    participant: Participant
    token: str
    replaced_connection: Any = None


def new_participant(
    name: str,
    color: str,
    role: str,
    token_hash: str,
    *,
    participant_id: str | None = None,
) -> Participant:
    return Participant(
        id=participant_id or new_object_id(),
        name=name,
        color=color,
        role=role,
        token_hash=token_hash,
        online=True,
        joined_at=iso_now(),
        last_seen=iso_now(),
    )
