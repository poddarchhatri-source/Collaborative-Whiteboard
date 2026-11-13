"""Newline-delimited JSON framing and message envelope helpers.

Framing
-------
Every protocol message is one UTF-8 JSON object followed by a single ``\\n``
byte.  TCP is a byte stream, so the newline is the record separator.  A line
may not exceed :data:`shared.constants.MAX_MESSAGE_BYTES`; oversized lines are
rejected before JSON parsing.  JSON is used because it is easy to inspect with
standard tools and maps naturally to the application's object model.

The envelope always contains ``v``, ``type`` and ``msg_id``.  Server messages
additionally contain ``server_seq`` where ordering matters.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from shared.constants import MAX_MESSAGE_BYTES, PROTOCOL_VERSION


class ProtocolError(ValueError):
    """Raised when a frame violates the transport contract."""

    def __init__(self, message: str, code: str = "PROTOCOL_ERROR") -> None:
        super().__init__(message)
        self.code = code


class PayloadTooLarge(ProtocolError):
    def __init__(self, message: str = "Message exceeds the maximum frame size") -> None:
        super().__init__(message, "TOO_LARGE")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_message_id() -> str:
    return uuid.uuid4().hex


def make_client_message(
    msg_type: str,
    payload: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    client_seq: int | None = None,
    reply_to: str | None = None,
) -> dict[str, Any]:
    """Build a client -> server envelope."""
    message: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "msg_id": new_message_id(),
        "timestamp": utc_now_iso(),
        "payload": payload or {},
    }
    if session_id:
        message["session_id"] = session_id
    if client_seq is not None:
        message["client_seq"] = int(client_seq)
    if reply_to:
        message["reply_to"] = reply_to
    return message


def make_server_message(
    msg_type: str,
    payload: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    server_seq: int | None = None,
    reply_to: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Build a server -> client envelope."""
    message: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "msg_id": new_message_id(),
        "timestamp": utc_now_iso(),
        "payload": payload or {},
    }
    if session_id:
        message["session_id"] = session_id
    if server_seq is not None:
        message["server_seq"] = int(server_seq)
    if reply_to:
        message["reply_to"] = reply_to
    if event_id:
        message["event_id"] = event_id
    return message


def encode_message(message: dict[str, Any]) -> bytes:
    """Serialize and frame one message. Always includes exactly one newline."""
    if not isinstance(message, dict):
        raise ProtocolError("Message must be a JSON object")
    try:
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"Message is not JSON serializable: {exc}") from exc
    if len(raw) + 1 > MAX_MESSAGE_BYTES:
        raise PayloadTooLarge()
    return raw + b"\n"


def decode_message(line: bytes | str) -> dict[str, Any]:
    """Parse a single framed line and validate the common envelope."""
    if isinstance(line, bytes):
        if len(line) > MAX_MESSAGE_BYTES:
            raise PayloadTooLarge()
        if line.endswith(b"\n"):
            line = line[:-1]
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("Frame is not valid UTF-8") from exc
    else:
        text = line.rstrip("\r\n")
    if not text:
        raise ProtocolError("Empty frame")
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise PayloadTooLarge()
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Malformed JSON at character {exc.pos}") from exc
    validate_envelope(message)
    return message


def validate_envelope(message: Any) -> None:
    if not isinstance(message, dict):
        raise ProtocolError("Top-level JSON value must be an object")
    version = message.get("v")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"Unsupported protocol version: {version!r}")
    msg_type = message.get("type")
    if not isinstance(msg_type, str) or not msg_type or len(msg_type) > 64:
        raise ProtocolError("Missing or invalid message type")
    if not all(c.isupper() or c == "_" for c in msg_type):
        raise ProtocolError("Message types use UPPER_SNAKE_CASE")
    msg_id = message.get("msg_id")
    if not isinstance(msg_id, str) or not (8 <= len(msg_id) <= 80):
        raise ProtocolError("Missing or invalid msg_id")
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be a JSON object")
    if "timestamp" in message and not isinstance(message["timestamp"], str):
        raise ProtocolError("timestamp must be an ISO-8601 string")


class LineFramer:
    """Incremental parser for newline-delimited JSON over a byte stream.

    Feed arbitrary TCP chunks and receive complete decoded messages.  This is
    used by the desktop client's reader thread and by the test harness.
    """

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self._buffer = bytearray()
        self.max_bytes = max_bytes

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        if not data:
            return []
        self._buffer.extend(data)
        if len(self._buffer) > self.max_bytes and b"\n" not in self._buffer:
            self._buffer.clear()
            raise PayloadTooLarge()
        messages: list[dict[str, Any]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            frame = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if not frame.strip():
                continue
            if len(frame) > self.max_bytes:
                raise PayloadTooLarge()
            messages.append(decode_message(frame))
        if len(self._buffer) > self.max_bytes:
            self._buffer.clear()
            raise PayloadTooLarge()
        return messages


def bounded_number(value: Any, default: float = 0.0, minimum: float = -1e7, maximum: float = 1e7) -> float:
    """Return a finite float in range or the default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if not math.isfinite(number):
        return default
    return max(minimum, min(maximum, number))


def bounded_int(value: Any, default: int = 0, minimum: int = -10_000_000, maximum: int = 10_000_000) -> int:
    try:
        if isinstance(value, bool):
            return default
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def now_monotonic() -> float:
    return time.monotonic()


def chunks(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
