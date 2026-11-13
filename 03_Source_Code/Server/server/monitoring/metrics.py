"""Live server metrics for the client dashboard and server control panel."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServerMetrics:
    started_at: float = field(default_factory=time.time)
    total_connections: int = 0
    active_connections: int = 0
    messages_received: int = 0
    messages_sent: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0
    errors: int = 0
    rejected_messages: int = 0
    sessions_created: int = 0
    reconnects: int = 0
    board_events: int = 0
    peak_clients: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    recent_events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=100), repr=False
    )

    def event(self, kind: str, message: str, **extra: Any) -> None:
        with self._lock:
            self.recent_events.append(
                {
                    "kind": kind,
                    "message": message,
                    "timestamp": time.time(),
                    **extra,
                }
            )

    def incr(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, field_name, int(getattr(self, field_name)) + amount)

    def connection_opened(self) -> None:
        with self._lock:
            self.total_connections += 1
            self.active_connections += 1
            self.peak_clients = max(self.peak_clients, self.active_connections)

    def connection_closed(self) -> None:
        with self._lock:
            self.active_connections = max(0, self.active_connections - 1)

    def add_bytes(self, sent: int = 0, received: int = 0) -> None:
        with self._lock:
            self.bytes_sent += max(0, sent)
            self.bytes_received += max(0, received)

    def snapshot(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            data = {
                "server_time": now,
                "uptime_seconds": round(now - self.started_at, 2),
                "total_connections": self.total_connections,
                "active_connections": self.active_connections,
                "messages_received": self.messages_received,
                "messages_sent": self.messages_sent,
                "bytes_received": self.bytes_received,
                "bytes_sent": self.bytes_sent,
                "errors": self.errors,
                "rejected_messages": self.rejected_messages,
                "sessions_created": self.sessions_created,
                "reconnects": self.reconnects,
                "board_events": self.board_events,
                "peak_clients": self.peak_clients,
                "recent_events": list(self.recent_events)[-30:],
            }
        if extra:
            data.update(extra)
        return data
