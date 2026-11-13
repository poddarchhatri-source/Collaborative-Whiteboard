"""Graphical server control centre: start/stop, live sessions and logs."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import app_icon, icon, logo_pixmap, pixmap
from client.ui.theme import theme_colors
from client.ui.widgets import StatCard, StatusPill
from server.config import ServerConfig
from shared.constants import DEFAULT_PORT
from server.logging_setup import setup_logging
from server.network import ServerThread, lan_ip_addresses


def format_bytes(value: float) -> str:
    value = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = LogEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.message.emit(self.format(record))
        except Exception:
            pass


class ServerWindow(QMainWindow):
    def __init__(self, config: ServerConfig) -> None:
        super().__init__()
        self.config = config
        self.thread: ServerThread | None = None
        self._log_handler: QtLogHandler | None = None
        self.setWindowTitle("SynCanvas Server · Control Centre")
        self.setWindowIcon(app_icon())
        self.resize(1180, 820)
        self.setMinimumSize(960, 680)
        self._build_ui()
        self._attach_logging()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._update_state()
        self._refresh_lan_ips()
        self._log(
            "SynCanvas Server Control Centre ready. Configure the bind address and press Start Server."
        )

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        top = QFrame()
        top.setObjectName("TopBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(12, 7, 12, 7)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(38))
        top_layout.addWidget(logo)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("SynCanvas Server")
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        subtitle = QLabel("LAN collaboration control centre")
        subtitle.setProperty("role", "faint")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        top_layout.addLayout(title_col)
        top_layout.addStretch(1)
        self.status_pill = StatusPill("Stopped", "offline")
        top_layout.addWidget(self.status_pill)
        layout.addWidget(top)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(12)

        config_card = QFrame()
        config_card.setProperty("role", "card")
        cfg = QVBoxLayout(config_card)
        cfg.setContentsMargins(16, 14, 16, 16)
        cfg.setSpacing(14)
        header = QLabel("Server configuration")
        header.setProperty("role", "h2")
        cfg.addWidget(header)

        # ── Bind address + TCP port ──────────────────────────────────────
        host_row = QHBoxLayout()
        host_row.setSpacing(12)

        host_col = QVBoxLayout()
        host_col.setContentsMargins(0, 0, 0, 0)
        host_col.setSpacing(5)
        host_col.addWidget(self._field_label("Bind address"))
        self.host_input = QLineEdit(self.config.host)
        self.host_input.setPlaceholderText("0.0.0.0")
        self.host_input.setFixedHeight(42)
        host_col.addWidget(self.host_input)

        port_col = QVBoxLayout()
        port_col.setContentsMargins(0, 0, 0, 0)
        port_col.setSpacing(5)
        port_col.addWidget(self._field_label("TCP port"))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.config.port or DEFAULT_PORT)
        self.port_input.setFixedHeight(42)
        self.port_input.setFixedWidth(110)
        port_col.addWidget(self.port_input)

        host_row.addLayout(host_col, 1)
        host_row.addLayout(port_col, 0)
        cfg.addLayout(host_row)

        # ── SQLite database ──────────────────────────────────────────────
        db_col = QVBoxLayout()
        db_col.setContentsMargins(0, 0, 0, 0)
        db_col.setSpacing(5)
        db_col.addWidget(self._field_label("SQLite database"))
        db_row = QHBoxLayout()
        db_row.setSpacing(8)
        self.db_input = QLineEdit(str(self.config.db_path))
        self.db_input.setFixedHeight(42)
        browse = QPushButton("Browse…")
        browse.setFixedHeight(42)
        browse.clicked.connect(self._browse_db)
        db_row.addWidget(self.db_input, 1)
        db_row.addWidget(browse)
        db_col.addLayout(db_row)
        cfg.addLayout(db_col)

        # ── Welcome message ──────────────────────────────────────────────
        motd_col = QVBoxLayout()
        motd_col.setContentsMargins(0, 0, 0, 0)
        motd_col.setSpacing(5)
        motd_col.addWidget(self._field_label("Welcome message"))
        self.motd_input = QLineEdit(self.config.motd)
        self.motd_input.setFixedHeight(42)
        motd_col.addWidget(self.motd_input)
        cfg.addLayout(motd_col)

        # ── Action buttons ───────────────────────────────────────────────
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.start_button = QPushButton("Start server")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(icon("network", "#09090b", 17))
        self.start_button.setFixedHeight(42)
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("Stop server")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setIcon(icon("close", "#ef4444", 17))
        self.stop_button.setFixedHeight(42)
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        cfg.addLayout(button_row)
        left.addWidget(config_card)

        addresses_card = QFrame()
        addresses_card.setProperty("role", "card")
        addr = QVBoxLayout(addresses_card)
        addr.setContentsMargins(16, 13, 16, 14)
        addr.setSpacing(8)
        header_row = QHBoxLayout()
        addr_title = QLabel("Connect clients")
        addr_title.setProperty("role", "h2")
        header_row.addWidget(addr_title)
        header_row.addStretch(1)
        copy = QPushButton("Copy connection info")
        copy.setObjectName("ghostButton")
        copy.setIcon(icon("duplicate", "#a1a1aa", 15))
        copy.clicked.connect(self._copy_invite)
        header_row.addWidget(copy)
        addr.addLayout(header_row)
        self.addresses_label = QLabel("Server is stopped.")
        self.addresses_label.setProperty("role", "muted")
        self.addresses_label.setWordWrap(True)
        self.addresses_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        addr.addWidget(self.addresses_label)
        hint = QLabel(
            "Clients must be on the same LAN and able to reach this port. On Windows, allow TCP "
            "through the firewall the first time the server starts."
        )
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        addr.addWidget(hint)
        left.addWidget(addresses_card)

        security_card = QFrame()
        security_card.setProperty("role", "card")
        sec = QVBoxLayout(security_card)
        sec.setContentsMargins(16, 13, 16, 14)
        sec.setSpacing(5)
        sec_title = QLabel("LAN security checklist")
        sec_title.setProperty("role", "h2")
        sec.addWidget(sec_title)
        for line in (
            "Use a session password for every room.",
            "Roles are enforced by this server, not just the client UI.",
            "No cloud services or internet connections are used.",
            "Message frames, images and identifiers are validated.",
            "Allow TCP through the host firewall on private networks only.",
        ):
            bullet = QLabel("•  " + line)
            bullet.setProperty("role", "muted")
            bullet.setWordWrap(True)
            sec.addWidget(bullet)
        left.addWidget(security_card)
        left.addStretch(1)
        columns.addLayout(left, 5)

        right = QVBoxLayout()
        right.setSpacing(12)
        stats_grid = QGridLayout()
        stats_grid.setSpacing(8)
        self.clients_card = StatCard("Online clients", "0", "users", "#f4f4f5")
        self.sessions_card = StatCard("Active sessions", "0", "network", "#e4e4e7")
        self.messages_card = StatCard("Messages in / out", "0 / 0", "chat", "#d4d4d8")
        self.data_card = StatCard("Data in / out", "0 / 0", "activity", "#a1a1aa")
        self.objects_card = StatCard("Canvas objects", "0", "layers", "#e4e4e7")
        self.uptime_card = StatCard("Uptime", "—", "clock", "#a1a1aa")
        for index, card in enumerate(
            [self.clients_card, self.sessions_card, self.messages_card, self.data_card, self.objects_card, self.uptime_card]
        ):
            stats_grid.addWidget(card, index // 3, index % 3)
        right.addLayout(stats_grid)

        table_card = QFrame()
        table_card.setProperty("role", "card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 12, 14, 12)
        table_layout.setSpacing(6)
        table_header = QHBoxLayout()
        table_title = QLabel("Persisted sessions")
        table_title.setProperty("role", "h2")
        table_header.addWidget(table_title)
        table_header.addStretch(1)
        self.reset_btn = QPushButton("Delete empty sessions")
        self.reset_btn.setObjectName("ghostButton")
        self.reset_btn.clicked.connect(self._delete_empty_sessions)
        table_header.addWidget(self.reset_btn)
        table_layout.addLayout(table_header)
        self.sessions_table = QTableWidget(0, 5)
        self.sessions_table.setHorizontalHeaderLabels(["Code", "Board", "Host", "People", "Locked"])
        self.sessions_table.verticalHeader().setVisible(False)
        self.sessions_table.horizontalHeader().setStretchLastSection(True)
        self.sessions_table.setColumnWidth(0, 76)
        self.sessions_table.setColumnWidth(1, 190)
        self.sessions_table.setColumnWidth(2, 120)
        self.sessions_table.setColumnWidth(3, 64)
        self.sessions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sessions_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.sessions_table.setMaximumHeight(190)
        table_layout.addWidget(self.sessions_table)
        right.addWidget(table_card, 1)

        log_card = QFrame()
        log_card.setProperty("role", "card")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(14, 12, 14, 12)
        log_layout.setSpacing(6)
        log_header = QHBoxLayout()
        log_title = QLabel("Server log")
        log_title.setProperty("role", "h2")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        open_logs = QPushButton("Open logs folder")
        open_logs.setObjectName("ghostButton")
        open_logs.clicked.connect(self._open_logs)
        log_header.addWidget(open_logs)
        clear = QPushButton("Clear")
        clear.setObjectName("ghostButton")
        clear.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(clear)
        log_layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "background: #0f0f11; border: 1px solid #242428; border-radius: 8px; "
            "font-family: 'JetBrains Mono', 'Cascadia Code', monospace; font-size: 11px; color: #f4f4f5;"
        )
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setMinimumHeight(140)
        log_layout.addWidget(self.log_view, 1)
        right.addWidget(log_card, 3)
        columns.addLayout(right, 7)
        layout.addLayout(columns, 1)

        self.footer = QLabel(
            f"SynCanvas Server v1.0 · Logs: {self.config.log_dir}"
        )
        self.footer.setProperty("role", "faint")
        layout.addWidget(self.footer)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "fieldLabel")
        return label

    # ------------------------------------------------------------------
    def _attach_logging(self) -> None:
        self._log_handler = QtLogHandler()
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S")
        )
        self._log_handler.emitter.message.connect(self._append_log)
        logging.getLogger().addHandler(self._log_handler)

    def _log(self, message: str) -> None:
        self._append_log(f"{time.strftime('%H:%M:%S')}  {message}")

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _browse_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose SQLite database", self.db_input.text(), "SQLite database (*.db)"
        )
        if path:
            self.db_input.setText(path)

    def _copy_invite(self) -> None:
        port = self.thread.server.bound_port if self.thread and self.thread.server else self.port_input.value()
        ips = lan_ip_addresses()
        ip = next((entry for entry in ips if entry != "127.0.0.1"), "127.0.0.1")
        QApplication.clipboard().setText(
            f"SynCanvas server: {ip}:{port}\nOpen SynCanvas on your device, enter this server address, "
            "then join with the room code."
        )
        self._log(f"Copied connection info for {ip}:{port}")

    def _open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.log_dir)))

    def _refresh_lan_ips(self) -> None:
        if self.thread and self.thread.running and self.thread.server:
            port = self.thread.server.bound_port
            ips = lan_ip_addresses()
            lines = [f"{ip}:{port}" for ip in ips]
            self.addresses_label.setText("Server is listening. Use one of these addresses:\n" + "   ".join(lines))
        else:
            self.addresses_label.setText("Server is stopped. Start it to see the LAN addresses clients should use.")

    # ------------------------------------------------------------------
    def _start(self) -> None:
        if self.thread and self.thread.running:
            return
        host = self.host_input.text().strip() or "0.0.0.0"
        port = self.port_input.value()
        db_path = Path(self.db_input.text()).expanduser()
        config = ServerConfig(
            host=host,
            port=port,
            db_path=db_path,
            log_dir=self.config.log_dir,
            motd=self.motd_input.text().strip() or "Welcome to SynCanvas",
            require_password=False,
        )
        self.config = config
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_pill.set_state("connecting", "Starting…")
        self._log(f"Starting server on {host}:{port}…")
        try:
            thread = ServerThread(config)
            bound = thread.start(timeout=10)
            self.thread = thread
            if bound != port:
                self.port_input.setValue(bound)
            self.status_pill.set_state("online", "Running")
            self._log(f"Server started on {host}:{bound}")
            self._update_state()
            self._refresh_lan_ips()
        except Exception as exc:
            self.thread = None
            self.status_pill.set_state("error", "Failed")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self._log(f"ERROR: {exc}")
            QMessageBox.critical(self, "Could not start server", str(exc))
            self._update_state()

    def _stop(self) -> None:
        if not self.thread:
            return
        self._log("Stopping server…")
        self.stop_button.setEnabled(False)
        self.status_pill.set_state("connecting", "Stopping…")
        try:
            self.thread.stop()
        except Exception as exc:
            self._log(f"ERROR while stopping: {exc}")
        self.thread = None
        self.status_pill.set_state("offline", "Stopped")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._log("Server stopped")
        self._update_state()
        self._refresh_lan_ips()

    def _delete_empty_sessions(self) -> None:
        if not self.thread or not self.thread.server:
            return
        manager = self.thread.server.core.sessions
        removed = 0
        for session in list(manager.sessions.values()):
            if not session.online_participants() and not session.board.objects:
                manager.delete_session(session)
                removed += 1
        self._log(f"Deleted {removed} empty session(s)")
        self._refresh()

    def _update_state(self) -> None:
        running = bool(self.thread and self.thread.running)
        self.host_input.setEnabled(not running)
        self.port_input.setEnabled(not running)
        self.db_input.setEnabled(not running)
        self.motd_input.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.reset_btn.setEnabled(running)
        self._refresh_lan_ips()
        self._update_footer()

    def _update_footer(self) -> None:
        if self.thread and self.thread.running and self.thread.server:
            self.footer.setText(
                f"SynCanvas Server v1.0 · Listening on {self.host_input.text()}:{self.thread.server.bound_port} · "
                f"Logs: {self.config.log_dir}"
            )
        else:
            self.footer.setText(f"SynCanvas Server v1.0 · Stopped · Logs: {self.config.log_dir}")

    def _refresh(self) -> None:
        if not self.thread or not self.thread.server:
            return
        try:
            stats = self.thread.stats()
        except Exception:
            return
        self.clients_card.set_value(str(stats.get("online_clients", 0)))
        self.sessions_card.set_value(str(stats.get("active_sessions", 0)))
        self.messages_card.set_value(
            f"{int(stats.get('messages_received', 0)):,} / {int(stats.get('messages_sent', 0)):,}"
        )
        self.data_card.set_value(
            f"{format_bytes(stats.get('bytes_received', 0))} / {format_bytes(stats.get('bytes_sent', 0))}"
        )
        self.objects_card.set_value(str(stats.get("total_objects", 0)))
        self.uptime_card.set_value(format_duration(stats.get("uptime_seconds", 0)))
        sessions = stats.get("sessions", [])
        self.sessions_table.setRowCount(len(sessions))
        for row, session in enumerate(sessions):
            values = [
                session.get("code", ""),
                session.get("name", ""),
                session.get("host_name", ""),
                str(session.get("participants", 0)),
                "Yes" if session.get("locked") else "No",
            ]
            for column, value in enumerate(values):
                self.sessions_table.setItem(row, column, QTableWidgetItem(value))
        self.status_pill.set_state("online" if stats.get("running", True) else "offline", "Running")
        self._refresh_lan_ips()

    def closeEvent(self, event) -> None:
        if self.thread:
            try:
                self.thread.stop()
            except Exception:
                pass
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
        super().closeEvent(event)
