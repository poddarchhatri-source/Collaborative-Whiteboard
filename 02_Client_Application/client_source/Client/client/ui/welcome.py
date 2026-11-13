"""Welcome / create / join / server connection screen."""

from __future__ import annotations

import html
import socket
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtNetwork import QTcpSocket
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.config import SettingsStore
from client.ui.dialog_text import AboutDialog, ShortcutsDialog
from client.ui.icons import icon, logo_pixmap, pixmap
from client.ui.widgets import StatusPill
from shared.constants import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    DEFAULT_PORT,
    HELLO,
    PROTOCOL_VERSION,
)
from shared.protocol import LineFramer, encode_message, make_client_message


class NetworkIllustration(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(10, 8, -10, -8)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(24, 24, 27, 230))
        gradient.setColorAt(1, QColor(15, 15, 17, 210))
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 18, 18)

        # Network arcs
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1, Qt.PenStyle.DashLine))
        center = QPointF(rect.center().x(), rect.center().y() - 10)
        for radius in (55, 92, 130):
            painter.drawEllipse(center, radius, radius * 0.68)

        # Board card
        board = QRectF(rect.center().x() - 84, rect.center().y() - 62, 168, 106)
        shadow = QPainterPath()
        shadow.addRoundedRect(board.translated(0, 5), 12, 12)
        painter.fillPath(shadow, QColor(0, 0, 0, 70))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(QColor(20, 20, 23, 245))
        painter.drawRoundedRect(board, 12, 12)
        painter.setPen(QPen(QColor(244, 244, 245, 190), 2))
        path = QPainterPath()
        path.moveTo(board.left() + 18, board.bottom() - 30)
        path.cubicTo(
            board.left() + 46, board.top() + 15,
            board.center().x(), board.bottom() - 18,
            board.right() - 18, board.top() + 22,
        )
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f4f4f5"))
        painter.drawEllipse(QPointF(board.right() - 28, board.top() + 24), 7, 7)
        painter.setBrush(QColor("#71717a"))
        painter.drawEllipse(QPointF(board.left() + 34, board.bottom() - 28), 6, 6)
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(QRectF(board.left() + 14, board.top() + 12, 38, 5), 2, 2)
        painter.drawRoundedRect(QRectF(board.left() + 14, board.top() + 22, 24, 4), 2, 2)

        # Peer nodes
        nodes = [
            (rect.left() + 55, rect.top() + 55, "#f4f4f5", "A"),
            (rect.right() - 48, rect.top() + 44, "#a1a1aa", "B"),
            (rect.left() + 42, rect.bottom() - 40, "#71717a", "C"),
            (rect.right() - 58, rect.bottom() - 52, "#d4d4d8", "D"),
        ]
        for x, y, color, label in nodes:
            point = QPointF(x, y)
            painter.setPen(QPen(QColor(color), 1.2))
            painter.setBrush(QColor(24, 24, 27, 250))
            painter.drawEllipse(point, 18, 18)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(point, 5, 5)
            painter.setPen(QPen(QColor("#f4f4f5")))
            painter.setFont(QFont("Inter", 8, QFont.Weight.Bold))
            painter.drawText(QRectF(x - 18, y + 20, 36, 15), Qt.AlignmentFlag.AlignCenter, label)
            painter.setPen(QPen(QColor(255, 255, 255, 25), 1))
            painter.drawLine(point, center)


class ServerProbe(QTcpSocket):
    """Non-blocking HELLO probe used by the server connection test."""

    completed = Signal(bool, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._framer = LineFramer()
        self._done = False
        self.readyRead.connect(self._read)
        self.connected.connect(self._send_hello)
        self.errorOccurred.connect(self._error)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(3500)
        self._timeout.timeout.connect(lambda: self._finish(False, "Server did not answer in time"))

    def probe(self, host: str, port: int) -> None:
        self._done = False
        self._framer = LineFramer()
        self.abort()
        self.connectToHost(host, int(port))
        self._timeout.start()

    def _send_hello(self) -> None:
        try:
            self.write(encode_message(make_client_message(HELLO, {})))
        except Exception:
            self._finish(False, "Could not send probe")

    def _read(self) -> None:
        try:
            messages = self._framer.feed(bytes(self.readAll()))
        except Exception:
            self._finish(False, "Server sent an invalid response")
            return
        for message in messages:
            if message.get("type") == "SYSTEM":
                motd = message.get("payload", {}).get("motd", "SynCanvas server")
                version = message.get("payload", {}).get("protocol_version", "?")
                self._finish(True, f"Connected · {motd} · protocol v{version}")
                return

    def _error(self, _error) -> None:
        self._finish(False, self.errorString() or "Could not reach the server")

    def _finish(self, ok: bool, message: str) -> None:
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        self.abort()
        self.completed.emit(ok, message)


class _ResponsiveFieldRow(QWidget):
    def __init__(self, fields: tuple[QWidget, ...], parent=None) -> None:
        super().__init__(parent)
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        for field in fields:
            self._layout.addWidget(field, 1)

    def resizeEvent(self, event) -> None:
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.width() < 400
            else QBoxLayout.Direction.LeftToRight
        )
        if self._layout.direction() != direction:
            self._layout.setDirection(direction)
        super().resizeEvent(event)


