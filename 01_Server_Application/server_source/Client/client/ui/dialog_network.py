"""Live network monitoring dashboard backed by real socket measurements."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import icon, pixmap
from client.ui.widgets import Sparkline, StatCard


def format_bytes(value: float) -> str:
    value = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class NetworkDashboardDialog(QDialog):
    def __init__(self, manager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Network monitoring")
        self.setModal(False)
        self.resize(1000, 760)
        self._latency_history: deque[float] = deque(maxlen=80)
        self._last_bytes = 0
        self._last_bytes_at = time.monotonic()
        self._throughput_history: deque[float] = deque(maxlen=80)
        self._stats_request = QTimer(self)
        self._stats_request.setInterval(3000)
        self._stats_request.timeout.connect(self.manager.request_server_stats)
        self._stats_request.start()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("network", "#f4f4f5", 26))
        header.addWidget(icon_label)
        title = QLabel("Network monitoring")
        title.setProperty("role", "h2")
        header.addWidget(title)
        self.live_badge = QLabel("LIVE · TCP")
        self.live_badge.setProperty("role", "successBadge")
        header.addWidget(self.live_badge)
        header.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("ghostButton")
        refresh.setIcon(icon("wifi", "#a1a1aa", 16))
        refresh.clicked.connect(self.manager.request_server_stats)
        header.addWidget(refresh)
        outer.addLayout(header)
        subtitle = QLabel(
            "Measurements below are collected from the active TCP socket. Latency is measured with "
            "application heartbeat and ACK round-trips; throughput is derived from actual socket bytes."
        )
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        cards = QGridLayout()
        cards.setSpacing(10)
        self.rtt_card = StatCard("Round-trip latency", "—", "clock", "#f4f4f5")
        self.sync_card = StatCard("Last sync delay", "—", "activity", "#e4e4e7")
        self.tx_card = StatCard("Data transmitted", "—", "export", "#d4d4d8")
        self.rx_card = StatCard("Data received", "—", "import", "#a1a1aa")
        self.msg_card = StatCard("Messages sent / received", "—", "chat", "#e4e4e7")
        self.reconnect_card = StatCard("Reconnections", "0", "redo", "#ef4444")
        self.clients_card = StatCard("Connected clients", "—", "users", "#f4f4f5")
        self.uptime_card = StatCard("Server uptime", "—", "clock", "#a1a1aa")
        for index, card in enumerate(
            [self.rtt_card, self.sync_card, self.tx_card, self.rx_card,
             self.msg_card, self.reconnect_card, self.clients_card, self.uptime_card]
        ):
            cards.addWidget(card, index // 4, index % 4)
        layout.addLayout(cards)

        chart_row = QHBoxLayout()
        chart_row.setSpacing(12)
        latency_frame = QFrame()
        latency_frame.setProperty("role", "card")
        lf = QVBoxLayout(latency_frame)
        lf.setContentsMargins(14, 12, 14, 12)
        lf.setSpacing(5)
        lf.addWidget(QLabel("Round-trip latency (ms)"))
        self.latency_spark = Sparkline()
        lf.addWidget(self.latency_spark)
        self.latency_caption = QLabel("Waiting for samples…")
        self.latency_caption.setProperty("role", "faint")
        lf.addWidget(self.latency_caption)
        chart_row.addWidget(latency_frame, 1)

        throughput_frame = QFrame()
        throughput_frame.setProperty("role", "card")
        tf = QVBoxLayout(throughput_frame)
        tf.setContentsMargins(14, 12, 14, 12)
        tf.setSpacing(5)
        tf.addWidget(QLabel("Application throughput (KB/s)"))
        self.throughput_spark = Sparkline()
        self.throughput_spark.color = "#a1a1aa"
        tf.addWidget(self.throughput_spark)
        self.throughput_caption = QLabel("Waiting for samples…")
        self.throughput_caption.setProperty("role", "faint")
        tf.addWidget(self.throughput_caption)
        chart_row.addWidget(throughput_frame, 1)
        layout.addLayout(chart_row)

        info_frame = QFrame()
        info_frame.setProperty("role", "card")
        info_layout = QGridLayout(info_frame)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setHorizontalSpacing(26)
        info_layout.setVerticalSpacing(7)
        self.info_labels: dict[str, QLabel] = {}
        fields = [
            ("Connection", "connection"),
            ("Server endpoint", "endpoint"),
            ("Transport", "transport"),
            ("Session", "session"),
            ("Active sessions", "sessions"),
            ("Online clients", "online"),
            ("Protocol", "protocol"),
            ("Sequence number", "seq"),
            ("Offline outbox", "outbox"),
            ("Peak clients", "peak"),
        ]
        for row, (label, key) in enumerate(fields):
            title_label = QLabel(label)
            title_label.setProperty("role", "faint")
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_labels[key] = value
            info_layout.addWidget(title_label, row // 2, (row % 2) * 2)
            info_layout.addWidget(value, row // 2, (row % 2) * 2 + 1)
        layout.addWidget(info_frame)

        events_frame = QFrame()
        events_frame.setProperty("role", "card")
        events_layout = QVBoxLayout(events_frame)
        events_layout.setContentsMargins(14, 12, 14, 14)
        events_layout.setSpacing(7)
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Recent network events"))
        header_row.addStretch(1)
        self.event_count = QLabel("0")
        self.event_count.setProperty("role", "faint")
        header_row.addWidget(self.event_count)
        events_layout.addLayout(header_row)
        self.events_table = QTableWidget(0, 3)
        self.events_table.setHorizontalHeaderLabels(["Time", "Event", "Detail"])
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.horizontalHeader().setStretchLastSection(True)
        self.events_table.setColumnWidth(0, 96)
        self.events_table.setColumnWidth(1, 130)
        self.events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.events_table.setMaximumHeight(230)
        events_layout.addWidget(self.events_table)
        layout.addWidget(events_frame)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        note = QLabel(
            "Round-trip latency includes JSON serialization, TCP round-trip and server dispatch. "
            "All values are measured, not simulated."
        )
        note.setProperty("role", "faint")
        note.setWordWrap(True)
        outer.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        outer.addWidget(buttons)

        self.manager.metrics_changed.connect(self._update_metrics)
        self.manager.latency.connect(self._add_latency)
        self.manager.server_stats.connect(self._update_server_stats)
        for sample in self.manager.recent_rtts():
            self._add_latency(sample)
        self._update_metrics(self.manager.metrics_snapshot())

    def _update_metrics(self, metrics: dict[str, Any]) -> None:
        state = metrics.get("state", "disconnected")
        state_text = {
            "connected": "Connected",
            "connecting": "Connecting",
            "authenticating": "Authenticating",
            "reconnecting": "Reconnecting",
            "disconnected": "Disconnected",
        }.get(state, state.title())
        state_color = {
            "connected": "successBadge",
            "reconnecting": "warningBadge",
            "connecting": "warningBadge",
            "authenticating": "warningBadge",
        }.get(state, "errorBadge")
        self.live_badge.setProperty("role", state_color)
        self.live_badge.setText(f"{state_text.upper()} · TCP")
        self.live_badge.style().unpolish(self.live_badge)
        self.live_badge.style().polish(self.live_badge)
        rtt = float(metrics.get("rtt_ms", 0.0))
        if rtt > 0:
            self.rtt_card.set_value(f"{rtt:.1f} ms")
        else:
            self.rtt_card.set_value("—")
        self.sync_card.set_value(f"{float(metrics.get('last_ack_ms', 0)):.1f} ms")
        self.tx_card.set_value(format_bytes(metrics.get("bytes_sent", 0)))
        self.rx_card.set_value(format_bytes(metrics.get("bytes_received", 0)))
        self.msg_card.set_value(
            f"{int(metrics.get('messages_sent', 0)):,} / {int(metrics.get('messages_received', 0)):,}"
        )
        self.reconnect_card.set_value(str(metrics.get("reconnect_count", 0)))
        server = metrics.get("server") or {}
        online = server.get("online_clients")
        self.clients_card.set_value(str(online) if online is not None else "—")
        uptime = metrics.get("server_uptime", 0) or server.get("uptime_seconds", 0)
        self.uptime_card.set_value(format_duration(uptime))
        self.info_labels["connection"].setText(state_text)
        self.info_labels["endpoint"].setText(f"{metrics.get('host', '')}:{metrics.get('port', '')}")
        self.info_labels["transport"].setText("TCP/IPv4 · newline-delimited JSON")
        self.info_labels["session"].setText(
            f"{metrics.get('session_name', '—')} ({metrics.get('session_code', '—')})"
        )
        self.info_labels["sessions"].setText(str(server.get("active_sessions", "—")))
        self.info_labels["online"].setText(str(server.get("online_clients", "—")))
        self.info_labels["protocol"].setText("SynCanvas protocol v1")
        self.info_labels["seq"].setText(str(metrics.get("last_seq", 0)))
        self.info_labels["outbox"].setText(str(metrics.get("outbox", 0)))
        self.info_labels["peak"].setText(str(server.get("peak_clients", "—")))
        total_bytes = int(metrics.get("bytes_sent", 0)) + int(metrics.get("bytes_received", 0))
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_bytes_at)
        if self._last_bytes and total_bytes >= self._last_bytes:
            bps = (total_bytes - self._last_bytes) / elapsed
            self._throughput_history.append(bps / 1024.0)
            self.throughput_spark.set_values(list(self._throughput_history), "#a1a1aa")
            self.throughput_caption.setText(
                f"current {self._throughput_history[-1]:.1f} KB/s · total {format_bytes(total_bytes)}"
            )
        self._last_bytes = total_bytes
        self._last_bytes_at = now
        self._update_events(metrics.get("recent_events", []))

    def _add_latency(self, value: float) -> None:
        if value <= 0:
            return
        if self._latency_history and abs(self._latency_history[-1] - value) < 0.05 and len(self._latency_history) % 3:
            return
        self._latency_history.append(value)
        values = list(self._latency_history)
        self.latency_spark.set_values(values, "#f4f4f5")
        self.latency_caption.setText(
            f"last {value:.1f} ms · avg {sum(values) / len(values):.1f} ms · "
            f"peak {max(values):.1f} ms · {len(values)} samples"
        )

    def _update_server_stats(self, payload: dict[str, Any]) -> None:
        self.clients_card.set_value(str(payload.get("online_clients", "—")))
        self.uptime_card.set_value(format_duration(payload.get("uptime_seconds", 0)))
        self.info_labels["sessions"].setText(str(payload.get("active_sessions", "—")))
        self.info_labels["online"].setText(str(payload.get("online_clients", "—")))
        self.info_labels["peak"].setText(str(payload.get("peak_clients", "—")))

    def _update_events(self, events: list[dict[str, Any]]) -> None:
        recent = list(reversed(events[-18:]))
        self.event_count.setText(f"{len(events)} recorded")
        self.events_table.setRowCount(len(recent))
        for row, event in enumerate(recent):
            timestamp = str(event.get("timestamp", ""))
            display_time = timestamp[11:19] if len(timestamp) > 19 else timestamp
            self.events_table.setItem(row, 0, QTableWidgetItem(display_time))
            kind = str(event.get("kind", ""))
            self.events_table.setItem(row, 1, QTableWidgetItem(kind.replace("_", " ").title()))
            self.events_table.setItem(row, 2, QTableWidgetItem(str(event.get("message", ""))))
