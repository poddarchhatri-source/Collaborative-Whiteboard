"""Client-side authoritative board mirror.

The model applies canonical server events in order, de-duplicates event ids,
merges optimistic local objects, and emits granular signals for the scene.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from shared.constants import (
    ACTION_BOARD_LOCKED,
    ACTION_CANVAS_CLEARED,
    ACTION_CANVAS_REPLACED,
    ACTION_CHAT,
    ACTION_OBJECT_CREATED,
    ACTION_OBJECTS_DELETED,
    ACTION_OBJECT_UPDATED,
    ACTION_REDO,
    ACTION_STROKE_APPENDED,
    ACTION_UNDO,
    ACTION_USER_JOINED,
    ACTION_USER_LEFT,
    ACTION_VERSION_RESTORED,
)
from shared.models import merge_patch


class BoardModel(QObject):
    object_added = Signal(dict)
    object_updated = Signal(dict)
    object_removed = Signal(str)
    canvas_reset = Signal()
    changed = Signal()
    event_applied = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.objects: dict[str, dict[str, Any]] = {}
        self.last_seq = 0
        self._seen_events: set[str] = set()
        self._seen_order: list[str] = []
        self.locked = False
        self.board_name = ""
        self.presentation: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # State loading
    # ------------------------------------------------------------------
    def clear(self) -> None:
        self.objects.clear()
        self.last_seq = 0
        self._seen_events.clear()
        self._seen_order.clear()
        self.canvas_reset.emit()
        self.changed.emit()

    def load_board(self, payload: dict[str, Any] | None, *, seq: int | None = None) -> None:
        objects: dict[str, dict[str, Any]] = {}
        if isinstance(payload, dict):
            for obj in payload.get("objects", []) if isinstance(payload.get("objects"), list) else []:
                if isinstance(obj, dict) and isinstance(obj.get("id"), str):
                    objects[obj["id"]] = copy.deepcopy(obj)
        self.objects = objects
        if seq is not None:
            self.last_seq = int(seq)
        self.canvas_reset.emit()
        self.changed.emit()

    def snapshot(self) -> dict[str, Any]:
        ordered = sorted(
            self.objects.values(),
            key=lambda obj: (int(obj.get("z", 0)), obj.get("created_at", ""), obj.get("id", "")),
        )
        return {
            "format": "syncanvas-board",
            "version": 1,
            "objects": [copy.deepcopy(obj) for obj in ordered],
        }

    # ------------------------------------------------------------------
    # Event application
    # ------------------------------------------------------------------
    def apply_event_message(self, message: dict[str, Any]) -> None:
        event_id = message.get("event_id", "")
        if event_id:
            if event_id in self._seen_events:
                return
            self._seen_events.add(event_id)
            self._seen_order.append(event_id)
            if len(self._seen_order) > 10_000:
                oldest = self._seen_order[:5000]
                for item in oldest:
                    self._seen_events.discard(item)
                del self._seen_order[:5000]
        seq = message.get("server_seq")
        if isinstance(seq, int):
            self.last_seq = max(self.last_seq, seq)
        payload = message.get("payload", {}) or {}
        action = payload.get("action")
        body = payload.get("body", {}) or {}
        actor = {
            "id": payload.get("actor_id", ""),
            "name": payload.get("actor_name", ""),
        }
        self.event_applied.emit(
            {
                "action": action,
                "body": body,
                "actor": actor,
                "timestamp": message.get("timestamp"),
                "seq": seq,
            }
        )
        try:
            self._apply_action(action, body)
        except Exception:
            # A malformed event should never crash the canvas.  A full sync is
            # available via REQUEST_SYNC if the model becomes inconsistent.
            pass

    def _apply_action(self, action: str | None, body: dict[str, Any]) -> None:
        if action == ACTION_OBJECT_CREATED:
            obj = body.get("object")
            if isinstance(obj, dict):
                self._upsert(obj)
        elif action == ACTION_STROKE_APPENDED:
            object_id = body.get("object_id")
            points = body.get("points", [])
            obj = self.objects.get(object_id) if isinstance(object_id, str) else None
            if obj is not None and isinstance(points, list):
                event_rev = int(body.get("rev", obj.get("rev", 1)))
                current_rev = int(obj.get("rev", 1))
                if event_rev > current_rev:
                    obj.setdefault("points", []).extend(points)
                    obj["rev"] = event_rev
                    obj["updated_at"] = body.get("updated_at", obj.get("updated_at"))
                    self._emit_updated(obj)
        elif action == ACTION_OBJECT_UPDATED:
            object_id = body.get("object_id")
            patch = body.get("patch")
            obj = self.objects.get(object_id) if isinstance(object_id, str) else None
            if obj is not None and isinstance(patch, dict):
                event_rev = int(body.get("rev", obj.get("rev", 1)))
                # Local optimistic revisions can be ahead of an echoed event.
                if event_rev >= int(obj.get("rev", 1)):
                    merged = merge_patch(obj, patch)
                    merged["rev"] = event_rev
                    if body.get("updated_at"):
                        merged["updated_at"] = body["updated_at"]
                    self.objects[obj["id"]] = merged
                    self._emit_updated(merged)
        elif action == ACTION_OBJECTS_DELETED:
            for object_id in body.get("ids", []):
                if isinstance(object_id, str) and object_id in self.objects:
                    del self.objects[object_id]
                    self.object_removed.emit(object_id)
            self.changed.emit()
        elif action == ACTION_CANVAS_CLEARED:
            self.objects.clear()
            self.canvas_reset.emit()
            self.changed.emit()
        elif action == ACTION_CANVAS_REPLACED:
            objects = body.get("objects")
            if isinstance(objects, list):
                self.load_board({"objects": objects})
        elif action in (ACTION_UNDO, ACTION_REDO):
            removed = body.get("remove", [])
            for object_id in removed:
                if isinstance(object_id, str) and object_id in self.objects:
                    del self.objects[object_id]
                    self.object_removed.emit(object_id)
            for obj in body.get("upserts", []):
                if isinstance(obj, dict):
                    self._upsert(obj)
            self.changed.emit()
        elif action == ACTION_BOARD_LOCKED:
            self.locked = bool(body.get("locked"))
            self.changed.emit()
        elif action == ACTION_VERSION_RESTORED:
            pass
        elif action in (ACTION_USER_JOINED, ACTION_USER_LEFT, ACTION_CHAT):
            pass

    def apply_canvas_state(self, payload: dict[str, Any]) -> None:
        board = payload.get("board", {})
        seq = payload.get("seq", self.last_seq)
        self.load_board(board, seq=seq if isinstance(seq, int) else None)
        self.changed.emit()

    def set_locked(self, locked: bool) -> None:
        self.locked = bool(locked)
        self.changed.emit()

    # ------------------------------------------------------------------
    # Optimistic local operations
    # ------------------------------------------------------------------
    def add_local_object(self, obj: dict[str, Any]) -> None:
        if obj.get("id") in self.objects:
            self.update_local_object(obj)
            return
        local = copy.deepcopy(obj)
        local["_local_pending"] = True
        local.setdefault("rev", 1)
        self.objects[local["id"]] = local
        self.object_added.emit(local)
        self.changed.emit()

    def update_local_object(self, obj: dict[str, Any]) -> None:
        object_id = obj.get("id")
        if not object_id:
            return
        self.objects[object_id] = copy.deepcopy(obj)
        self._emit_updated(self.objects[object_id])

    def append_local_stroke(self, object_id: str, points: list[list[float]]) -> None:
        obj = self.objects.get(object_id)
        if obj is None:
            return
        obj.setdefault("points", []).extend(points)
        obj["rev"] = int(obj.get("rev", 1)) + 1
        obj["_local_pending"] = True
        self._emit_updated(obj)

    def remove_local_objects(self, ids: list[str]) -> None:
        for object_id in ids:
            if object_id in self.objects:
                del self.objects[object_id]
                self.object_removed.emit(object_id)
        self.changed.emit()

    def object_rev(self, object_id: str) -> int:
        obj = self.objects.get(object_id)
        return int(obj.get("rev", 1)) if obj else 1

    def object_count(self) -> int:
        return len(self.objects)

    def next_z(self) -> int:
        if not self.objects:
            return 1
        return max(int(obj.get("z", 0)) for obj in self.objects.values()) + 1

    # ------------------------------------------------------------------
    def _upsert(self, obj: dict[str, Any]) -> None:
        object_id = obj.get("id")
        if not isinstance(object_id, str):
            return
        existing = self.objects.get(object_id)
        if existing is None:
            stored = copy.deepcopy(obj)
            stored["_local_pending"] = False
            self.objects[object_id] = stored
            self.object_added.emit(stored)
            self.changed.emit()
            return
        incoming_rev = int(obj.get("rev", 1))
        current_rev = int(existing.get("rev", 1))
        merged = copy.deepcopy(obj)
        if current_rev > incoming_rev:
            # Preserve optimistic points and revision while acknowledging the
            # server's identity metadata (owner, timestamps, canonical style).
            merged["points"] = existing.get("points", [])
            merged["rev"] = current_rev
        merged["_local_pending"] = bool(existing.get("_local_pending")) and current_rev > incoming_rev
        self.objects[object_id] = merged
        self._emit_updated(merged)

    def _emit_updated(self, obj: dict[str, Any]) -> None:
        self.object_updated.emit(copy.deepcopy(obj))
        self.changed.emit()
