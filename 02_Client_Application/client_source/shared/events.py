"""Event helpers and server-side permission checks."""

from __future__ import annotations

from typing import Any

from shared.constants import (
    ACTION_CANVAS_CLEARED,
    ACTION_CANVAS_REPLACED,
    ACTION_CHAT,
    ACTION_OBJECT_CREATED,
    ACTION_OBJECTS_DELETED,
    ACTION_OBJECT_UPDATED,
    ACTION_PRESENTATION,
    ACTION_STROKE_APPENDED,
    ACTION_UNDO,
    ACTION_REDO,
    ACTION_USER_JOINED,
    ACTION_USER_LEFT,
    ACTION_VERSION_RESTORED,
    ACTION_BOARD_LOCKED,
    PERMISSIONS,
    ROLE_HOST,
)
from shared.models import iso_now, new_object_id
from shared.protocol import make_server_message


def has_permission(role: str | None, permission: str) -> bool:
    if not role:
        return False
    return permission in PERMISSIONS.get(role, set())


def build_event(
    *,
    session_id: str,
    seq: int,
    action: str,
    actor_id: str,
    actor_name: str,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a canonical, ordered server event.

    Every state-changing action receives exactly one sequence number.  Clients
    apply events in sequence order and use ``event_id`` for de-duplication.
    """
    identifier = event_id or new_object_id()
    return {
        "v": 1,
        "type": "EVENT",
        "msg_id": new_object_id(),
        "event_id": identifier,
        "server_seq": int(seq),
        "session_id": session_id,
        "timestamp": timestamp or iso_now(),
        "actor_id": actor_id,
        "actor_name": actor_name,
        "action": action,
        "payload": payload or {},
    }


def event_to_message(event: dict[str, Any]) -> dict[str, Any]:
    """Convert a stored canonical event into its wire envelope."""
    return make_server_message(
        "EVENT",
        {
            "action": event["action"],
            "actor_id": event.get("actor_id"),
            "actor_name": event.get("actor_name"),
            "body": event.get("payload", {}),
        },
        session_id=event["session_id"],
        server_seq=event["server_seq"],
        event_id=event["event_id"],
    )


def activity_for_event(action: str, actor: str, body: dict[str, Any]) -> str | None:
    if action == ACTION_OBJECT_CREATED:
        kind = body.get("object", {}).get("kind", "object")
        return f"{actor} created a {kind.replace('_', ' ')}"
    if action == ACTION_STROKE_APPENDED:
        return None  # too noisy for the activity log
    if action == ACTION_OBJECT_UPDATED:
        return f"{actor} modified an object"
    if action == ACTION_OBJECTS_DELETED:
        count = len(body.get("ids", []))
        return f"{actor} deleted {count} object{'s' if count != 1 else ''}"
    if action == ACTION_CANVAS_CLEARED:
        return f"{actor} cleared the canvas"
    if action == ACTION_CANVAS_REPLACED:
        return f"{actor} imported a new board"
    if action == ACTION_UNDO:
        return f"{actor} undid their last operation"
    if action == ACTION_REDO:
        return f"{actor} redid an operation"
    if action == ACTION_VERSION_RESTORED:
        return f"{actor} restored version {body.get('name', '')}".strip()
    if action == ACTION_BOARD_LOCKED:
        return f"{actor} {'locked' if body.get('locked') else 'unlocked'} the board"
    if action == ACTION_PRESENTATION:
        state = "started" if body.get("active") else "stopped"
        return f"{actor} {state} presentation mode"
    if action == ACTION_USER_JOINED:
        return f"{body.get('name', actor)} joined the board"
    if action == ACTION_USER_LEFT:
        return f"{body.get('name', actor)} left the board"
    return None


def can_edit(role: str | None) -> bool:
    return role in (ROLE_HOST, "editor")


MUTATION_ACTIONS = {
    ACTION_OBJECT_CREATED,
    ACTION_STROKE_APPENDED,
    ACTION_OBJECT_UPDATED,
    ACTION_OBJECTS_DELETED,
    ACTION_CANVAS_CLEARED,
    ACTION_CANVAS_REPLACED,
    ACTION_UNDO,
    ACTION_REDO,
    ACTION_VERSION_RESTORED,
    ACTION_BOARD_LOCKED,
    ACTION_CHAT,
}
