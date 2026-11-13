"""Client network transport and connection lifecycle.

The socket lives on a QThread; the GUI only sees Qt signals.  A manager object
on the GUI thread performs the handshake, heartbeats, automatic reconnection,
offline outbox replay and measured network statistics.
"""

from __future__ import annotations

import json
import queue
import random
import select
import socket
import threading
import time
from collections import deque
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from shared.constants import (
    ACK,
    ACTIVITY,
    CANVAS_STATE,
    CHAT_MESSAGE,
    CREATE_SESSION,
    CURSOR,
    ERROR,
    EVENT,
    GET_SERVER_STATS,
    HEARTBEAT_INTERVAL,
    JOIN_SESSION,
    KICKED,
    LASER_POINT,
    LEFT,
    MAX_PENDING_OUTBOX,
    MAX_RECONNECT_BACKOFF,
    PING,
    PONG,
    PRESENCE,
    PRESENTATION_STATE,
    PROTOCOL_VERSION,
    REQUEST_SYNC,
    RESUME_SESSION,
    SERVER_STATS,
    SNAPSHOT_LIST_RESULT,
    SNAPSHOT_PREVIEW,
    SNAPSHOT_SAVED,
    SYNC_COMPLETE,
    SYSTEM,
    VERSION_RESTORED,
    WELCOME,
)
from shared.protocol import (
    LineFramer,
    ProtocolError,
    encode_message,
    make_client_message,
    utc_now_iso,
)

# Message types for which the server returns an ACK.
_ACKED_TYPES = {
    "CREATE_SESSION", "JOIN_SESSION", "RESUME_SESSION", "CREATE_OBJECT",
    "UPDATE_OBJECT", "DELETE_OBJECTS", "STROKE_APPEND", "STROKE_END", "UNDO",
    "REDO", "CLEAR_CANVAS", "CHAT", "SET_ROLE", "KICK_USER", "SNAPSHOT_CREATE",
    "SNAPSHOT_LIST", "SNAPSHOT_GET", "SNAPSHOT_RESTORE", "PRESENTATION_START",
    "PRESENTATION_UPDATE", "PRESENTATION_STOP", "SET_BOARD_LOCK", "BOARD_IMPORT",
    "REQUEST_SYNC", "GET_SERVER_STATS", "SET_PROFILE",
}


class NetworkWorker(QThread):
    connected = Signal()
    message_received = Signal(dict)
    connection_lost = Signal(str)
    bytes_counted = Signal(int, int)  # sent, received
    transport_error = Signal(str)

    def __init__(self, host: str, port: int, timeout: float = 5.0, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.port = port
        self.timeout = timeout
        self._outgoing: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4000)
        self._sock: socket.socket | None = None
        self._stopping = threading.Event()
        self._closed = False

    def send_message(self, message: dict[str, Any]) -> bool:
        if self._closed:
            return False
        try:
            self._outgoing.put_nowait(message)
            return True
        except queue.Full:
            self.transport_error.emit("Outbound network queue is full")
            return False

    def _close_current_socket(self) -> None:
        with self._safe_socket() as sock:
            if sock is None:
                return
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stopping.set()
        self._close_current_socket()

    def force_disconnect(self) -> None:
        """Simulate a network failure without marking this as a manual stop."""
        self._close_current_socket()

    def _safe_socket(self):
        class _Ctx:
            def __init__(self, sock):
                self.sock = sock

            def __enter__(self):
                return self.sock

            def __exit__(self, *args):
                return False

        return _Ctx(self._sock) if self._sock is not None else _Ctx(None)

    def run(self) -> None:
        reason = "Disconnected"
        framer = LineFramer()
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(0.3)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            self.connected.emit()
            while not self._stopping.is_set():
                # Flush the GUI-to-network queue.
                for _ in range(200):
                    try:
                        message = self._outgoing.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        raw = encode_message(message)
                    except ProtocolError as exc:
                        self.transport_error.emit(str(exc))
                        continue
                    sock.sendall(raw)
                    self.bytes_counted.emit(len(raw), 0)
                try:
                    ready, _, _ = select.select([sock], [], [], 0.005)
                except (OSError, ValueError):
                    break
                if ready:
                    try:
                        data = sock.recv(262_144)
                    except socket.timeout:
                        continue
                    except OSError as exc:
                        reason = str(exc)
                        break
                    if not data:
                        reason = "Server closed the connection"
                        break
                    self.bytes_counted.emit(0, len(data))
                    try:
                        messages = framer.feed(data)
                    except ProtocolError as exc:
                        self.transport_error.emit(str(exc))
                        reason = "Protocol framing error"
                        break
                    for message in messages:
                        self.message_received.emit(message)
        except (OSError, ValueError) as exc:
            reason = str(exc) or exc.__class__.__name__
        except Exception as exc:  # defensive: never kill the GUI
            reason = f"Network thread error: {exc}"
        finally:
            self._closed = True
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._sock = None
            if not self._stopping.is_set():
                self.connection_lost.emit(reason)