class WelcomeScreen(QWidget):
    createRequested = Signal(dict)
    joinRequested = Signal(dict)
    resumeRequested = Signal(dict)
    settingsRequested = Signal()
    serverHelpRequested = Signal()

    def __init__(self, store: SettingsStore, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.store = store
        self._probe: ServerProbe | None = None
        self._busy = False
        self._build()
        self.reload_settings()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hero = QFrame()
        hero.setStyleSheet(
            "QFrame#Hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #18181b, stop:0.5 #141416, stop:1 #0f0f11);"
            "border-right: 1px solid #242428; }"
        )
        hero.setObjectName("Hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(36, 30, 36, 26)
        hero_layout.setSpacing(16)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(logo_pixmap(46))
        brand_row.addWidget(logo)
        brand_name = QLabel(APP_NAME)
        brand_name.setProperty("role", "brand")
        brand_row.addWidget(brand_name)
        version = QLabel(f"v{APP_VERSION}")
        version.setProperty("role", "faint")
        brand_row.addWidget(version)
        brand_row.addStretch(1)
        help_button = QToolButton()
        help_button.setIcon(icon("command", "#a1a1aa", 18))
        help_button.setToolTip("Keyboard shortcuts (F1)")
        help_button.clicked.connect(lambda: ShortcutsDialog(self).exec())
        brand_row.addWidget(help_button)
        about_button = QToolButton()
        about_button.setIcon(icon("server", "#a1a1aa", 18))
        about_button.setToolTip("About SynCanvas")
        about_button.clicked.connect(lambda: AboutDialog(self).exec())
        brand_row.addWidget(about_button)
        hero_layout.addLayout(brand_row)
        hero_layout.addSpacing(10)
        headline = QLabel("A shared space\nfor working things out.")
        headline.setProperty("role", "h1")
        headline.setStyleSheet("font-size: 40px; font-weight: 800; line-height: 1.1;")
        hero_layout.addWidget(headline)
        sub = QLabel(
            "Bring your group together on one shared board. Draw, plan and discuss "
            "ideas over your local network — no internet account required."
        )
        sub.setWordWrap(True)
        sub.setProperty("role", "muted")
        sub.setStyleSheet("font-size: 14px;")
        hero_layout.addWidget(sub)
        hero_layout.addSpacing(4)
        self.illustration = NetworkIllustration()
        hero_layout.addWidget(self.illustration, 1)
        chips = QGridLayout()
        chips.setHorizontalSpacing(8)
        chips.setVerticalSpacing(8)
        features = [
            ("network", "TCP/IP socket sync"),
            ("pencil", "Infinite canvas"),
            ("chat", "Group chat"),
            ("history", "Version history"),
            ("shield", "Host-controlled roles"),
            ("wifi", "Works fully offline"),
        ]
        for index, (icon_name, text) in enumerate(features):
            chip = QFrame()
            chip.setStyleSheet(
                "QFrame { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);"
                " border-radius: 9px; }"
            )
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(9, 6, 11, 6)
            chip_layout.setSpacing(6)
            image = QLabel()
            image.setPixmap(pixmap(icon_name, "#a1a1aa", 15))
            chip_layout.addWidget(image)
            label = QLabel(text)
            label.setStyleSheet("font-size: 11px; color: #d4d4d8; background: transparent;")
            chip_layout.addWidget(label)
            chips.addWidget(chip, index // 3, index % 3)
        hero_layout.addLayout(chips)
        footer = QLabel("Runs on your local network · No cloud account needed")
        footer.setProperty("role", "faint")
        hero_layout.addWidget(footer)
        root.addWidget(hero, 6)

        # Right side -----------------------------------------------------
        right_host = QFrame()
        right_host.setObjectName("Root")
        right_layout = QVBoxLayout(right_host)
        right_layout.setContentsMargins(36, 16, 36, 16)
        right_layout.setSpacing(8)
        top_row = QHBoxLayout()
        top_row.addStretch(1)
        self.connection_pill = StatusPill("Not connected", "offline")
        top_row.addWidget(self.connection_pill)
        settings_button = QToolButton()
        settings_button.setIcon(icon("settings", "#94a3b8", 19))
        settings_button.setToolTip("Settings")
        settings_button.clicked.connect(self.settingsRequested)
        top_row.addWidget(settings_button)
        right_layout.addLayout(top_row)
        right_layout.addSpacing(4)
        heading = QLabel("Open a board")
        heading.setProperty("role", "title")
        right_layout.addWidget(heading)
        subheading = QLabel("Start a session or connect to a board already running.")
        subheading.setProperty("role", "muted")
        right_layout.addWidget(subheading)

        server_card = QFrame()
        server_card.setProperty("role", "softCard")
        server_layout = QGridLayout(server_card)
        server_layout.setContentsMargins(14, 11, 14, 11)
        server_layout.setHorizontalSpacing(8)
        server_layout.setVerticalSpacing(4)
        server_label = QLabel("SERVER CONNECTION")
        server_label.setProperty("role", "section")
        server_layout.addWidget(server_label, 0, 0, 1, 4)
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("192.168.1.10")
        self.host_input.setToolTip("IPv4 address or hostname of the SynCanvas server")
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(DEFAULT_PORT)
        self.port_input.setFixedWidth(98)
        self.probe_button = QPushButton("Test")
        self.probe_button.setObjectName("ghostButton")
        self.probe_button.setIcon(icon("wifi", "#94a3b8", 15))
        self.probe_button.clicked.connect(self._test_connection)
        server_layout.addWidget(self.host_input, 1, 0, 1, 2)
        server_layout.addWidget(self.port_input, 1, 2)
        server_layout.addWidget(self.probe_button, 1, 3)
        self.probe_status = QLabel("Enter the server IP shown on the host machine.")
        self.probe_status.setProperty("role", "faint")
        self.probe_status.setWordWrap(True)
        server_layout.addWidget(self.probe_status, 2, 0, 1, 4)
        right_layout.addWidget(server_card)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_host_tab(), "Host a board")
        self.tabs.addTab(self._build_join_tab(), "Join a board")
        right_layout.addWidget(self.tabs, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            "background: rgba(244,63,94,0.12); border: 1px solid rgba(244,63,94,0.35); "
            "border-radius: 8px; padding: 8px 10px; color: #fda4af; font-size: 12px;"
        )
        self.error_label.hide()
        right_layout.addWidget(self.error_label)

        recent_row = QHBoxLayout()
        recent_label = QLabel("RECENT SESSIONS")
        recent_label.setProperty("role", "section")
        recent_row.addWidget(recent_label)
        recent_row.addStretch(1)
        self.clear_recent = QPushButton("Clear")
        self.clear_recent.setObjectName("linkButton")
        self.clear_recent.clicked.connect(self._clear_recent)
        recent_row.addWidget(self.clear_recent)
        right_layout.addLayout(recent_row)
        self.recent = QListWidget()
        self.recent.setMaximumHeight(72)
        self.recent.itemDoubleClicked.connect(self._recent_activated)
        self.recent.setToolTip("Double-click to resume with your reconnect token")
        right_layout.addWidget(self.recent)

        status_row = QHBoxLayout()
        self.busy_label = QLabel("")
        self.busy_label.setProperty("role", "faint")
        status_row.addWidget(self.busy_label)
        status_row.addStretch(1)
        server_help = QPushButton("How to run a server")
        server_help.setObjectName("linkButton")
        server_help.clicked.connect(self.serverHelpRequested)
        status_row.addWidget(server_help)
        right_layout.addLayout(status_row)
        root.addWidget(right_host, 5)
        self._set_busy(False)

    def _build_host_tab(self) -> QWidget:
        page = QWidget()
        tab_layout = QVBoxLayout(page)
        tab_layout.setContentsMargins(2, 10, 2, 10)
        tab_layout.setSpacing(10)

        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # ── Row 1: Your name + Board name ──────────────────────────────
        name_w = QWidget()
        name_col = QVBoxLayout(name_w)
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(5)
        name_col.addWidget(self._field_label("Your name"))
        self.host_name = QLineEdit()
        self.host_name.setPlaceholderText("e.g. Aisha")
        self.host_name.setMinimumHeight(44)
        name_col.addWidget(self.host_name)

        board_w = QWidget()
        board_col = QVBoxLayout(board_w)
        board_col.setContentsMargins(0, 0, 0, 0)
        board_col.setSpacing(5)
        board_col.addWidget(self._field_label("Board name"))
        self.board_name = QLineEdit()
        self.board_name.setPlaceholderText("e.g. Data Structures Review")
        self.board_name.setMinimumHeight(44)
        board_col.addWidget(self.board_name)

        row1_w = _ResponsiveFieldRow((name_w, board_w))
        layout.addWidget(row1_w)

        # ── Row 2: Session password ─────────────────────────────────────
        pass_w = QWidget()
        pass_col = QVBoxLayout(pass_w)
        pass_col.setContentsMargins(0, 0, 0, 0)
        pass_col.setSpacing(5)
        pass_col.addWidget(self._field_label("Session password (optional)"))
        pw_row = QHBoxLayout()
        pw_row.setContentsMargins(0, 0, 0, 0)
        pw_row.setSpacing(6)
        self.host_password = QLineEdit()
        self.host_password.setPlaceholderText("Leave blank for no password")
        self.host_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.host_password.setMinimumHeight(44)
        eye = QToolButton()
        eye.setIcon(icon("shield", "#64748b", 17))
        eye.setCheckable(True)
        eye.setFixedSize(42, 42)
        eye.setToolTip("Show password")
        eye.toggled.connect(
            lambda checked: self.host_password.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        pw_row.addWidget(self.host_password, 1)
        pw_row.addWidget(eye)
        pass_col.addLayout(pw_row)
        layout.addWidget(pass_w)

        # ── Note ────────────────────────────────────────────────────────
        note = QLabel("You will be the host and can assign editor/viewer roles, lock the board, and manage versions.")
        note.setProperty("role", "faint")
        note.setWordWrap(True)
        layout.addWidget(note)

        # ── Quick start card ────────────────────────────────────────────
        quick = QFrame()
        quick.setProperty("role", "softCard")
        quick_layout = QVBoxLayout(quick)
        quick_layout.setContentsMargins(13, 10, 13, 11)
        quick_layout.setSpacing(6)
        quick_title = QLabel("GETTING STARTED")
        quick_title.setProperty("role", "section")
        quick_layout.addWidget(quick_title)
        for number, text in (
            ("1", "Start the server on one computer on your LAN."),
            ("2", "Create a board — you are the host."),
            ("3", "Share the room code and server IP with your team."),
        ):
            step_row = QHBoxLayout()
            step_row.setSpacing(8)
            step_row.setContentsMargins(0, 0, 0, 0)
            badge = QLabel(number)
            badge.setFixedSize(20, 20)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "background: #27272a; color: #f4f4f5; border: 1px solid #3f3f46;"
                " border-radius: 10px; font-size: 10px; font-weight: 800;"
            )
            step_row.addWidget(badge)
            step_lbl = QLabel(text)
            step_lbl.setProperty("role", "muted")
            step_lbl.setWordWrap(True)
            step_row.addWidget(step_lbl, 1)
            quick_layout.addLayout(step_row)
        layout.addWidget(quick)

        # ── Action button ───────────────────────────────────────────────
        self.create_button = QPushButton("Create board")
        self.create_button.setObjectName("primaryButton")
        self.create_button.setIcon(icon("arrow", "#09090b", 17))
        self.create_button.setFixedHeight(42)
        self.create_button.clicked.connect(self._create)
        tab_layout.addWidget(self._scrollable_form_page(form), 1)
        tab_layout.addWidget(self.create_button)
        return page

    def _build_join_tab(self) -> QWidget:
        page = QWidget()
        tab_layout = QVBoxLayout(page)
        tab_layout.setContentsMargins(2, 10, 2, 10)
        tab_layout.setSpacing(10)

        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # ── Row 1: Room code + Your name ───────────────────────────────
        row1_w = QWidget()
        row1 = QHBoxLayout(row1_w)
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(12)

        code_w = QWidget()
        code_col = QVBoxLayout(code_w)
        code_col.setContentsMargins(0, 0, 0, 0)
        code_col.setSpacing(5)
        code_col.addWidget(self._field_label("Room code"))
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("ABC123")
        self.code_input.setMaxLength(6)
        self.code_input.setFixedHeight(42)
        self.code_input.setStyleSheet(
            "background: #141416; border: 1px solid #2e2e33; border-radius: 9px;"
            " padding: 0px 12px; color: #f4f4f5; font-family: 'JetBrains Mono', monospace;"
            " font-size: 20px; font-weight: 800; letter-spacing: 4px;"
        )
        self.code_input.textChanged.connect(self._normalise_code)
        code_col.addWidget(self.code_input)

        name_w = QWidget()
        name_col = QVBoxLayout(name_w)
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(5)
        name_col.addWidget(self._field_label("Your name"))
        self.join_name = QLineEdit()
        self.join_name.setPlaceholderText("e.g. Rohan")
        self.join_name.setFixedHeight(42)
        name_col.addWidget(self.join_name)

        row1.addWidget(code_w, 1)
        row1.addWidget(name_w, 1)
        layout.addWidget(row1_w)
        layout.addSpacing(12)

        # ── Row 2: Join as (role) + Password ───────────────────────────
        row2_w = QWidget()
        row2 = QHBoxLayout(row2_w)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(12)

        role_w = QWidget()
        role_col = QVBoxLayout(role_w)
        role_col.setContentsMargins(0, 0, 0, 0)
        role_col.setSpacing(5)
        role_col.addWidget(self._field_label("Join as"))
        self.join_role = QComboBox()
        self.join_role.setFixedHeight(42)
        self.join_role.addItem("Editor — can draw and edit", "editor")
        self.join_role.addItem("Viewer — watch and chat", "viewer")
        role_col.addWidget(self.join_role)

        pass_w = QWidget()
        pass_col = QVBoxLayout(pass_w)
        pass_col.setContentsMargins(0, 0, 0, 0)
        pass_col.setSpacing(5)
        pass_col.addWidget(self._field_label("Password"))
        self.join_password = QLineEdit()
        self.join_password.setPlaceholderText("Required if the host set one")
        self.join_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.join_password.setFixedHeight(42)
        pass_col.addWidget(self.join_password)

        row2.addWidget(role_w, 1)
        row2.addWidget(pass_w, 1)
        layout.addWidget(row2_w)
        layout.addSpacing(10)

        # ── Hint text ───────────────────────────────────────────────────
        hint = QLabel("Ask the host for the 6-character room code and the server IP address.")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(10)

        # ── Permissions info card ───────────────────────────────────────
        tip = QFrame()
        tip.setProperty("role", "softCard")
        tip_layout = QVBoxLayout(tip)
        tip_layout.setContentsMargins(13, 10, 13, 11)
        tip_layout.setSpacing(5)
        tip_title = QLabel("PERMISSIONS")
        tip_title.setProperty("role", "section")
        tip_layout.addWidget(tip_title)
        tip_text = QLabel(
            "Editors can draw, edit and save versions. Viewers can watch the board and use chat. "
            "All permissions are enforced by the server."
        )
        tip_text.setProperty("role", "muted")
        tip_text.setWordWrap(True)
        tip_layout.addWidget(tip_text)
        layout.addWidget(tip)

        layout.addStretch(1)

        # ── Action button ───────────────────────────────────────────────
        self.join_button = QPushButton("Join board")
        self.join_button.setObjectName("primaryButton")
        self.join_button.setIcon(icon("users", "#09090b", 17))
        self.join_button.setFixedHeight(42)
        self.join_button.clicked.connect(self._join)
        tab_layout.addWidget(self._scrollable_form_page(form), 1)
        tab_layout.addWidget(self.join_button)
        return page

    @staticmethod
    def _scrollable_form_page(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "fieldLabel")
        return label

    # ------------------------------------------------------------------
    def reload_settings(self) -> None:
        settings = self.store.settings
        self.host_input.setText(settings.host or "127.0.0.1")
        self.port_input.setValue(int(settings.port or DEFAULT_PORT))
        if settings.username:
            self.host_name.setText(settings.username)
            self.join_name.setText(settings.username)
        if settings.last_code:
            self.code_input.setText(settings.last_code)
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.recent.clear()
        sessions = self.store.settings.recent_sessions
        if not sessions:
            item = QListWidgetItem("No recent boards yet — host one to get started.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(item)
            self.clear_recent.setEnabled(False)
            return
        self.clear_recent.setEnabled(True)
        for session in sessions[:6]:
            has_token = bool(self.store.settings.token_for(session.get("code", "")))
            suffix = "  ·  resume available" if has_token else ""
            item = QListWidgetItem(
                f"{session.get('name', 'Board')}   {session.get('code', '')}{suffix}\n"
                f"{session.get('host', '')}:{session.get('port', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, session)
            self.recent.addItem(item)

    def _clear_recent(self) -> None:
        self.store.settings.recent_sessions = []
        self.store.save()
        self._refresh_recent()

    def _recent_activated(self, item: QListWidgetItem) -> None:
        session = item.data(Qt.ItemDataRole.UserRole)
        if not session:
            return
        token = self.store.settings.token_for(session.get("code", ""))
        if token:
            self.resumeRequested.emit(
                {
                    "host": session.get("host", self.host_input.text()),
                    "port": int(session.get("port", self.port_input.value())),
                    "code": session.get("code", ""),
                    "token": token,
                }
            )
        else:
            self.host_input.setText(session.get("host", self.host_input.text()))
            self.port_input.setValue(int(session.get("port", self.port_input.value())))
            self.code_input.setText(session.get("code", ""))
            self.tabs.setCurrentIndex(1)

    def _normalise_code(self, text: str) -> None:
        cleaned = "".join(ch for ch in text.upper() if ch.isalnum())[:6]
        if cleaned != text:
            self.code_input.blockSignals(True)
            self.code_input.setText(cleaned)
            self.code_input.blockSignals(False)

    def _server_values(self) -> tuple[str, int] | None:
        host = self.host_input.text().strip()
        port = self.port_input.value()
        if not host:
            self.show_error("Enter the server IP address or hostname.")
            return None
        if not (1 <= port <= 65535):
            self.show_error("Port must be between 1 and 65535.")
            return None
        return host, port

    def _create(self) -> None:
        server = self._server_values()
        if not server:
            return
        name = self.host_name.text().strip()
        board = self.board_name.text().strip()
        if len(name) < 2:
            self.show_error("Please enter your name (at least 2 characters).")
            return
        if len(board) < 2:
            self.show_error("Give your board a name (at least 2 characters).")
            return
        if len(self.host_password.text()) > 128:
            self.show_error("Password is too long.")
            return
        self.clear_error()
        self.store.update(host=server[0], port=server[1], username=name)
        self.createRequested.emit(
            {
                "host": server[0],
                "port": server[1],
                "session_name": board,
                "username": name,
                "password": self.host_password.text(),
            }
        )

    def _join(self) -> None:
        server = self._server_values()
        if not server:
            return
        code = self.code_input.text().strip().upper()
        name = self.join_name.text().strip()
        if len(code) != 6:
            self.show_error("Room codes are exactly 6 characters.")
            return
        if len(name) < 2:
            self.show_error("Please enter your name (at least 2 characters).")
            return
        self.clear_error()
        self.store.update(host=server[0], port=server[1], username=name)
        self.joinRequested.emit(
            {
                "host": server[0],
                "port": server[1],
                "code": code,
                "username": name,
                "password": self.join_password.text(),
                "role": self.join_role.currentData(),
            }
        )

    def _test_connection(self) -> None:
        server = self._server_values()
        if not server:
            return
        self.probe_button.setEnabled(False)
        self.probe_status.setText("Testing TCP connection…")
        self.probe_status.setStyleSheet("color: #c18b32;")
        self._probe = ServerProbe(self)
        self._probe.completed.connect(self._probe_finished)
        self._probe.probe(*server)

    def _probe_finished(self, ok: bool, message: str) -> None:
        self.probe_button.setEnabled(True)
        self.probe_status.setText(message)
        self.probe_status.setStyleSheet("color: #3b9b70;" if ok else "color: #cf5b62;")
        self.connection_pill.set_state("online" if ok else "error", "Server online" if ok else "Connection failed")
        self._probe = None

    # ------------------------------------------------------------------
    def set_busy(self, busy: bool, message: str = "") -> None:
        self._set_busy(busy, message)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.create_button.setEnabled(not busy)
        self.join_button.setEnabled(not busy)
        self.probe_button.setEnabled(not busy)
        self.busy_label.setText(message if busy else "")
        if busy:
            self.connection_pill.set_state("connecting", "Connecting")

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()
        self._set_busy(False)

    def clear_error(self) -> None:
        self.error_label.hide()
        self.error_label.setText("")

    def reset_connection_state(self) -> None:
        self._set_busy(False)
        self.clear_error()
        self.connection_pill.set_state("offline", "Not connected")
        self.probe_status.setText("Enter the server IP shown on the host machine.")
        self.probe_status.setStyleSheet("")
        self._refresh_recent()

    def focus_join(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.code_input.setFocus()

    def focus_host(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.board_name.setFocus()
