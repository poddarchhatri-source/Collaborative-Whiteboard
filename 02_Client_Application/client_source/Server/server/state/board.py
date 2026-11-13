"""Authoritative board state and operation-owned collaborative undo/redo.

Design notes
------------
* The server is the single writer of the authoritative object dictionary.
* Every mutation returns a compact broadcast body.  Strokes are streamed as
  point batches instead of resending the whole canvas.
* Each participant has a private undo stack.  Undo/redo actions are applied
  atomically only when all affected objects still have the recorded revision.
  If another participant changed one of them, the server rejects the undo with
  ``UNDO_CONFLICT`` rather than clobbering newer work.
"""

from __future__ import annotations

import copy
import time
from collections import deque
from typing import Any

from shared.constants import (
    KIND_CONNECTOR,
    KIND_IMAGE,
    KIND_STROKE,
    MAX_OBJECTS,
    MAX_UNDO_HISTORY,
    MAX_REDO_HISTORY,
)
from shared.models import (
    ValidationFailure,
    bounds_for_object,
    clone_object,
    merge_patch,
    normalize_new_object,
    validate_patch,
)
from shared.protocol import utc_now_iso


class StateError(Exception):
    def __init__(self, message: str, code: str = "BAD_REQUEST", data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


class BoardState:
    def __init__(self, objects: dict[str, dict[str, Any]] | None = None) -> None:
        self.objects: dict[str, dict[str, Any]] = objects or {}
        self.undo_stacks: dict[str, deque[dict[str, Any]]] = {}
        self.redo_stacks: dict[str, deque[dict[str, Any]]] = {}
        self.last_modified = time.time()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def object_count(self) -> int:
        return len(self.objects)

    def sorted_objects(self) -> list[dict[str, Any]]:
        return sorted(
            self.objects.values(),
            key=lambda obj: (int(obj.get("z", 0)), obj.get("created_at", ""), obj.get("id", "")),
        )

    def get(self, object_id: str) -> dict[str, Any] | None:
        return self.objects.get(object_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "format": "syncanvas-board",
            "version": 1,
            "objects": [copy.deepcopy(obj) for obj in self.sorted_objects()],
        }

    def serialized_json_size(self) -> int:
        import json

        payload = self.snapshot()
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def load(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        raw_objects = payload.get("objects", [])
        if not isinstance(raw_objects, list):
            return
        for raw in raw_objects:
            if not isinstance(raw, dict):
                continue
            obj_id = raw.get("id")
            if not isinstance(obj_id, str):
                continue
            # Stored objects were normalized when they entered the server; copy
            # defensively so the in-memory state never aliases loaded JSON.
            self.objects[obj_id] = copy.deepcopy(raw)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def create_object(
        self, raw: Any, *, actor_id: str, actor_name: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if len(self.objects) >= MAX_OBJECTS:
            raise StateError("Board object limit reached", "SERVER_BUSY")
        try:
            obj = normalize_new_object(raw, owner_id=actor_id, owner_name=actor_name)
        except ValidationFailure:
            raise
        if obj["id"] in self.objects:
            raise StateError("Object id already exists", "REVISION_CONFLICT")
        if obj["kind"] == KIND_CONNECTOR:
            for endpoint in (obj["connector"]["from_id"], obj["connector"]["to_id"]):
                if endpoint not in self.objects:
                    raise StateError("Connector endpoint does not exist", "NOT_FOUND")
        self.objects[obj["id"]] = obj
        self._touch()
        # A stroke may still grow through STROKE_APPEND events, but its history
        # entry is created now. Undo deletes the whole stroke, which is the
        # natural unit users expect.
        action = {
            "label": f"create {obj['kind']}",
            "undo": [{"op": "remove", "id": obj["id"], "owner_id": actor_id}],
            "redo": [{"op": "restore", "object": clone_object(obj)}],
        }
        self._push_undo(actor_id, action)
        return {"object": obj}, [action]

    def append_stroke(
        self, object_id: str, points: Any, *, actor_id: str
    ) -> dict[str, Any]:
        obj = self.objects.get(object_id)
        if obj is None:
            raise StateError("Stroke no longer exists", "NOT_FOUND")
        if obj.get("kind") != KIND_STROKE:
            raise StateError("STROKE_APPEND is only valid for strokes")
        if obj.get("owner_id") != actor_id:
            raise StateError("Only the stroke author may append to it", "FORBIDDEN")
        if not isinstance(points, list) or not points:
            raise StateError("points must be a non-empty array")
        if len(points) > 5000:
            raise StateError("Too many points in one batch", "TOO_LARGE")
        clean: list[list[float]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                x = float(point[0])
                y = float(point[1])
            except (TypeError, ValueError):
                continue
            if not (-1e6 < x < 1e6 and -1e6 < y < 1e6):
                continue
            clean.append([round(x, 2), round(y, 2)])
        if not clean:
            raise StateError("No valid points in batch")
        existing = obj.setdefault("points", [])
        if len(existing) + len(clean) > 60_000:
            raise StateError("Stroke is too long", "TOO_LARGE")
        existing.extend(clean)
        obj["rev"] = int(obj.get("rev", 1)) + 1
        obj["updated_at"] = utc_now_iso()
        x, y, w, h = bounds_for_object(obj)
        # Keep the object origin stable; only cached bounds change.
        obj["w"], obj["h"] = round(w, 2), round(h, 2)
        self._touch()
        return {
            "object_id": object_id,
            "points": clean,
            "rev": obj["rev"],
            "updated_at": obj["updated_at"],
        }

    def update_object(
        self,
        object_id: str,
        patch: Any,
        *,
        base_rev: int | None,
        actor_id: str,
        actor_name: str,
    ) -> dict[str, Any]:
        obj = self.objects.get(object_id)
        if obj is None:
            raise StateError("Object not found", "NOT_FOUND")
        try:
            clean_patch = validate_patch(patch)
        except ValidationFailure:
            raise
        if not clean_patch:
            raise StateError("Empty patch")
        current_rev = int(obj.get("rev", 1))
        if base_rev is None:
            raise StateError("base_rev is required for updates", "BAD_REQUEST")
        if int(base_rev) != current_rev:
            raise StateError(
                "The object changed since you loaded it. The server kept the newer version.",
                "REVISION_CONFLICT",
                {"object": copy.deepcopy(obj), "expected_rev": current_rev},
            )
        if obj.get("locked") and obj.get("owner_id") != actor_id:
            raise StateError("Object is locked", "FORBIDDEN")
        before = clone_object(obj)
        merged = merge_patch(obj, clean_patch)
        if "connector" in clean_patch:
            endpoints = (
                clean_patch["connector"]["from_id"], clean_patch["connector"]["to_id"]
            )
            for endpoint in endpoints:
                if endpoint not in self.objects:
                    raise StateError("Connector endpoint does not exist", "NOT_FOUND")
        merged["rev"] = current_rev + 1
        merged["updated_at"] = utc_now_iso()
        merged["owner_name"] = obj.get("owner_name", actor_name)
        self.objects[object_id] = merged
        after = clone_object(merged)
        action = {
            "label": "update object",
            "undo": [{"op": "replace", "object": before, "expect_rev": after["rev"]}],
            "redo": [{"op": "replace", "object": after, "expect_rev": after["rev"] + 1}],
        }
        self._push_undo(actor_id, action)
        self._touch()
        return {
            "object_id": object_id,
            "patch": clean_patch,
            "rev": merged["rev"],
            "updated_at": merged["updated_at"],
        }

    def delete_objects(
        self, ids: Any, *, actor_id: str, actor_name: str
    ) -> dict[str, Any]:
        if not isinstance(ids, list) or not ids:
            raise StateError("ids must be a non-empty array")
        if len(ids) > 500:
            raise StateError("Too many objects in one delete", "TOO_LARGE")
        removed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for object_id in ids:
            if not isinstance(object_id, str) or object_id in seen:
                continue
            seen.add(object_id)
            obj = self.objects.get(object_id)
            if obj is None:
                continue
            if obj.get("locked") and obj.get("owner_id") != actor_id:
                continue
            removed.append(clone_object(obj))
        if not removed:
            raise StateError("No removable objects found", "NOT_FOUND")
        removed_ids = {obj["id"] for obj in removed}
        # Deleting a connected node must also retire its attached connectors;
        # leaving dangling connectors would confuse the diagram.
        for obj in list(self.objects.values()):
            if obj.get("kind") != KIND_CONNECTOR:
                continue
            connector = obj.get("connector", {})
            if connector.get("from_id") in removed_ids or connector.get("to_id") in removed_ids:
                if obj["id"] not in removed_ids:
                    removed_ids.add(obj["id"])
                    removed.append(clone_object(obj))
        for obj in removed:
            self.objects.pop(obj["id"], None)
        action = {
            "label": "delete objects",
            "undo": [{"op": "restore", "object": clone_object(obj)} for obj in removed],
            "redo": [
                {
                    "op": "remove",
                    "id": obj["id"],
                    "owner_id": obj.get("owner_id"),
                    "expect_rev": None,
                }
                for obj in removed
            ],
        }
        self._push_undo(actor_id, action)
        self._touch()
        return {"ids": [obj["id"] for obj in removed], "objects": removed}

    def clear(self, *, actor_id: str, actor_name: str) -> dict[str, Any]:
        before = [clone_object(obj) for obj in self.sorted_objects()]
        self.objects.clear()
        action = {
            "label": "clear canvas",
            "undo": [{"op": "restore", "object": clone_object(obj)} for obj in before],
            "redo": [{"op": "remove_all"}],
            "guard": "clear",
            "guard_restored_revs": {},
        }
        self._push_undo(actor_id, action)
        self._touch()
        return {"count": len(before), "objects": before}

    def replace_all(
        self, raw_objects: Any, *, actor_id: str, actor_name: str
    ) -> dict[str, Any]:
        if not isinstance(raw_objects, list):
            raise StateError("objects must be an array")
        if len(raw_objects) > MAX_OBJECTS:
            raise StateError("Too many objects", "TOO_LARGE")
        before = [clone_object(obj) for obj in self.sorted_objects()]
        new_objects: dict[str, dict[str, Any]] = {}
        for raw in raw_objects:
            obj = normalize_new_object(raw, owner_id=actor_id, owner_name=actor_name)
            if obj["id"] in new_objects:
                obj["id"] = __import__("uuid").uuid4().hex
            new_objects[obj["id"]] = obj
        self.objects = new_objects
        after = [clone_object(obj) for obj in self.sorted_objects()]
        action = {
            "label": "import board",
            "undo": [
                {"op": "remove_all"},
                *[{"op": "restore", "object": clone_object(obj)} for obj in before],
            ],
            "redo": [
                {"op": "remove_all"},
                *[{"op": "restore", "object": clone_object(obj)} for obj in after],
            ],
            "guard": "replace",
            "before_revs": {obj["id"]: int(obj.get("rev", 1)) for obj in before},
            "after_revs": {obj["id"]: int(obj.get("rev", 1)) for obj in after},
        }
        self._push_undo(actor_id, action)
        self._touch()
        return {"objects": after, "count": len(after)}

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------
    def _push_undo(self, actor_id: str, action: dict[str, Any]) -> None:
        stack = self.undo_stacks.setdefault(actor_id, deque(maxlen=MAX_UNDO_HISTORY))
        action["owner_id"] = actor_id
        action["timestamp"] = utc_now_iso()
        stack.append(action)
        self.redo_stacks[actor_id] = deque(maxlen=MAX_REDO_HISTORY)

    def can_undo(self, actor_id: str) -> bool:
        return bool(self.undo_stacks.get(actor_id))

    def can_redo(self, actor_id: str) -> bool:
        return bool(self.redo_stacks.get(actor_id))

    def undo(self, actor_id: str) -> dict[str, Any]:
        stack = self.undo_stacks.get(actor_id)
        if not stack:
            raise StateError("Nothing to undo", "NOT_FOUND")
        action = stack[-1]
        if not self._can_apply(action, "undo"):
            raise StateError(
                "Undo skipped: someone else changed the same object after this operation.",
                "UNDO_CONFLICT",
            )
        stack.pop()
        result = self._apply_ops(action["undo"], direction="undo")
        # Store the revision produced by the undo so redo can detect intervening
        # edits to the same object/board.
        self._update_guards_after_apply(action, result, direction="undo")
        redo_stack = self.redo_stacks.setdefault(actor_id, deque(maxlen=MAX_REDO_HISTORY))
        redo_stack.append(action)
        self._touch()
        body = {
            "description": action.get("label", "operation"),
            "upserts": result["upserts"],
            "remove": result["remove"],
            "source_actor_id": actor_id,
        }
        return body

    def redo(self, actor_id: str) -> dict[str, Any]:
        stack = self.redo_stacks.get(actor_id)
        if not stack:
            raise StateError("Nothing to redo", "NOT_FOUND")
        action = stack[-1]
        if not self._can_apply(action, "redo"):
            raise StateError(
                "Redo skipped: the board changed since this operation was undone.",
                "UNDO_CONFLICT",
            )
        stack.pop()
        result = self._apply_ops(action["redo"], direction="redo")
        self._update_guards_after_apply(action, result, direction="redo")
        self.undo_stacks.setdefault(actor_id, deque(maxlen=MAX_UNDO_HISTORY)).append(action)
        self._touch()
        return {
            "description": action.get("label", "operation"),
            "upserts": result["upserts"],
            "remove": result["remove"],
            "source_actor_id": actor_id,
        }

    def _can_apply(self, action: dict[str, Any], direction: str) -> bool:
        """Conflict preflight: undo must never silently overwrite newer state."""
        guard = action.get("guard")
        current_fingerprint = self._revision_fingerprint()
        if guard == "clear":
            # A clear can be undone only while the board is still empty; a
            # redo can be applied only if the restored set is untouched.
            if direction == "undo":
                return not current_fingerprint
            return current_fingerprint == action.get("guard_restored_revs", {})
        if guard == "replace":
            if direction == "undo":
                return current_fingerprint == action.get("after_revs", {})
            return current_fingerprint == action.get("before_revs", {})
        ops = action.get(direction, [])
        for op in ops:
            kind = op.get("op")
            if kind == "restore":
                obj = op["object"]
                existing = self.objects.get(obj["id"])
                if existing is not None:
                    return False
            elif kind == "replace":
                existing = self.objects.get(op["object"]["id"])
                if existing is None:
                    return False
                if int(existing.get("rev", 1)) != int(op.get("expect_rev", -1)):
                    return False
            elif kind == "remove":
                existing = self.objects.get(op["id"])
                if existing is None:
                    return False
                allowed_owner = op.get("owner_id")
                if allowed_owner is not None and existing.get("owner_id") != allowed_owner:
                    return False
                expected_rev = op.get("expect_rev")
                if expected_rev is not None and int(existing.get("rev", 1)) != int(expected_rev):
                    return False
            elif kind == "remove_all":
                pass
        return True

    def _revision_fingerprint(self) -> dict[str, int]:
        return {obj_id: int(obj.get("rev", 1)) for obj_id, obj in sorted(self.objects.items())}

    def _update_guards_after_apply(
        self, action: dict[str, Any], result: dict[str, list], *, direction: str
    ) -> None:
        if action.get("guard") == "clear" and direction == "undo":
            action["guard_restored_revs"] = self._revision_fingerprint()
        if action.get("guard") == "replace":
            if direction == "undo":
                action["before_revs"] = self._revision_fingerprint()
            else:
                action["after_revs"] = self._revision_fingerprint()
        # Keep the *other* direction's optimistic check in sync with the new
        # revision produced by this undo/redo.
        target = "redo" if direction == "undo" else "undo"
        upsert_by_id = {obj["id"]: obj for obj in result["upserts"]}
        for op in action.get(target, []):
            if op.get("op") == "replace":
                changed = upsert_by_id.get(op["object"]["id"])
                if changed:
                    op["expect_rev"] = int(changed.get("rev", 1))
            elif op.get("op") == "remove":
                # After restoring deleted objects, redo should only remove the
                # exact revisions produced by the restore.
                changed = upsert_by_id.get(op["id"])
                if changed:
                    op["expect_rev"] = int(changed.get("rev", 1))

    def _apply_ops(self, ops: list[dict[str, Any]], *, direction: str) -> dict[str, list]:
        upserts: list[dict[str, Any]] = []
        removed: list[str] = []
        for op in ops:
            if op["op"] == "remove":
                object_id = op["id"]
                if object_id in self.objects:
                    del self.objects[object_id]
                    removed.append(object_id)
            elif op["op"] == "remove_all":
                removed.extend(list(self.objects.keys()))
                self.objects.clear()
            elif op["op"] == "restore":
                obj = clone_object(op["object"])
                # A restored object is a new logical revision.
                obj["rev"] = int(obj.get("rev", 1)) + 1
                obj["updated_at"] = utc_now_iso()
                self.objects[obj["id"]] = obj
                upserts.append(obj)
            elif op["op"] == "replace":
                obj = clone_object(op["object"])
                current = self.objects.get(obj["id"])
                current_rev = int(current.get("rev", 1)) if current else 0
                obj["rev"] = current_rev + 1
                obj["updated_at"] = utc_now_iso()
                self.objects[obj["id"]] = obj
                upserts.append(obj)
        return {"upserts": upserts, "remove": removed}

    def _touch(self) -> None:
        self.last_modified = time.time()
