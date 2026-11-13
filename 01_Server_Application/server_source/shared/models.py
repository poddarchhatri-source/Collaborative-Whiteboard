"""Validated domain models shared by server and client.

The wire representation is intentionally plain JSON dictionaries.  These
helpers provide the *server-side* validation boundary: every object that enters
the authoritative state passes through :func:`normalize_new_object`, and every
patch passes through :func:`validate_patch`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
import string
import uuid
from datetime import datetime
from typing import Any

from shared.constants import (
    DEFAULT_FILL,
    DEFAULT_STROKE,
    FILLABLE_KINDS,
    KIND_CONNECTOR,
    KIND_IMAGE,
    KIND_STICKY,
    KIND_STROKE,
    KIND_TEXT,
    MAX_CHAT_LENGTH,
    MAX_IMAGE_BYTES,
    MAX_NAME_LENGTH,
    MAX_PASSWORD_LENGTH,
    MAX_SESSION_NAME,
    MAX_TEXT_LENGTH,
    OBJECT_KINDS,
    ROLE_EDITOR,
    ROLE_HOST,
    ROLE_VIEWER,
    ROLES,
    STICKY_COLORS,
)
from shared.protocol import bounded_int, bounded_number, utc_now_iso

# Colours are kept to a small, explicit grammar. This prevents injection into
# SVG/Qt stylesheet paths and keeps exports deterministic.
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ALLOWED_COLORS = {"transparent", "none"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{6,80}$")
_CODE_RE = re.compile(r"^[A-Z2-9]{6}$")
_SAFE_USERNAME = re.compile(r"^[^\x00-\x1f<>{}\\]{2,32}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ValidationFailure(ValueError):
    def __init__(self, message: str, code: str = "BAD_REQUEST") -> None:
        super().__init__(message)
        self.code = code


def new_object_id() -> str:
    return uuid.uuid4().hex


def new_token() -> str:
    return secrets.token_urlsafe(32)


def validate_username(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationFailure("Username must be text")
    name = value.strip()
    if not (2 <= len(name) <= MAX_NAME_LENGTH):
        raise ValidationFailure(f"Username must be 2-{MAX_NAME_LENGTH} characters")
    if not _SAFE_USERNAME.match(name):
        raise ValidationFailure("Username contains unsupported characters")
    return name


def validate_password(value: Any, *, allow_empty: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationFailure("Password must be text")
    if len(value) > MAX_PASSWORD_LENGTH:
        raise ValidationFailure("Password is too long")
    if not allow_empty and len(value) < 4:
        raise ValidationFailure("Session password must be at least 4 characters")
    return value


def validate_session_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationFailure("Board name must be text")
    name = value.strip()
    if not (2 <= len(name) <= MAX_SESSION_NAME):
        raise ValidationFailure(f"Board name must be 2-{MAX_SESSION_NAME} characters")
    if _CONTROL_CHARS.search(name):
        raise ValidationFailure("Board name contains control characters")
    return name


def validate_role(value: Any, *, allow_host: bool = False) -> str:
    if value not in ROLES:
        raise ValidationFailure("Unknown role")
    if value == ROLE_HOST and not allow_host:
        raise ValidationFailure("Host role cannot be requested by a joining client")
    return str(value)


def validate_session_code(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationFailure("Session code must be text")
    code = value.strip().upper()
    if not _CODE_RE.match(code):
        raise ValidationFailure("Session code must be 6 letters/digits")
    return code


def sanitize_text(value: Any, max_length: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationFailure("Text value must be a string")
    text = _CONTROL_CHARS.sub("", value)
    if len(text) > max_length:
        raise ValidationFailure(f"Text exceeds {max_length} characters", "TOO_LARGE")
    return text


def safe_color(value: Any, default: str = DEFAULT_STROKE) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if _HEX_COLOR.match(candidate):
            return candidate.lower()
        if candidate.lower() in _ALLOWED_COLORS:
            return candidate.lower()
    return default


def _validate_point_list(points: Any, *, limit: int = 40_000) -> list[list[float]]:
    if not isinstance(points, list):
        raise ValidationFailure("points must be an array")
    if len(points) > limit:
        raise ValidationFailure("Too many points in one stroke", "TOO_LARGE")
    clean: list[list[float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x = bounded_number(point[0])
        y = bounded_number(point[1])
        clean.append([round(x, 2), round(y, 2)])
    return clean


def _validate_style(raw: Any) -> dict[str, Any]:
    style = raw if isinstance(raw, dict) else {}
    width = bounded_number(style.get("stroke_width", style.get("width", 3)), 3, 0.5, 80)
    opacity = bounded_number(style.get("opacity", 1.0), 1.0, 0.05, 1.0)
    font_size = bounded_int(style.get("font_size", 18), 18, 6, 144)
    font_family = style.get("font_family", "Inter")
    if not isinstance(font_family, str) or len(font_family) > 48:
        font_family = "Inter"
    return {
        "stroke": safe_color(style.get("stroke"), DEFAULT_STROKE),
        "fill": safe_color(style.get("fill"), "none"),
        "stroke_width": round(width, 2),
        "opacity": round(opacity, 2),
        "dashed": bool(style.get("dashed", False)),
        "font_size": font_size,
        "font_family": font_family,
        "font_weight": "bold" if style.get("font_weight") == "bold" else "normal",
        "text_color": safe_color(style.get("text_color"), style.get("stroke", DEFAULT_STROKE)),
        "text_align": style.get("text_align") if style.get("text_align") in ("left", "center", "right") else "left",
    }


def _validate_image(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationFailure("image object requires image data")
    data = raw.get("data")
    if not isinstance(data, str):
        raise ValidationFailure("Image data must be base64 text")
    if data.startswith("data:"):
        # Tolerate a data URL on import, but store only the payload.
        try:
            data = data.split(",", 1)[1]
        except IndexError as exc:
            raise ValidationFailure("Malformed image data URL") from exc
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationFailure("Image is not valid base64") from exc
    if len(decoded) > MAX_IMAGE_BYTES:
        raise ValidationFailure(
            f"Image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB", "TOO_LARGE"
        )
    if decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif decoded.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ValidationFailure("Only PNG and JPEG images are accepted")
    return {
        "mime": mime,
        "data": data,
        "sha256": hashlib.sha256(decoded).hexdigest(),
        "bytes": len(decoded),
    }


def _clip_rect(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    w = max(2.0, min(50_000.0, w))
    h = max(2.0, min(50_000.0, h))
    x = max(-50_000.0, min(50_000.0, x))
    y = max(-50_000.0, min(50_000.0, y))
    return round(x, 2), round(y, 2), round(w, 2), round(h, 2)


def normalize_new_object(
    raw: Any,
    *,
    owner_id: str,
    owner_name: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize a new canvas object from an untrusted client."""
    if not isinstance(raw, dict):
        raise ValidationFailure("Object must be a JSON object")
    kind = raw.get("kind")
    if kind not in OBJECT_KINDS:
        raise ValidationFailure(f"Unsupported object kind: {kind!r}")
    object_id = raw.get("id")
    if object_id is None:
        object_id = new_object_id()
    if not isinstance(object_id, str) or not _SAFE_ID.match(object_id):
        raise ValidationFailure("Invalid object id")

    x, y, w, h = _clip_rect(
        bounded_number(raw.get("x", 0)),
        bounded_number(raw.get("y", 0)),
        bounded_number(raw.get("w", 160), 160),
        bounded_number(raw.get("h", 100), 100),
    )
    text = sanitize_text(raw.get("text", ""))
    style = _validate_style(raw.get("style"))
    points = _validate_point_list(raw.get("points", []))
    if kind == KIND_STROKE and not points:
        raise ValidationFailure("A freehand stroke needs at least one point")
    if kind == KIND_STROKE and len(points) > 40_000:
        raise ValidationFailure("Stroke is too long", "TOO_LARGE")

    image = None
    if kind == KIND_IMAGE:
        image = _validate_image(raw.get("image"))
        w = max(16.0, w)
        h = max(16.0, h)

    connector = None
    if kind == KIND_CONNECTOR:
        source = raw.get("connector") if isinstance(raw.get("connector"), dict) else {}
        from_id = source.get("from_id", raw.get("from_id"))
        to_id = source.get("to_id", raw.get("to_id"))
        if not isinstance(from_id, str) or not isinstance(to_id, str):
            raise ValidationFailure("Connector requires from_id and to_id")
        if not _SAFE_ID.match(from_id) or not _SAFE_ID.match(to_id):
            raise ValidationFailure("Connector endpoints are invalid")
        connector = {
            "from_id": from_id,
            "to_id": to_id,
            "arrow": bool(source.get("arrow", True)),
            "label": sanitize_text(source.get("label", ""), 300),
        }

    parent_id = raw.get("parent_id")
    if parent_id is not None and (not isinstance(parent_id, str) or not _SAFE_ID.match(parent_id)):
        parent_id = None
    group_id = raw.get("group_id")
    if group_id is not None and (not isinstance(group_id, str) or not _SAFE_ID.match(group_id)):
        group_id = None

    z = bounded_int(raw.get("z", 0), 0, -1_000_000, 1_000_000)
    timestamp = now or utc_now_iso()
    normalized: dict[str, Any] = {
        "id": object_id,
        "kind": kind,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "points": points,
        "text": text,
        "style": style,
        "z": z,
        "group_id": group_id,
        "parent_id": parent_id,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "created_at": timestamp,
        "updated_at": timestamp,
        "rev": 1,
        "locked": bool(raw.get("locked", False)),
    }
    if kind in FILLABLE_KINDS:
        if normalized["style"]["fill"] == "none" and kind in {KIND_STICKY}:
            normalized["style"]["fill"] = STICKY_COLORS[0]
        elif normalized["style"]["fill"] == "none":
            normalized["style"]["fill"] = DEFAULT_FILL
    if kind == KIND_STICKY:
        normalized["style"]["fill"] = safe_color(
            raw.get("style", {}).get("fill") if isinstance(raw.get("style"), dict) else None,
            STICKY_COLORS[0],
        )
        if normalized["style"]["fill"] == "none":
            normalized["style"]["fill"] = STICKY_COLORS[0]
    if kind in (KIND_TEXT,):
        normalized["style"]["fill"] = "none"
    if image is not None:
        normalized["image"] = image
    if connector is not None:
        normalized["connector"] = connector
    return normalized