class ConnectionManager(QObject):
    state_changed = Signal(str, str)
    welcome = Signal(dict)
    event = Signal(dict)
    presence = Signal(list)
    chat = Signal(dict)
    activity = Signal(dict)
    canvas_state = Signal(dict)
    sync_complete = Signal(dict)
    ack = Signal(dict)
    protocol_error = Signal(dict)
    server_stats = Signal(dict)
    snapshot_list = Signal(list)
    snapshot_preview = Signal(dict)
    snapshot_saved = Signal(dict)
    version_restored = Signal(dict)
    presentation = Signal(dict)
    cursor = Signal(dict)
    laser = Signal(dict)
    kicked = Signal(str)
    system = Signal(dict)
    left = Signal()
    latency = Signal(float)
    metrics_changed = Signal(dict)
    outbox_changed = Signal(int)
    toast = Signal(str, str)
    reconnect_scheduled = Signal(int, float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = "disconnected"
        self.host = ""
        self.port = 0
        self.session_id = ""
        self.session_code = ""
        self.session_name = ""
        self.self_participant: dict[str, Any] = {}
        self.participants: list[dict[str, Any]] = []
        self.session_info: dict[str, Any] = {}
        self.token = ""
        self._worker: NetworkWorker | None = None
        self._mode = ""
        self._request_payload: dict[str, Any] = {}
        self._has_state = False
        self._last_seq = 0
        self._auto_reconnect = False
        self._manual_stop = False
        self._kicked = False
        self._resume_ready = False
        self._reconnect_attempt = 0
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)
        self._last_rx = time.monotonic()
        self._last_ping = 0.0
        self._pings: dict[float, float] = {}
        self._pending: dict[str, tuple[float, str]] = {}
        self._pending_lock = threading.Lock()
        self._outbox: list[dict[str, Any]] = []
        self._client_seq = 0
        self._connected_since = 0.0
        self._bytes_sent = 0
        self._bytes_recv = 0
        self._messages_sent = 0
        self._messages_recv = 0
        self._reconnect_count = 0
        self._rtt_samples: deque[float] = deque(maxlen=120)
        self._ack_rtt_samples: deque[float] = deque(maxlen=120)
        self._last_sync_ms = 0.0
        self._last_ack_ms = 0.0
        self._server_uptime = 0.0
        self._latest_stats: dict[str, Any] = {}
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=80)
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(self._heartbeat_tick)
        self._heartbeat_timer.start()
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(1000)
        self._metrics_timer.timeout.connect(self._emit_metrics)
        self._metrics_timer.start()
        self._network_event("system", "Network manager ready")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def connect_for_auth(
        self,
        host: str,
        port: int,
        request_type: str,
        payload: dict[str, Any],
        *,
        resume_token: str = "",
    ) -> None:
        self._stop_worker()
        self.host = host.strip()
        self.port = int(port)
        self._mode = request_type
        self._request_payload = dict(payload)
        self._has_state = False
        self._last_seq = 0
        self._auto_reconnect = bool(resume_token)
        self._manual_stop = False
        self._kicked = False
        self._resume_ready = False
        self._reconnect_attempt = 0
        self._connected_since = 0.0
        self.token = resume_token
        self.session_code = str(payload.get("code", "")).upper()
        self.session_id = ""
        self._reconnect_timer.stop()
        self._set_state("connecting", f"Connecting to {self.host}:{self.port}...")
        self._network_event("connect", f"Connecting to {self.host}:{self.port}")
        self._start_worker()

    def reconnect_now(self) -> None:
        if self.state in ("reconnecting", "disconnected") and self.token:
            self._reconnect_timer.stop()
            self._attempt_reconnect()

    def send(self, msg_type: str, payload: dict[str, Any] | None = None, *, queue_if_offline: bool = True) -> str:
        message = make_client_message(
            msg_type,
            payload or {},
            session_id=self.session_id or None,
            client_seq=self._next_seq(),
        )
        if self.state == "connected" and self._resume_ready and self._worker is not None:
            sent = self._transmit(message)
            if not sent and queue_if_offline and self.session_id:
                self._enqueue_outbox(message)
        elif queue_if_offline and self.session_id and self._auto_reconnect:
            self._enqueue_outbox(message)
        elif self.state in ("connecting", "authenticating"):
            # UI commands should not be lost during the initial handshake.
            self._enqueue_outbox(message)
        return message["msg_id"]

    def request_server_stats(self) -> None:
        if self.state == "connected":
            self.send(GET_SERVER_STATS, {})

    def request_sync(self) -> None:
        if self.state == "connected":
            self.send(REQUEST_SYNC, {"since_seq": self._last_seq}, queue_if_offline=False)

    def request_snapshot_list(self) -> None:
        self.send("SNAPSHOT_LIST", {})

    def request_snapshot(self, snapshot_id: str) -> None:
        self.send("SNAPSHOT_GET", {"snapshot_id": snapshot_id})

    def leave_session(self) -> None:
        self._manual_stop = True
        self._auto_reconnect = False
        if self.state == "connected" and self._worker:
            try:
                self._transmit(make_client_message("LEAVE_SESSION", {}, session_id=self.session_id))
            except Exception:
                pass
        QTimer.singleShot(120, self._finish_manual_disconnect)

    def disconnect(self) -> None:
        self._manual_stop = True
        self._auto_reconnect = False
        self._reconnect_timer.stop()
        self._stop_worker()
        self._set_state("disconnected", "Disconnected")
        self.left.emit()

    def shutdown(self) -> None:
        self._heartbeat_timer.stop()
        self._metrics_timer.stop()
        self._reconnect_timer.stop()
        self._manual_stop = True
        self._auto_reconnect = False
        self._stop_worker()

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def _start_worker(self) -> None:
        self._stop_worker()
        self._last_rx = time.monotonic()
        worker = NetworkWorker(self.host, self.port, timeout=5.0)
        self._worker = worker
        worker.connected.connect(lambda w=worker: self._on_worker_connected(w))
        worker.message_received.connect(self._on_message)
        worker.connection_lost.connect(lambda reason, w=worker: self._on_worker_lost(w, reason))
        worker.bytes_counted.connect(self._on_bytes)
        worker.transport_error.connect(lambda msg: self.toast.emit(msg, "error"))
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        worker.start()

    def _stop_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop()
            if worker.isRunning():
                worker.wait(1500)
            worker.deleteLater()

    def _on_worker_finished(self, worker: NetworkWorker) -> None:
        # Do not clear ``_worker`` here: ``finished`` can be delivered before
        # ``connection_lost``. Clearing early would make the loss handler treat
        # the event as stale and skip automatic reconnection.
        worker.deleteLater()

    def _on_worker_connected(self, worker: NetworkWorker) -> None:
        if worker is not self._worker:
            return
        self._reconnect_attempt = 0
        self._last_rx = time.monotonic()
        self._set_state("authenticating", "Authenticating...")
        self._network_event("connect", "TCP connection established")
        if self._mode == RESUME_SESSION:
            payload = {
                "code": self.session_code,
                "token": self.token,
                "last_seq": self._last_seq,
                "has_state": self._has_state,
            }
        else:
            payload = dict(self._request_payload)
        message = make_client_message(self._mode, payload, client_seq=self._next_seq())
        self._transmit(message, pending=False)

    def _on_worker_lost(self, worker: NetworkWorker, reason: str) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        worker.deleteLater()
        self._network_event("disconnect", reason)
        if self._manual_stop or self._kicked:
            self._set_state("disconnected", "Disconnected")
            return
        if not self._auto_reconnect:
            self._set_state("disconnected", reason or "Connection lost")
            return
        self._schedule_reconnect(reason)

    def _schedule_reconnect(self, reason: str) -> None:
        if self._reconnect_timer.isActive():
            return
        self._reconnect_count += 1
        self._reconnect_attempt += 1
        delay = min(MAX_RECONNECT_BACKOFF, 0.8 * (2 ** min(self._reconnect_attempt - 1, 4)))
        delay += random.uniform(0, 0.35)
        self._set_state(
            "reconnecting",
            f"Connection lost. Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempt})",
        )
        self._reconnect_timer.start(int(delay * 1000))
        self.reconnect_scheduled.emit(self._reconnect_attempt, delay)
        self._network_event("reconnect", f"Scheduled reconnect in {delay:.1f}s: {reason}")

    def _attempt_reconnect(self) -> None:
        if not self.token or self._manual_stop or self._kicked:
            return
        self._mode = RESUME_SESSION
        self._has_state = True
        self._resume_ready = False
        self._set_state("reconnecting", "Reconnecting...")
        self._start_worker()

    def _finish_manual_disconnect(self) -> None:
        self._stop_worker()
        self._set_state("disconnected", "Left the session")
        self.left.emit()

    # ------------------------------------------------------------------
    # Incoming messages
    # ------------------------------------------------------------------
    def _on_message(self, message: dict[str, Any]) -> None:
        self._last_rx = time.monotonic()
        self._messages_recv += 1
        msg_type = message.get("type")
        payload = message.get("payload", {}) or {}
        if msg_type == PING:
            # Reply immediately; heartbeats must not wait in the offline queue.
            try:
                self._transmit(make_client_message(PONG, {"t": payload.get("t")}), pending=False)
            except Exception:
                pass
            return
        if msg_type == PONG:
            token = payload.get("t")
            started = self._pings.pop(token, None) if isinstance(token, (int, float)) else None
            if started:
                rtt = max(0.0, (time.monotonic() - started) * 1000.0)
                self._record_rtt(rtt)
            return
        if msg_type == WELCOME:
            self._handle_welcome(payload)
            return
        if msg_type == EVENT:
            seq = message.get("server_seq")
            if isinstance(seq, int):
                self._last_seq = max(self._last_seq, seq)
            self.event.emit(message)
            return
        if msg_type == ACK:
            self._handle_ack(message)
            return
        if msg_type == ERROR:
            error = {
                "code": payload.get("code", "ERROR"),
                "message": payload.get("message", "Server error"),
                "details": payload.get("details", {}),
                "reply_to": message.get("reply_to", ""),
            }
            self._network_event("error", f"{error['code']}: {error['message']}")
            self.protocol_error.emit(error)
            if not self.session_id and error["code"] in {
                "AUTH_REQUIRED", "WRONG_PASSWORD", "SESSION_NOT_FOUND", "NAME_TAKEN",
                "ROOM_FULL", "BAD_REQUEST", "TOO_LARGE", "UNAUTHORIZED",
            }:
                self._auto_reconnect = False
                self._manual_stop = True
                self._stop_worker()
                self._set_state("disconnected", error["message"])
            return
        if msg_type == PRESENCE:
            participants = payload.get("participants", [])
            if isinstance(participants, list):
                self.participants = participants
                mine = next(
                    (p for p in participants if p.get("id") == self.self_participant.get("id")),
                    None,
                )
                if mine:
                    self.self_participant.update(mine)
                self.presence.emit(participants)
            return
        if msg_type == CHAT_MESSAGE:
            chat = payload.get("message")
            if isinstance(chat, dict):
                self.chat.emit(chat)
            return
        if msg_type == ACTIVITY:
            entry = payload.get("activity")
            if isinstance(entry, dict):
                self.activity.emit(entry)
            return
        if msg_type == CURSOR:
            self.cursor.emit(payload)
            return
        if msg_type == LASER_POINT:
            self.laser.emit(payload)
            return
        if msg_type == PRESENTATION_STATE:
            self.presentation.emit(payload)
            return
        if msg_type == CANVAS_STATE:
            if isinstance(message.get("server_seq"), int):
                self._last_seq = max(self._last_seq, message["server_seq"])
            self.canvas_state.emit(payload)
            return
        if msg_type == SYNC_COMPLETE:
            if isinstance(payload.get("seq"), int):
                self._last_seq = max(self._last_seq, payload["seq"])
            self._resume_ready = True
            self._last_sync_ms = 0.0
            self.sync_complete.emit(payload)
            self._network_event("sync", f"Synchronization complete ({payload.get('mode', 'full')})")
            self._flush_outbox()
            return
        if msg_type == SERVER_STATS:
            self._latest_stats = payload
            if isinstance(payload.get("uptime_seconds"), (int, float)):
                self._server_uptime = float(payload["uptime_seconds"])
            self.server_stats.emit(payload)
            return
        if msg_type == SNAPSHOT_LIST_RESULT:
            self.snapshot_list.emit(payload.get("snapshots", []))
            return
        if msg_type == SNAPSHOT_PREVIEW:
            self.snapshot_preview.emit(payload)
            return
        if msg_type == SNAPSHOT_SAVED:
            snap = payload.get("snapshot", {})
            self.snapshot_saved.emit(snap)
            self.toast.emit(f"Version '{snap.get('name', 'snapshot')}' saved", "success")
            return
        if msg_type == VERSION_RESTORED:
            self.version_restored.emit(payload)
            self.toast.emit(f"Restored version: {payload.get('name', '')}", "success")
            return
        if msg_type == KICKED:
            self._kicked = True
            self._auto_reconnect = False
            self._manual_stop = True
            self._set_state("disconnected", payload.get("reason", "Removed by host"))
            self.kicked.emit(payload.get("reason", "Removed by host"))
            self._stop_worker()
            return
        if msg_type == LEFT:
            self._auto_reconnect = False
            self._manual_stop = True
            self._set_state("disconnected", "Left the session")
            self.left.emit()
            return
        if msg_type == SYSTEM:
            self.system.emit(payload)
            return
        if message.get("session_id") is None and msg_type not in ("ERROR", "SYSTEM"):
            return

    def _handle_welcome(self, payload: dict[str, Any]) -> None:
        self.session_info = payload.get("session", {}) or {}
        self.self_participant = payload.get("self", {}) or {}
        self.participants = payload.get("participants", []) or []
        self.session_id = self.session_info.get("id", "")
        self.session_code = self.session_info.get("code", self.session_code)
        self.session_name = self.session_info.get("name", "")
        self.token = payload.get("token", self.token)
        seq = payload.get("seq")
        if isinstance(seq, int):
            self._last_seq = max(self._last_seq, seq)
        self._auto_reconnect = True
        self._manual_stop = False
        self._kicked = False
        self._has_state = True
        self._connected_since = time.monotonic()
        stats = payload.get("stats", {})
        if isinstance(stats, dict) and isinstance(stats.get("uptime_seconds"), (int, float)):
            self._server_uptime = float(stats["uptime_seconds"])
        sync_mode = payload.get("sync_mode", "full")
        if sync_mode == "full":
            self._resume_ready = True
            self._set_state("connected", f"Connected to {self.host}:{self.port}")
            self.welcome.emit(payload)
            self._network_event("session", f"Joined {self.session_name} ({self.session_code})")
            QTimer.singleShot(20, self._flush_outbox)
        else:
            self._resume_ready = False
            self._set_state("connected", "Connected - applying missed updates...")
            self.welcome.emit(payload)
            self._network_event("sync", "Reconnected; waiting for missed events")
        QTimer.singleShot(100, self.request_server_stats)

    def _handle_ack(self, message: dict[str, Any]) -> None:
        reply_to = message.get("reply_to", "")
        now = time.monotonic()
        with self._pending_lock:
            pending = self._pending.pop(reply_to, None)
        if pending:
            elapsed_ms = max(0.0, (now - pending[0]) * 1000.0)
            self._last_ack_ms = elapsed_ms
            self._last_sync_ms = elapsed_ms
            self._ack_rtt_samples.append(elapsed_ms)
        self.ack.emit(
            {
                "reply_to": reply_to,
                "event_id": message.get("event_id"),
                "server_seq": message.get("server_seq"),
                "payload": message.get("payload", {}),
                "latency_ms": self._last_ack_ms,
            }
        )

    # ------------------------------------------------------------------
    # Outbound / outbox
    # ------------------------------------------------------------------
    def _transmit(self, message: dict[str, Any], *, pending: bool = True) -> bool:
        worker = self._worker
        if worker is None:
            return False
        ok = worker.send_message(message)
        if ok:
            self._messages_sent += 1
            msg_type = message.get("type", "")
            if pending and msg_type in _ACKED_TYPES:
                with self._pending_lock:
                    self._pending[message["msg_id"]] = (time.monotonic(), msg_type)
                    if len(self._pending) > 800:
                        oldest = sorted(self._pending.items(), key=lambda kv: kv[1][0])[:200]
                        for key, _ in oldest:
                            self._pending.pop(key, None)
        return ok

    def _enqueue_outbox(self, message: dict[str, Any]) -> None:
        if len(self._outbox) >= MAX_PENDING_OUTBOX:
            self.toast.emit("Offline operation queue is full; reconnect to sync", "error")
            return
        self._outbox.append(message)
        self.outbox_changed.emit(len(self._outbox))
        self._network_event("offline", "Edit queued while offline")
        if len(self._outbox) == 1:
            self.toast.emit("Offline edits are being queued", "warning")

    def _flush_outbox(self) -> None:
        if not self._resume_ready or self.state != "connected":
            return
        pending = self._outbox
        self._outbox = []
        if not pending:
            self.outbox_changed.emit(0)
            return
        self._network_event("sync", f"Replaying {len(pending)} offline operation(s)")
        for message in pending:
            self._transmit(message)
        self.outbox_changed.emit(0)
        self.toast.emit(
            f"Replayed {len(pending)} offline operation{'s' if len(pending) != 1 else ''}",
            "info",
        )

    # ------------------------------------------------------------------
    # Heartbeat / metrics
    # ------------------------------------------------------------------
    def _heartbeat_tick(self) -> None:
        if self.state != "connected":
            return
        now = time.monotonic()
        if now - self._last_ping >= HEARTBEAT_INTERVAL / 2:
            self._last_ping = now
            token = time.monotonic()
            self._pings[token] = token
            if len(self._pings) > 30:
                self._pings.clear()
            try:
                self._transmit(make_client_message(PING, {"t": token}), pending=False)
            except Exception:
                pass
        if now - self._last_rx > 24.0 and self._worker is not None:
            self._network_event("heartbeat", "Heartbeat watchdog expired")
            worker = self._worker
            # Do not use stop(): that suppresses connection_lost.  Forcing the
            # socket closed makes the worker emit the loss and trigger backoff.
            worker.force_disconnect()

    def _record_rtt(self, milliseconds: float) -> None:
        if 0 <= milliseconds < 60_000:
            self._rtt_samples.append(milliseconds)
            self.latency.emit(milliseconds)

    def _on_bytes(self, sent: int, received: int) -> None:
        self._bytes_sent += max(0, sent)
        self._bytes_recv += max(0, received)

    def _emit_metrics(self) -> None:
        self.metrics_changed.emit(self.metrics_snapshot())

    def recent_rtts(self) -> list[float]:
        return list(self._rtt_samples)

    def metrics_snapshot(self) -> dict[str, Any]:
        avg = sum(self._rtt_samples) / len(self._rtt_samples) if self._rtt_samples else 0.0
        rtts = sorted(self._rtt_samples)
        p95 = rtts[min(len(rtts) - 1, int(len(rtts) * 0.95))] if rtts else 0.0
        return {
            "state": self.state,
            "host": self.host,
            "port": self.port,
            "session_code": self.session_code,
            "session_name": self.session_name,
            "bytes_sent": self._bytes_sent,
            "bytes_received": self._bytes_recv,
            "messages_sent": self._messages_sent,
            "messages_received": self._messages_recv,
            "rtt_ms": avg,
            "rtt_p95_ms": p95,
            "last_ack_ms": self._last_ack_ms,
            "last_sync_ms": self._last_sync_ms,
            "reconnect_count": self._reconnect_count,
            "outbox": len(self._outbox),
            "connected_seconds": (
                time.monotonic() - self._connected_since if self._connected_since else 0.0
            ),
            "server_uptime": self._server_uptime,
            "last_seq": self._last_seq,
            "recent_events": list(self._recent_events),
            "server": self._latest_stats,
            "participant": self.self_participant,
        }

    def _network_event(self, kind: str, message: str) -> None:
        self._recent_events.append(
            {"kind": kind, "message": message, "timestamp": utc_now_iso(), "at": time.time()}
        )

    # ------------------------------------------------------------------
    def _set_state(self, state: str, detail: str) -> None:
        if state != self.state or detail:
            self.state = state
            self.state_changed.emit(state, detail)
            self._network_event(state, detail)

    def _next_seq(self) -> int:
        self._client_seq += 1
        return self._client_seq