_PATCH_FIELDS = {
    "x", "y", "w", "h", "points", "text", "style", "z", "group_id",
    "parent_id", "locked", "image", "connector", "rotation",
}


def validate_patch(raw: Any) -> dict[str, Any]:
    """Return a sanitized object patch. Identity/ownership fields are immutable."""
    if not isinstance(raw, dict):
        raise ValidationFailure("patch must be an object")
    unknown = set(raw) - _PATCH_FIELDS
    if unknown:
        raise ValidationFailure(f"Patch contains immutable fields: {', '.join(sorted(unknown))}")
    patch: dict[str, Any] = {}
    if "x" in raw:
        patch["x"] = round(bounded_number(raw["x"]), 2)
    if "y" in raw:
        patch["y"] = round(bounded_number(raw["y"]), 2)
    if "w" in raw:
        patch["w"] = round(max(2.0, min(50_000.0, bounded_number(raw["w"], 2))), 2)
    if "h" in raw:
        patch["h"] = round(max(2.0, min(50_000.0, bounded_number(raw["h"], 2))), 2)
    if "points" in raw:
        patch["points"] = _validate_point_list(raw["points"], limit=40_000)
    if "text" in raw:
        patch["text"] = sanitize_text(raw["text"])
    if "style" in raw:
        patch["style"] = _validate_style(raw["style"])
    if "z" in raw:
        patch["z"] = bounded_int(raw["z"], 0, -1_000_000, 1_000_000)
    if "group_id" in raw:
        value = raw["group_id"]
        patch["group_id"] = value if isinstance(value, str) and _SAFE_ID.match(value) else None
    if "parent_id" in raw:
        value = raw["parent_id"]
        patch["parent_id"] = value if isinstance(value, str) and _SAFE_ID.match(value) else None
    if "locked" in raw:
        patch["locked"] = bool(raw["locked"])
    if "rotation" in raw:
        patch["rotation"] = round(bounded_number(raw["rotation"], 0, -360, 360), 2)
    if "image" in raw:
        patch["image"] = _validate_image(raw["image"])
    if "connector" in raw:
        if not isinstance(raw["connector"], dict):
            raise ValidationFailure("connector must be an object")
        source = raw["connector"]
        from_id, to_id = source.get("from_id"), source.get("to_id")
        if not isinstance(from_id, str) or not isinstance(to_id, str):
            raise ValidationFailure("Connector requires endpoints")
        patch["connector"] = {
            "from_id": from_id,
            "to_id": to_id,
            "arrow": bool(source.get("arrow", True)),
            "label": sanitize_text(source.get("label", ""), 300),
        }
    return patch


def merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(target)
    for key, value in patch.items():
        if key == "style" and isinstance(value, dict):
            style = dict(target.get("style", {}))
            style.update(value)
            merged["style"] = style
        else:
            merged[key] = value
    return merged


def clone_object(obj: dict[str, Any]) -> dict[str, Any]:
    """Deep-enough copy for history and snapshots."""
    result = dict(obj)
    result["style"] = dict(obj.get("style", {}))
    result["points"] = [list(point) for point in obj.get("points", [])]
    if "image" in obj:
        result["image"] = dict(obj["image"])
    if "connector" in obj:
        result["connector"] = dict(obj["connector"])
    return result


def bounds_for_object(obj: dict[str, Any]) -> tuple[float, float, float, float]:
    """Approximate axis-aligned bounds, including freehand points."""
    x, y = float(obj.get("x", 0)), float(obj.get("y", 0))
    w, h = float(obj.get("w", 2)), float(obj.get("h", 2))
    if obj.get("points"):
        xs = [p[0] for p in obj["points"]]
        ys = [p[1] for p in obj["points"]]
        return (x + min(xs), y + min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    return x, y, w, h


def make_chat_message(sender_id: str, sender_name: str, text: Any) -> dict[str, Any]:
    message = sanitize_text(text, MAX_CHAT_LENGTH).strip()
    if not message:
        raise ValidationFailure("Chat message is empty")
    if len(message) > MAX_CHAT_LENGTH:
        raise ValidationFailure("Chat message is too long")
    return {
        "id": new_object_id(),
        "sender_id": sender_id,
        "sender": sender_name,
        "text": message,
        "timestamp": utc_now_iso(),
    }


def make_activity(
    kind: str,
    actor_id: str,
    actor_name: str,
    detail: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "id": new_object_id(),
        "kind": kind,
        "actor_id": actor_id,
        "actor": actor_name,
        "detail": sanitize_text(detail, 500),
        "timestamp": timestamp or utc_now_iso(),
    }


def public_participant(participant: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets before sending a participant over the wire."""
    return {
        "id": participant["id"],
        "name": participant["name"],
        "color": participant["color"],
        "role": participant["role"],
        "online": bool(participant.get("online", True)),
        "tool": participant.get("tool", "select"),
        "cursor": participant.get("cursor"),
        "joined_at": participant.get("joined_at"),
        "last_seen": participant.get("last_seen"),
        "typing": bool(participant.get("typing", False)),
    }


def iso_now() -> str:
    return utc_now_iso()


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def generation_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + "23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
