"""SynCanvas main window: welcome flow and collaborative board workspace."""

from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.canvas.board_view import BoardView
from client.canvas.model import BoardModel
from client.config import SettingsStore
from client.networking.client import ConnectionManager
from client.services.exporter import ExportError
from client.services.images import ImageError, display_size, load_image
from client.ui.dialog_export import ExportImportDialog
from client.ui.dialog_network import NetworkDashboardDialog
from client.ui.dialog_participants import ParticipantManagerDialog
from client.ui.dialog_settings import SettingsDialog
from client.ui.dialog_text import (
    CommandPalette,
    FlowchartTemplatesDialog,
    PaletteCommand,
    ShortcutsDialog,
    TextEditorDialog,
)
from client.ui.dialog_versions import SaveVersionDialog, VersionHistoryDialog
from client.ui.icons import app_icon, icon as vector_icon, logo_pixmap, pixmap
from client.ui.panels import BoardSidebar, ChatPanel
from client.ui.theme import apply_theme, theme_colors
from client.ui.tool_options import ToolOptionsBar
from client.ui.widgets import MiniMap, StatusPill, ToastManager
from client.ui.welcome import WelcomeScreen
from shared.constants import (
    ACTION_BOARD_LOCKED,
    ACTION_OBJECT_CREATED,
    ACTION_OBJECTS_DELETED,
    ACTION_PRESENTATION,
    ACTION_USER_JOINED,
    ACTION_USER_LEFT,
    APP_NAME,
    APP_VERSION,
    KIND_CONNECTOR,
    KIND_IMAGE,
    KIND_MINDNODE,
    KIND_STICKY,
    KIND_TEXT,
    ROLE_EDITOR,
    ROLE_HOST,
    ROLE_VIEWER,
    SHORTCUTS,
    TOOL_ARROW,
    TOOL_BRUSH,
    TOOL_CIRCLE,
    TOOL_CONNECTOR,
    TOOL_DIAMOND,
    TOOL_ELLIPSE,
    TOOL_ERASER,
    TOOL_FLOW_DECISION,
    TOOL_FLOW_IO,
    TOOL_FLOW_PROCESS,
    TOOL_FLOW_START,
    TOOL_IMAGE,
    TOOL_LASER,
    TOOL_LINE,
    TOOL_MINDNODE,
    TOOL_PAN,
    TOOL_PENCIL,
    TOOL_RECT,
    TOOL_ROUNDED_RECT,
    TOOL_SELECT,
    TOOL_STICKY,
    TOOL_TEXT,
    TOOL_TRIANGLE,
    TOOL_LABELS,
    PALETTE,
)
from shared.protocol import make_client_message

# The real icon helper; the extra import above is kept harmless for tooling.
icon = vector_icon


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle(f"{APP_NAME} · LAN collaborative whiteboard")
        self.setWindowIcon(app_icon())
        self.resize(1480, 920)
        self.setMinimumSize(1080, 680)
        self.setObjectName("Root")

        self.manager = ConnectionManager(self)
        self.model = BoardModel(self)
        self.view = BoardView(self.model, self)
        self.toast = ToastManager(self)

        self._session_active = False
        self._role = ROLE_VIEWER
        self._self_participant: dict[str, Any] = {}
        self._current_tool = TOOL_SELECT
        self._can_undo = False
        self._can_redo = False
        self._presentation_active = False
        self._presentation_is_host = False
        self._presentation_host_name = ""
        self._following_presenter = bool(store.settings.presentation_follow)
        self._presentation_state: dict[str, Any] = {}
        self._presentation_previous_geometry = None
        self._preexisting_maximized = False
        self._presentation_opted_out = False
        self._presentation_slide = 0
        self._dialogs: dict[str, Any] = {}
        self._shortcuts: list[QShortcut] = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(2200)
        self._save_timer.timeout.connect(self._saved_now)
        self._last_locked = False
        self._tool_buttons: dict[str, QToolButton] = {}
        self._last_server_stats: dict[str, Any] = {}
        self._last_presentation_sent = 0.0
        self._auth_payload: dict[str, Any] = {}

        self._build_ui()
        self._build_shortcuts()
        self._wire_signals()
        self._apply_settings(initial=True)
        geometry = store.settings.window_geometry
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except Exception:
                pass
        QTimer.singleShot(60, self._position_overlays)
        self._update_interaction_state()

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.welcome = WelcomeScreen(self.store, self)
        self.stack.addWidget(self.welcome)

        self.board_page = QWidget()
        self.board_page.setObjectName("Root")
        board_layout = QVBoxLayout(self.board_page)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(0)

        self.top_bar = self._build_top_bar()
        board_layout.addWidget(self.top_bar)

        self.offline_banner = QFrame()
        self.offline_banner.setStyleSheet(
            "QFrame { background: rgba(245,158,11,0.14); border-bottom: 1px solid rgba(245,158,11,0.38); }"
        )
        offline_layout = QHBoxLayout(self.offline_banner)
        offline_layout.setContentsMargins(14, 4, 14, 4)
        offline_layout.setSpacing(8)
        warn_icon = QLabel()
        warn_icon.setPixmap(pixmap("warning", "#f59e0b", 16))
        offline_layout.addWidget(warn_icon)
        self.offline_label = QLabel("Connection lost — edits will be queued and synchronized after reconnect.")
        self.offline_label.setStyleSheet("color: #fcd34d; font-size: 12px; background: transparent;")
        offline_layout.addWidget(self.offline_label, 1)
        self.retry_button = QPushButton("Retry now")
        self.retry_button.setObjectName("linkButton")
        self.retry_button.clicked.connect(self.manager.reconnect_now)
        offline_layout.addWidget(self.retry_button)
        self.offline_banner.hide()
        board_layout.addWidget(self.offline_banner)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.left_toolbar = self._build_toolbar()
        content.addWidget(self.left_toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.tool_options = ToolOptionsBar()
        style_strip = QWidget()
        style_layout = QHBoxLayout(style_strip)
        style_layout.setContentsMargins(10, 7, 10, 5)
        style_layout.setSpacing(0)
        style_layout.addWidget(self.tool_options, 0, Qt.AlignmentFlag.AlignLeft)
        style_layout.addStretch(1)
        center_layout.addWidget(style_strip)
        self.center_layout = center_layout
        center_layout.addWidget(self.view, 1)
        self.splitter.addWidget(center)

        self.sidebar = BoardSidebar()
        self.sidebar.setMinimumWidth(300)
        self.splitter.addWidget(self.sidebar)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([1120, 330])
        content.addWidget(self.splitter, 1)

        board_layout.addLayout(content, 1)
        self.bottom_bar = self._build_bottom_bar()
        board_layout.addWidget(self.bottom_bar)
        self.stack.addWidget(self.board_page)

        self._build_canvas_overlays()
        self.stack.setCurrentIndex(0)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(60)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30))
        layout.addWidget(logo)
        brand = QLabel(APP_NAME)
        brand.setProperty("role", "brand")
        brand.setStyleSheet("font-size: 15px;")
        layout.addWidget(brand)
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setFixedHeight(26)
        sep.setStyleSheet("background: #2e2e33;")
        layout.addWidget(sep)
        self.board_title = QLabel("Board")
        self.board_title.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(self.board_title)
        self.code_label = QLabel("")
        self.code_label.setProperty("role", "badge")
        self.code_label.setToolTip("Room code")
        layout.addWidget(self.code_label)
        copy_code = QToolButton()
        copy_code.setIcon(icon("duplicate", "#a1a1aa", 14))
        copy_code.setToolTip("Copy room code")
        copy_code.clicked.connect(self._copy_code)
        layout.addWidget(copy_code)
        self.role_badge = QLabel("")
        self.role_badge.setProperty("role", "badge")
        layout.addWidget(self.role_badge)
        layout.addStretch(1)
        self.save_indicator = QLabel("Saved")
        self.save_indicator.setProperty("role", "faint")
        layout.addWidget(self.save_indicator)
        self.connection_pill = StatusPill("Connected", "online")
        layout.addWidget(self.connection_pill)
        self._btn_versions = self._top_action("history", "Version history", self._show_versions)
        layout.addWidget(self._btn_versions)
        self._btn_network = self._top_action("network", "Network monitoring", self._show_network)
        layout.addWidget(self._btn_network)
        self._btn_people = self._top_action("users", "Participants", self._show_participants)
        layout.addWidget(self._btn_people)
        self._btn_lock = self._top_action("lock", "Lock board", self._toggle_lock, checkable=True)
        layout.addWidget(self._btn_lock)
        self._btn_join_presentation = QPushButton("Join presentation")
        self._btn_join_presentation.setObjectName("primaryButton")
        self._btn_join_presentation.setVisible(False)
        self._btn_join_presentation.clicked.connect(self._rejoin_presentation)
        layout.addWidget(self._btn_join_presentation)
        self._btn_present = QPushButton("Present")
        self._btn_present.setObjectName("primaryButton")
        self._btn_present.setIcon(icon("present", "#09090b", 16))
        self._btn_present.clicked.connect(self._start_presentation)
        layout.addWidget(self._btn_present)
        self._btn_settings = self._top_action("settings", "Settings", self._show_settings)
        layout.addWidget(self._btn_settings)
        self._btn_more = self._top_action("more", "More actions", self._show_more_menu)
        layout.addWidget(self._btn_more)
        self._btn_leave = self._top_action("logout", "Leave board", self._leave_board)
        layout.addWidget(self._btn_leave)
        return bar

    def _top_action(self, icon_name: str, tooltip: str, callback, *, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setIcon(icon(icon_name, "#a1a1aa", 18))
        button.setToolTip(tooltip)
        button.setFixedSize(34, 34)
        if checkable:
            button.setCheckable(True)
            button.toggled.connect(lambda _checked: callback())
        else:
            button.clicked.connect(callback)
        return button

    def _build_toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("LeftToolbar")
        frame.setFixedWidth(58)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        def add_tool(tool: str, icon_name: str, label: str) -> QToolButton:
            button = QToolButton()
            button.setIcon(icon(icon_name, "#f4f4f5", 19))
            button.setToolTip(label)
            button.setCheckable(True)
            button.setFixedSize(38, 36)
            button.setIconSize(QSize(19, 19))
            self.tool_group.addButton(button)
            self._tool_buttons[tool] = button
            button.clicked.connect(lambda _=False, t=tool: self._select_tool(t))
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            return button

        def add_separator() -> None:
            line = QFrame()
            line.setFixedHeight(1)
            line.setFixedWidth(26)
            line.setStyleSheet("background: #242428;")
            layout.addWidget(line, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addSpacing(1)

        add_tool(TOOL_SELECT, "select", "Select / move (V)")
        add_tool(TOOL_PAN, "hand", "Pan canvas (H)")
        add_separator()
        add_tool(TOOL_PENCIL, "pencil", "Pencil (P)")
        add_tool(TOOL_BRUSH, "brush", "Brush (B)")
        add_tool(TOOL_ERASER, "eraser", "Eraser (E)")
        add_separator()
        add_tool(TOOL_TEXT, "text", "Text (T)")
        add_separator()
        self.shape_button = QToolButton()
        self.shape_button.setIcon(icon("square", "#f4f4f5", 19))
        self.shape_button.setToolTip("Shapes (R, O, D…)")
        self.shape_button.setCheckable(True)
        self.shape_button.setFixedSize(38, 36)
        self.shape_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tool_group.addButton(self.shape_button)
        shape_menu = QMenu(self.shape_button)
        for tool, icon_name, label in (
            (TOOL_RECT, "square", "Rectangle (R)"),
            (TOOL_ROUNDED_RECT, "rounded", "Rounded rectangle"),
            (TOOL_ELLIPSE, "circle", "Ellipse (O)"),
            (TOOL_CIRCLE, "circle", "Circle"),
            (TOOL_DIAMOND, "diamond", "Diamond (D)"),
            (TOOL_TRIANGLE, "triangle", "Triangle"),
        ):
            action = shape_menu.addAction(icon(icon_name, "#d4d4d8", 16), label)
            action.triggered.connect(lambda _=False, t=tool: self._select_tool(t))
        self.shape_button.setMenu(shape_menu)
        self._shape_menu = shape_menu
        layout.addWidget(self.shape_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.line_button = QToolButton()
        self.line_button.setIcon(icon("line", "#f4f4f5", 19))
        self.line_button.setToolTip("Lines and arrows")
        self.line_button.setCheckable(True)
        self.line_button.setFixedSize(38, 36)
        self.line_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tool_group.addButton(self.line_button)
        line_menu = QMenu(self.line_button)
        for tool, icon_name, label in (
            (TOOL_LINE, "line", "Line"),
            (TOOL_ARROW, "arrow", "Arrow (A)"),
        ):
            action = line_menu.addAction(icon(icon_name, "#d4d4d8", 16), label)
            action.triggered.connect(lambda _=False, t=tool: self._select_tool(t))
        self.line_button.setMenu(line_menu)
        self._line_menu = line_menu
        layout.addWidget(self.line_button, 0, Qt.AlignmentFlag.AlignHCenter)
        add_tool(TOOL_CONNECTOR, "connector", "Attached connector (C)")
        add_separator()
        add_tool(TOOL_STICKY, "sticky", "Sticky note (S)")
        add_tool(TOOL_MINDNODE, "mindmap", "Mind-map node (M)")
        add_tool(TOOL_IMAGE, "image", "Insert image")
        add_separator()
        self.flow_button = QToolButton()
        self.flow_button.setIcon(icon("flow", "#f4f4f5", 19))
        self.flow_button.setToolTip("Flowchart symbols")
        self.flow_button.setCheckable(True)
        self.flow_button.setFixedSize(38, 36)
        self.flow_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tool_group.addButton(self.flow_button)
        flow_menu = QMenu(self.flow_button)
        for tool, icon_name, label in (
            (TOOL_FLOW_START, "circle", "Start / end"),
            (TOOL_FLOW_PROCESS, "square", "Process"),
            (TOOL_FLOW_DECISION, "diamond", "Decision"),
            (TOOL_FLOW_IO, "rounded", "Input / output"),
        ):
            action = flow_menu.addAction(icon(icon_name, "#d4d4d8", 16), label)
            action.triggered.connect(lambda _=False, t=tool: self._select_tool(t))
        self.flow_button.setMenu(flow_menu)
        self._flow_menu = flow_menu
        layout.addWidget(self.flow_button, 0, Qt.AlignmentFlag.AlignHCenter)
        add_tool(TOOL_LASER, "laser", "Laser pointer (L)")
        layout.addStretch(1)
        add_separator()
        self.undo_button = QToolButton()
        self.undo_button.setIcon(icon("undo", "#a1a1aa", 19))
        self.undo_button.setToolTip("Undo my last operation (Ctrl+Z)")
        self.undo_button.setFixedSize(38, 34)
        self.undo_button.clicked.connect(self._undo)
        layout.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.redo_button = QToolButton()
        self.redo_button.setIcon(icon("redo", "#a1a1aa", 19))
        self.redo_button.setToolTip("Redo (Ctrl+Shift+Z)")
        self.redo_button.setFixedSize(38, 34)
        self.redo_button.clicked.connect(self._redo)
        layout.addWidget(self.redo_button, 0, Qt.AlignmentFlag.AlignHCenter)
        help_button = QToolButton()
        help_button.setIcon(icon("command", "#71717a", 18))
        help_button.setToolTip("Keyboard shortcuts (F1)")
        help_button.setFixedSize(38, 32)
        help_button.clicked.connect(self._show_help)
        layout.addWidget(help_button, 0, Qt.AlignmentFlag.AlignHCenter)
        help_button.setToolTip("Keyboard shortcuts (F1)")
        help_button.setFixedSize(38, 32)
        help_button.clicked.connect(self._show_help)
        layout.addWidget(help_button, 0, Qt.AlignmentFlag.AlignHCenter)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.tool_scroll = scroll
        self._tool_buttons[TOOL_SELECT].setChecked(True)
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        return frame

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("BottomBar")
        bar.setFixedHeight(30)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 2, 12, 2)
        layout.setSpacing(14)
        self.bottom_connection = QLabel("●  Not connected")
        self.bottom_connection.setStyleSheet("color: #22c55e; font-size: 11px;")
        layout.addWidget(self.bottom_connection)
        self.bottom_endpoint = QLabel("")
        self.bottom_endpoint.setProperty("role", "faint")
        layout.addWidget(self.bottom_endpoint)
        layout.addStretch(1)
        self.bottom_latency = QLabel("Latency —")
        self.bottom_latency.setProperty("role", "faint")
        layout.addWidget(self.bottom_latency)
        self.bottom_sync = QLabel("In sync")
        self.bottom_sync.setProperty("role", "faint")
        layout.addWidget(self.bottom_sync)
        self.bottom_outbox = QLabel("")
        self.bottom_outbox.setProperty("role", "faint")
        layout.addWidget(self.bottom_outbox)
        self.bottom_objects = QLabel("0 objects")
        self.bottom_objects.setProperty("role", "faint")
        layout.addWidget(self.bottom_objects)
        self.bottom_zoom = QLabel("100%")
        self.bottom_zoom.setProperty("role", "faint")
        layout.addWidget(self.bottom_zoom)
        net_button = QPushButton("Network details")
        net_button.setObjectName("linkButton")
        net_button.clicked.connect(self._show_network)
        layout.addWidget(net_button)
        return bar

    def _build_canvas_overlays(self) -> None:
        self.zoom_bar = QFrame(self.view)
        self.zoom_bar.setObjectName("ZoomBar")
        self.zoom_bar.setStyleSheet(
            "QFrame#ZoomBar { background: rgba(24,24,27,0.97); border: 1px solid #2e2e33; border-radius: 9px; }"
        )
        zl = QHBoxLayout(self.zoom_bar)
        zl.setContentsMargins(4, 2, 4, 2)
        zl.setSpacing(1)
        zoom_out = QToolButton()
        zoom_out.setIcon(icon("minus", "#d4d4d8", 15))
        zoom_out.setToolTip("Zoom out")
        zoom_out.clicked.connect(self.view.zoom_out)
        zl.addWidget(zoom_out)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setStyleSheet("font-size: 11px; font-weight: 700;")
        zl.addWidget(self.zoom_label)
        zoom_in = QToolButton()
        zoom_in.setIcon(icon("plus", "#d4d4d8", 15))
        zoom_in.setToolTip("Zoom in")
        zoom_in.clicked.connect(self.view.zoom_in)
        zl.addWidget(zoom_in)
        fit = QToolButton()
        fit.setIcon(icon("fit", "#d4d4d8", 15))
        fit.setToolTip("Fit board to screen")
        fit.clicked.connect(self.view.fit_content)
        zl.addWidget(fit)
        map_button = QToolButton()
        map_button.setIcon(icon("minimap", "#a1a1aa", 15))
        map_button.setToolTip("Toggle overview map")
        map_button.clicked.connect(self._toggle_minimap)
        zl.addWidget(map_button)
        self.zoom_bar.adjustSize()
        self.zoom_bar.raise_()

        self.minimap_frame = QFrame(self.view)
        self.minimap_frame.setStyleSheet(
            "QFrame#MiniMapFrame { background: rgba(24,24,27,0.98); border: 1px solid #2e2e33; border-radius: 10px; }"
        )
        self.minimap_frame.setObjectName("MiniMapFrame")
        ml = QVBoxLayout(self.minimap_frame)
        ml.setContentsMargins(6, 6, 6, 6)
        ml.setSpacing(3)
        map_header = QHBoxLayout()
        map_label = QLabel("OVERVIEW")
        map_label.setProperty("role", "section")
        map_header.addWidget(map_label)
        map_header.addStretch(1)
        close_map = QToolButton()
        close_map.setIcon(icon("close", "#71717a", 12))
        close_map.clicked.connect(lambda: self.minimap_frame.hide())
        map_header.addWidget(close_map)
        ml.addLayout(map_header)
        self.minimap = MiniMap(self.view)
        ml.addWidget(self.minimap)
        self.minimap_frame.setFixedSize(220, 178)
        self.minimap_frame.hide()

        self.presentation_bar = QFrame(self.view)
        self.presentation_bar.setObjectName("PresentationBar")
        self.presentation_bar.setStyleSheet(
            "QFrame#PresentationBar { background: rgba(24,24,27,0.98); border: 1px solid #3f3f46; "
            "border-radius: 12px; }"
        )
        pl = QHBoxLayout(self.presentation_bar)
        pl.setContentsMargins(12, 6, 8, 6)
        pl.setSpacing(6)
        live_dot = QLabel("●")
        live_dot.setStyleSheet("color: #ef4444; font-size: 11px;")
        pl.addWidget(live_dot)
        self.presentation_label = QLabel("Presenting")
        self.presentation_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        pl.addWidget(self.presentation_label)
        self.presentation_page = QLabel("")
        self.presentation_page.setProperty("role", "faint")
        pl.addWidget(self.presentation_page)
        pl.addSpacing(10)
        self.pres_prev = QToolButton()
        self.pres_prev.setIcon(icon("backward", "#f4f4f5", 16))
        self.pres_prev.setToolTip("Previous viewport")
        self.pres_prev.clicked.connect(lambda: self._navigate_presentation(-1))
        pl.addWidget(self.pres_prev)
        self.pres_next = QToolButton()
        self.pres_next.setIcon(icon("forward", "#f4f4f5", 16))
        self.pres_next.setToolTip("Next viewport")
        self.pres_next.clicked.connect(lambda: self._navigate_presentation(1))
        pl.addWidget(self.pres_next)
        self.pres_follow = QPushButton("Following")
        self.pres_follow.setCheckable(True)
        self.pres_follow.setChecked(self._following_presenter)
        self.pres_follow.clicked.connect(self._toggle_follow)
        pl.addWidget(self.pres_follow)
        self.pres_stop = QPushButton("Stop presentation")
        self.pres_stop.setObjectName("dangerButton")
        self.pres_stop.clicked.connect(self._stop_presentation)
        pl.addWidget(self.pres_stop)
        self.pres_exit = QPushButton("Exit")
        self.pres_exit.setObjectName("ghostButton")
        self.pres_exit.clicked.connect(self._exit_presentation_local)
        pl.addWidget(self.pres_exit)
        self.presentation_bar.setFixedHeight(48)
        self.presentation_bar.hide()
        self.view.installEventFilter(self)

    # ==================================================================
    # Signals
    # ==================================================================
    def _wire_signals(self) -> None:
        self.welcome.createRequested.connect(self._create_session)
        self.welcome.joinRequested.connect(self._join_session)
        self.welcome.resumeRequested.connect(self._resume_session)
        self.welcome.settingsRequested.connect(self._show_settings)
        self.welcome.serverHelpRequested.connect(self._show_server_help)

        self.manager.state_changed.connect(self._on_state_changed)
        self.manager.welcome.connect(self._on_welcome)
        self.manager.event.connect(self._on_event)
        self.manager.presence.connect(self._on_presence)
        self.manager.chat.connect(self._on_chat)
        self.manager.activity.connect(self._on_activity)
        self.manager.canvas_state.connect(self._on_canvas_state)
        self.manager.sync_complete.connect(self._on_sync_complete)
        self.manager.ack.connect(self._on_ack)
        self.manager.protocol_error.connect(self._on_protocol_error)
        self.manager.server_stats.connect(self._on_server_stats)
        self.manager.presentation.connect(self._on_presentation_state)
        self.manager.cursor.connect(self.view.update_remote_cursor)
        self.manager.laser.connect(self.view.add_laser_point)
        self.manager.kicked.connect(self._on_kicked)
        self.manager.system.connect(self._on_system)
        self.manager.left.connect(self._on_left)
        self.manager.latency.connect(self._on_latency)
        self.manager.metrics_changed.connect(self._on_metrics)
        self.manager.outbox_changed.connect(self._on_outbox)
        self.manager.toast.connect(self.toast.show)
        self.manager.reconnect_scheduled.connect(self._on_reconnect_scheduled)
        self.manager.version_restored.connect(
            lambda payload: self.toast.show(f"Version restored: {payload.get('name', '')}", "success")
        )

        self.view.cursorMoved.connect(self._on_cursor)
        self.view.createObjectRequested.connect(self._create_object)
        self.view.updateObjectRequested.connect(self._update_object)
        self.view.deleteObjectsRequested.connect(self._delete_objects)
        self.view.strokeAppendRequested.connect(self._append_stroke)
        self.view.strokeEndRequested.connect(self._end_stroke)
        self.view.textInsertRequested.connect(self._insert_text)
        self.view.stickyInsertRequested.connect(self._insert_sticky)
        self.view.mindNodeInsertRequested.connect(self._insert_mindnode)
        self.view.imageInsertRequested.connect(self._insert_image)
        self.view.textEditRequested.connect(self._edit_object_text)
        self.view.selectionObjectsChanged.connect(self._on_selection)
        self.view.zoomChanged.connect(self._on_zoom)
        self.view.laserMoved.connect(self._send_laser)
        self.view.viewportChanged.connect(self._on_host_viewport)
        self.view.notice.connect(self.toast.show)

        self.sidebar.participants.manageRequested.connect(self._show_participants)
        self.sidebar.chat.messageSubmitted.connect(self._send_chat)
        self.sidebar.properties.styleChangeRequested.connect(self._apply_selected_style)
        self.sidebar.properties.actionRequested.connect(self._property_action)
        self.sidebar.properties.defaultStyleChanged.connect(self._default_style_changed)

        self.tool_options.colorChanged.connect(self._stroke_color_changed)
        self.tool_options.fillChanged.connect(self._fill_color_changed)
        self.tool_options.stickyChanged.connect(self._sticky_color_changed)
        self.tool_options.widthChanged.connect(self._width_changed)
        self.tool_options.gridToggled.connect(self._grid_toggled)
        self.tool_options.snapToggled.connect(self._snap_toggled)
        self.tool_options.templatesRequested.connect(self._show_templates)
        self.tool_options.laserToggled.connect(self._laser_toggled)

        self.model.changed.connect(self._on_model_changed)
        self.model.object_updated.connect(self._on_object_updated)

    def _build_shortcuts(self) -> None:
        def add(sequence: str | QKeySequence, callback, parent=None) -> QShortcut:
            shortcut = QShortcut(QKeySequence(sequence), parent or self.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
            return shortcut

        tool_keys = {
            "V": TOOL_SELECT,
            "H": TOOL_PAN,
            "P": TOOL_PENCIL,
            "B": TOOL_BRUSH,
            "E": TOOL_ERASER,
            "T": TOOL_TEXT,
            "R": TOOL_RECT,
            "O": TOOL_ELLIPSE,
            "A": TOOL_ARROW,
            "S": TOOL_STICKY,
            "M": TOOL_MINDNODE,
            "C": TOOL_CONNECTOR,
            "L": TOOL_LASER,
            "D": TOOL_DIAMOND,
        }
        for key, tool in tool_keys.items():
            add(key, lambda t=tool: self._select_tool(t))
        add("Ctrl+Z", self._undo)
        add("Ctrl+Shift+Z", self._redo)
        add("Ctrl+Y", self._redo)
        add("Ctrl+D", self.view.duplicate_selected)
        add("Ctrl+0", self.view.reset_zoom)
        add("Ctrl++", self.view.zoom_in)
        add("Ctrl+=", self.view.zoom_in)
        add("Ctrl+-", self.view.zoom_out)
        global_shortcuts = [
            ("F1", self._show_help),
            ("Ctrl+K", self._command_palette),
            ("Ctrl+S", self._save_version),
            ("Ctrl+E", self._show_export),
            ("Ctrl+G", lambda: self.tool_options.grid_button.toggle()),
        ]
        for sequence, callback in global_shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self._pres_prev_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._pres_prev_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._pres_prev_shortcut.activated.connect(lambda: self._navigate_presentation(-1))
        self._pres_prev_shortcut.setEnabled(False)
        self._pres_next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self._pres_next_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._pres_next_shortcut.activated.connect(lambda: self._navigate_presentation(1))
        self._pres_next_shortcut.setEnabled(False)
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._esc_shortcut.activated.connect(self._exit_presentation_local)
        self._esc_shortcut.setEnabled(False)

    # ==================================================================
    # Connection flow
    # ==================================================================
    def _create_session(self, values: dict[str, Any]) -> None:
        self._auth_payload = dict(values)
        self.welcome.set_busy(True, "Creating your board…")
        self.manager.connect_for_auth(
            values["host"],
            int(values["port"]),
            "CREATE_SESSION",
            {
                "session_name": values["session_name"],
                "username": values["username"],
                "password": values.get("password", ""),
            },
        )

    def _join_session(self, values: dict[str, Any]) -> None:
        self._auth_payload = dict(values)
        self.welcome.set_busy(True, "Joining the board…")
        self.manager.connect_for_auth(
            values["host"],
            int(values["port"]),
            "JOIN_SESSION",
            {
                "code": values["code"],
                "username": values["username"],
                "password": values.get("password", ""),
                "role": values.get("role", "editor"),
            },
        )

    def _resume_session(self, values: dict[str, Any]) -> None:
        self._auth_payload = dict(values)
        self.welcome.set_busy(True, "Resuming your session…")
        self.manager.connect_for_auth(
            values["host"],
            int(values["port"]),
            "RESUME_SESSION",
            {"code": values["code"], "token": values["token"]},
            resume_token=values["token"],
        )

    def _on_state_changed(self, state: str, detail: str) -> None:
        state_label = {
            "connected": "Connected",
            "connecting": "Connecting",
            "authenticating": "Authenticating",
            "reconnecting": "Reconnecting",
            "disconnected": "Offline",
        }.get(state, state.title())
        state_kind = {
            "connected": "online",
            "connecting": "connecting",
            "authenticating": "connecting",
            "reconnecting": "reconnecting",
            "disconnected": "offline",
        }.get(state, "offline")
        self.connection_pill.set_state(state_kind, state_label)
        if self.stack.currentIndex() == 0:
            if state == "disconnected":
                self.welcome.set_busy(False)
            return
        if state == "connected":
            self.offline_banner.hide()
            self.bottom_connection.setText("●  Connected")
            self.bottom_connection.setStyleSheet("color: #22c55e; font-size: 11px;")
            self.bottom_endpoint.setText(f"{self.manager.host}:{self.manager.port}")
            self.save_indicator.setText("Synced")
            self.save_indicator.setProperty("role", "faint")
        elif state in ("reconnecting", "connecting", "authenticating"):
            self.offline_banner.show()
            self.offline_label.setText(detail or "Connection lost — edits are queued.")
            self.bottom_connection.setText("●  Reconnecting")
            self.bottom_connection.setStyleSheet("color: #f59e0b; font-size: 11px;")
        else:
            self.offline_banner.show()
            self.offline_label.setText(detail or "Disconnected. Edits are queued locally.")
            self.bottom_connection.setText("●  Offline")
            self.bottom_connection.setStyleSheet("color: #f43f5e; font-size: 11px;")
            self.save_indicator.setText("Offline")
        self._update_interaction_state()

    def _on_welcome(self, payload: dict[str, Any]) -> None:
        first = not self._session_active
        self._session_active = True
        self._self_participant = payload.get("self", {}) or {}
        self._role = self._self_participant.get("role", ROLE_EDITOR)
        session = payload.get("session", {}) or {}
        self.board_title.setText(session.get("name", "Untitled board"))
        self.code_label.setText(session.get("code", ""))
        self.code_label.setToolTip(f"Room code: {session.get('code', '')}")
        self.role_badge.setText({"host": "Host", "editor": "Editor", "viewer": "Viewer"}.get(self._role, self._role.title()))
        self.view.set_self_participant(self._self_participant)
        self.sidebar.participants.set_self(self._self_participant)
        self.sidebar.chat.set_self(self._self_participant)
        self.sidebar.properties.set_self_role(self._role)
        self._update_participant_colors()

        board = payload.get("board")
        old_camera = None
        if self._session_active and self._presentation_active:
            old_camera = self.view.transform()
        if isinstance(board, dict):
            center = self.view.mapToScene(self.view.viewport().rect().center())
            transform = self.view.transform()
            self.model.load_board(board, seq=payload.get("seq") if isinstance(payload.get("seq"), int) else None)
            if not first:
                self.view.setTransform(transform)
                self.view.centerOn(center)
        self.model.locked = bool(payload.get("locked", False))
        self._last_locked = self.model.locked
        participants = payload.get("participants", [])
        if isinstance(participants, list):
            self._on_presence(participants)
        chat = payload.get("chat", [])
        if isinstance(chat, list):
            enriched = []
            for message in chat:
                item = dict(message)
                item["color"] = self._color_for(message.get("sender_id"))
                enriched.append(item)
            self.sidebar.chat.set_history(enriched)
        activity = payload.get("activity", [])
        if isinstance(activity, list):
            self.sidebar.activity.set_history(activity)
        self._can_undo = bool(payload.get("can_undo", False))
        self._can_redo = bool(payload.get("can_redo", False))
        self._update_undo_redo()
        self._following_presenter = bool(self.store.settings.presentation_follow)
        presentation = payload.get("presentation", {})
        if isinstance(presentation, dict) and presentation.get("active"):
            self._presentation_state = presentation
            if self._role != ROLE_HOST and not self._presentation_active:
                self._enter_presentation(host=False, presenter=presentation.get("presenter_name", "Host"))
            if self._following_presenter and presentation.get("viewport"):
                self.view.apply_camera_viewport(presentation["viewport"])
        self.store.settings.remember_session(
            session.get("code", ""),
            session.get("name", ""),
            self.manager.token,
            self.manager.host,
            self.manager.port,
        )
        self.store.settings.username = self._self_participant.get("name", self.store.settings.username)
        self.store.save()
        self.stack.setCurrentIndex(1)
        self.welcome.set_busy(False)
        self.setWindowTitle(f"{session.get('name', 'Board')} · {APP_NAME}")
        self._update_interaction_state()
        self._mark_saved()
        self._update_object_count()
        self.setWindowIcon(app_icon())
        if first:
            QTimer.singleShot(120, self._after_join)
        self._position_overlays()
        self.manager.request_server_stats()

    def _after_join(self) -> None:
        self.view.fit_content()
        if self._role == ROLE_HOST and self.manager.session_code:
            self.toast.show(
                f"Board ready · share room code {self.manager.session_code} and server {self.manager.host}",
                "success",
                5200,
            )
        else:
            self.toast.show("You are connected. Double-click shapes to edit their labels.", "info", 3800)

    # ==================================================================
    # Incoming collaboration state
    # ==================================================================
    def _on_event(self, message: dict[str, Any]) -> None:
        self.model.apply_event_message(message)
        payload = message.get("payload", {}) or {}
        action = payload.get("action")
        if action == ACTION_BOARD_LOCKED:
            locked = bool((payload.get("body") or {}).get("locked"))
            self.toast.show(
                "The host locked the board" if locked else "The host unlocked the board",
                "warning" if locked else "success",
            )
            self._last_locked = locked
            self._update_interaction_state()
        actor_id = payload.get("actor_id")
        body = payload.get("body", {}) or {}
        if action == ACTION_USER_JOINED and actor_id != self._self_participant.get("id"):
            self.toast.show(f"{payload.get('actor_name', 'A participant')} joined the board", "info", 2200)
        elif action == ACTION_USER_LEFT and body.get("id") != self._self_participant.get("id"):
            name = body.get("name", payload.get("actor_name", "A participant"))
            self.toast.show(f"{name} left the board", "info", 2200)
        if action == ACTION_OBJECT_CREATED and actor_id == self._self_participant.get("id"):
            self._mark_dirty()
        self._update_object_count()

    def _on_presence(self, participants: list[dict[str, Any]]) -> None:
        self._participant_colors = {
            p.get("id"): p.get("color", "#a1a1aa") for p in participants if p.get("id")
        }
        self.sidebar.participants.set_participants(participants)
        self.view.set_participants(participants)
        me = next((p for p in participants if p.get("id") == self._self_participant.get("id")), None)
        if me:
            old_role = self._role
            self._role = me.get("role", self._role)
            self._self_participant.update(me)
            self.role_badge.setText(
                {"host": "Host", "editor": "Editor", "viewer": "Viewer"}.get(self._role, self._role.title())
            )
            if old_role != self._role:
                self.sidebar.properties.set_self_role(self._role)
                self._update_interaction_state()
                self.toast.show(f"Your role is now {self._role.title()}", "info")
        self._top_people_count(participants)

    def _on_chat(self, message: dict[str, Any]) -> None:
        item = dict(message)
        item.setdefault("color", self._color_for(message.get("sender_id")))
        self.sidebar.chat.add_message(item)
        if (
            self.sidebar.tabs.currentWidget() is not self.sidebar.chat
            and message.get("sender_id") != self._self_participant.get("id")
        ):
            text = str(message.get("text", ""))
            preview = text[:70] + ("…" if len(text) > 70 else "")
            self.toast.show(f"{message.get('sender', 'Someone')}: {preview}", "info", 2800)

    def _on_activity(self, entry: dict[str, Any]) -> None:
        self.sidebar.activity.add_activity(entry)

    def _on_canvas_state(self, payload: dict[str, Any]) -> None:
        board = payload.get("board")
        if not isinstance(board, dict):
            return
        center = self.view.mapToScene(self.view.viewport().rect().center())
        transform = self.view.transform()
        self.model.load_board(board, seq=payload.get("seq") if isinstance(payload.get("seq"), int) else None)
        self.view.setTransform(transform)
        self.view.centerOn(center)
        self._update_object_count()
        self._mark_saved()
        if payload.get("reason") == "version_restore":
            self.toast.show(f"Restored version: {payload.get('version', '')}", "success")
        self._update_interaction_state()

    def _on_sync_complete(self, payload: dict[str, Any]) -> None:
        self.offline_banner.hide()
        self.bottom_sync.setText("In sync")
        self.save_indicator.setText("Synced")
        self.toast.show("Back in sync with the server", "success")
        self._update_object_count()
        self._update_interaction_state()

    def _on_ack(self, ack: dict[str, Any]) -> None:
        payload = ack.get("payload", {}) or {}
        if "can_undo" in payload:
            self._can_undo = bool(payload.get("can_undo"))
            self._can_redo = bool(payload.get("can_redo"))
            self._update_undo_redo()
        if ack.get("latency_ms"):
            self.bottom_sync.setText("In sync")
        self._mark_saved()

    def _on_protocol_error(self, error: dict[str, Any]) -> None:
        code = error.get("code", "ERROR")
        message = error.get("message", "Server rejected the request")
        if self.stack.currentIndex() == 0:
            self.welcome.show_error(message)
            self.welcome.set_busy(False)
            return
        if code in {"REVISION_CONFLICT", "UNDO_CONFLICT"}:
            self.toast.show(message, "warning", 5200)
            self.manager.send("REQUEST_SYNC", {"since_seq": 0, "full": True}, queue_if_offline=False)
            QTimer.singleShot(500, self._update_interaction_state)
            if code == "UNDO_CONFLICT":
                self._can_undo = False
                self._can_redo = False
                self._update_undo_redo()
        elif code in {"BOARD_LOCKED", "FORBIDDEN", "UNAUTHORIZED"}:
            self.toast.show(message, "warning", 4200)
            self._update_interaction_state()
        elif code in {"AUTH_REQUIRED", "SESSION_NOT_FOUND", "WRONG_PASSWORD"}:
            QMessageBox.warning(self, "Session ended", message)
            self._return_to_lobby()
        else:
            self.toast.show(message, "error", 4200)
        if self.stack.currentIndex() == 1 and self.manager.state == "connected":
            self.manager.send("REQUEST_SYNC", {"since_seq": 0, "full": True}, queue_if_offline=False)
        self._mark_saved()

    def _on_server_stats(self, payload: dict[str, Any]) -> None:
        self._last_server_stats = payload
        if self._role == ROLE_HOST:
            online = payload.get("online_clients")
            if online is not None:
                self._top_people_count_from_count(int(online))

    def _on_kicked(self, reason: str) -> None:
        QMessageBox.warning(self, "Removed from session", reason)
        self._return_to_lobby()

    def _on_left(self) -> None:
        if self.stack.currentIndex() == 1:
            self._return_to_lobby()

    def _on_system(self, payload: dict[str, Any]) -> None:
        message = payload.get("message", "")
        if message:
            self.toast.show(message, "info")

    def _on_latency(self, value: float) -> None:
        self.bottom_latency.setText(f"Latency {value:.1f} ms")

    def _on_metrics(self, metrics: dict[str, Any]) -> None:
        self._last_server_stats = metrics.get("server", self._last_server_stats)
        state = metrics.get("state")
        if state == "connected":
            self.bottom_connection.setText("●  Connected")
            self.bottom_connection.setStyleSheet("color: #22c55e; font-size: 11px;")
        self.bottom_endpoint.setText(f"{metrics.get('host', '')}:{metrics.get('port', '')}")
        rtt = float(metrics.get("rtt_ms", 0) or 0)
        if rtt:
            self.bottom_latency.setText(f"Latency {rtt:.1f} ms")
        outbox = int(metrics.get("outbox", 0) or 0)
        self.bottom_outbox.setText(f"{outbox} queued" if outbox else "")
        self.bottom_zoom.setText(f"{self.view.zoom_percent()}%")
        self.bottom_objects.setText(f"{self.model.object_count()} objects")
        if outbox and state != "connected":
            self.offline_label.setText(f"{outbox} edit(s) queued — they will replay after reconnection.")
        if state == "connected":
            self.bottom_sync.setText("In sync")

    def _on_outbox(self, count: int) -> None:
        self.bottom_outbox.setText(f"{count} queued" if count else "")
        if count:
            self.offline_label.setText(f"{count} edit(s) queued — they will replay after reconnection.")
        self._update_interaction_state()

    def _on_reconnect_scheduled(self, attempt: int, delay: float) -> None:
        if self.stack.currentIndex() == 1:
            self.toast.show(f"Connection lost. Reconnecting in {delay:.1f}s…", "warning", 2600)

    def _send_chat(self, text: str) -> None:
        if not text.strip():
            return
        self.manager.send("CHAT", {"text": text.strip()[:1000]})
        self._mark_dirty()

    # ==================================================================
    # Presence / tools / style
    # ==================================================================
    def _select_tool(self, tool: str) -> None:
        if self._is_read_only() and tool not in (TOOL_SELECT, TOOL_PAN):
            self.toast.show("You have view-only access to this board", "warning")
            tool = TOOL_SELECT
        self._current_tool = tool
        self.view.set_tool(tool)
        label = TOOL_LABELS.get(tool, tool.title())
        self.tool_options.set_tool(tool, label)
        self._sync_tool_buttons(tool)
        if self.manager.state == "connected":
            self.manager.send("SET_PROFILE", {"tool": tool}, queue_if_offline=False)

    def _sync_tool_buttons(self, tool: str) -> None:
        button = self._tool_buttons.get(tool)
        if button is not None:
            button.setChecked(True)
        for state in (TOOL_RECT, TOOL_ROUNDED_RECT, TOOL_ELLIPSE, TOOL_CIRCLE, TOOL_DIAMOND, TOOL_TRIANGLE):
            if tool == state:
                self.shape_button.setIcon(icon({
                    TOOL_RECT: "square",
                    TOOL_ROUNDED_RECT: "rounded",
                    TOOL_ELLIPSE: "circle",
                    TOOL_CIRCLE: "circle",
                    TOOL_DIAMOND: "diamond",
                    TOOL_TRIANGLE: "triangle",
                }[tool], "#f4f4f5", 19))
                self.shape_button.setChecked(True)
        if tool in (TOOL_LINE, TOOL_ARROW):
            self.line_button.setIcon(icon("arrow" if tool == TOOL_ARROW else "line", "#f4f4f5", 19))
            self.line_button.setChecked(True)
        if tool in (TOOL_FLOW_START, TOOL_FLOW_PROCESS, TOOL_FLOW_DECISION, TOOL_FLOW_IO):
            self.flow_button.setChecked(True)
        if tool == TOOL_LASER:
            self.tool_options.set_laser(True)
        else:
            self.tool_options.set_laser(False)

    def _stroke_color_changed(self, color: str) -> None:
        self.store.settings.stroke_color = color
        self.store.save()
        self.view.set_style(stroke=color)
        if self.view.selected_objects() and not self._is_read_only():
            self.view.update_selected_style({"style": {"stroke": color, "text_color": color}})

    def _fill_color_changed(self, color: str) -> None:
        self.store.settings.fill_color = color
        self.store.save()
        self.view.set_style(fill=color)
        if self.view.selected_objects() and not self._is_read_only():
            self.view.update_selected_style({"style": {"fill": color}})

    def _sticky_color_changed(self, color: str) -> None:
        self.store.settings.sticky_color = color
        self.store.save()
        self.view.set_style(sticky=color)

    def _width_changed(self, width: int) -> None:
        self.store.settings.stroke_width = width
        self.store.save()
        self.view.set_style(width=width)
        if self.view.selected_objects() and not self._is_read_only():
            self.view.update_selected_style({"style": {"stroke_width": width}})

    def _grid_toggled(self, visible: bool) -> None:
        self.view.set_grid(visible)
        self.store.settings.show_grid = visible
        self.store.save()

    def _snap_toggled(self, enabled: bool) -> None:
        self.view.set_snap(enabled)
        self.store.settings.snap_to_grid = enabled
        self.store.save()

    def _laser_toggled(self, active: bool) -> None:
        self._select_tool(TOOL_LASER if active else TOOL_SELECT)

    def _apply_selected_style(self, patch: dict[str, Any]) -> None:
        if self._is_read_only():
            return
        self.view.update_selected_style(patch)
        self._mark_dirty()

    def _default_style_changed(self, patch: dict[str, Any]) -> None:
        if "stroke" in patch:
            self.view.set_style(stroke=patch["stroke"])
        if "fill" in patch:
            self.view.set_style(fill=patch["fill"])
        if "stroke_width" in patch:
            self.view.set_style(width=int(patch["stroke_width"]))
            self.tool_options.set_width(int(patch["stroke_width"]))
        if "font_size" in patch:
            self.view.set_style(font_size=int(patch["font_size"]))
        self._mark_dirty()

    def _on_selection(self, objects: list[dict[str, Any]]) -> None:
        self.sidebar.properties.set_selection(objects)

    def _on_object_updated(self, obj: dict[str, Any]) -> None:
        if self.view.selected_objects():
            # Keep the properties panel in sync with the newest revision.
            self.sidebar.properties.set_selection(self.view.selected_objects())

    def _on_model_changed(self) -> None:
        self._update_object_count()
        if self.model.locked != self._last_locked:
            self._last_locked = self.model.locked
            self._update_interaction_state()

    def _on_cursor(self, x: float, y: float, tool: str) -> None:
        if self.manager.state != "connected":
            return
        self.manager.send(
            "CURSOR_UPDATE",
            {"cursor": {"x": x, "y": y}, "tool": tool},
            queue_if_offline=False,
        )

    def _send_laser(self, x: float, y: float) -> None:
        if self.manager.state != "connected" or self._is_read_only():
            return
        self.manager.send("LASER", {"cursor": {"x": x, "y": y}}, queue_if_offline=False)

    def _on_zoom(self, value: float) -> None:
        percent = int(round(value * 100))
        self.zoom_label.setText(f"{percent}%")
        self.bottom_zoom.setText(f"{percent}%")
        self._position_overlays()

    # ==================================================================
    # Outgoing object operations
    # ==================================================================
    @staticmethod
    def _wire_object(obj: dict[str, Any]) -> dict[str, Any]:
        clean = copy.deepcopy(obj)
        for key in list(clean):
            if key.startswith("_"):
                clean.pop(key, None)
        clean.pop("owner_id", None)
        clean.pop("owner_name", None)
        clean.pop("rev", None)
        clean.pop("created_at", None)
        clean.pop("updated_at", None)
        return clean

    def _create_object(self, obj: dict[str, Any]) -> None:
        self.manager.send("CREATE_OBJECT", {"object": self._wire_object(obj)})
        self._mark_dirty()

    def _update_object(self, object_id: str, patch: dict[str, Any], base_rev: int) -> None:
        self.manager.send(
            "UPDATE_OBJECT",
            {"object_id": object_id, "patch": patch, "base_rev": base_rev},
        )
        self._mark_dirty()

    def _delete_objects(self, ids: list[str]) -> None:
        if not ids:
            return
        self.manager.send("DELETE_OBJECTS", {"ids": ids})
        self._mark_dirty()

    def _append_stroke(self, object_id: str, points: list[list[float]]) -> None:
        self.manager.send("STROKE_APPEND", {"object_id": object_id, "points": points})
        self._mark_dirty()

    def _end_stroke(self, object_id: str) -> None:
        if object_id:
            self.manager.send("STROKE_END", {"object_id": object_id})
            self._mark_dirty()

    def _insert_text(self, x: float, y: float) -> None:
        if self._is_read_only():
            return
        dialog = TextEditorDialog("Add text", "", placeholder="Type your text…", parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.text():
            width = max(180, min(520, 14 * max(10, len(dialog.text().split("\n")[0]))))
            self.view.create_text_object(x, y, dialog.text(), kind=KIND_TEXT, w=width, h=80)
            self._mark_dirty()

    def _insert_sticky(self, x: float, y: float) -> None:
        if self._is_read_only():
            return
        dialog = TextEditorDialog("New sticky note", "", placeholder="What should the team remember?", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = dialog.text() or "New note"
        self.view.create_text_object(
            x, y, text, kind=KIND_STICKY, w=210, h=160, sticky_color=self.store.settings.sticky_color
        )
        self._mark_dirty()

    def _insert_mindnode(self, x: float, y: float, parent_id: str) -> None:
        if self._is_read_only():
            return
        dialog = TextEditorDialog(
            "Mind-map node" if parent_id else "Central topic",
            "",
            placeholder="Idea or topic…",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = dialog.text() or "New idea"
        self.view.create_text_object(
            x, y, text, kind=KIND_MINDNODE, w=max(160, min(320, 16 * len(text))), h=62, parent_id=parent_id
        )
        self._mark_dirty()

    def _insert_image(self, x: float, y: float) -> None:
        if self._is_read_only():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Insert image", str(Path.home()), "Images (*.png *.jpg *.jpeg)"
        )
        if not path:
            return
        try:
            payload, width, height = load_image(path)
        except (ImageError, OSError) as exc:
            QMessageBox.warning(self, "Image not inserted", str(exc))
            return
        w, h = display_size(width, height)
        self.view.create_image_object(x, y, payload, w, h)
        self._mark_dirty()
        self.toast.show(f"Image inserted ({width}×{height}px)", "success")

    def _edit_object_text(self, object_id: str) -> None:
        obj = self.model.objects.get(object_id)
        if not obj:
            return
        if self._is_read_only():
            self.toast.show("Viewers cannot edit objects", "warning")
            return
        if obj.get("kind") == KIND_CONNECTOR:
            title = "Edit connector label"
            initial = obj.get("connector", {}).get("label", "")
        else:
            title = "Edit note" if obj.get("kind") == KIND_STICKY else (
                "Edit mind-map node" if obj.get("kind") == KIND_MINDNODE else "Edit text"
            )
            initial = obj.get("text", "")
        dialog = TextEditorDialog(title, initial, parent=self, max_length=300 if obj.get("kind") == KIND_CONNECTOR else 8000)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.view.update_object_text(object_id, dialog.text())
            self._mark_dirty()

    def _undo(self) -> None:
        if self._is_read_only() or not self._can_undo:
            return
        self.manager.send("UNDO", {})
        self._can_undo = False
        self._update_undo_redo()
        self._mark_dirty()

    def _redo(self) -> None:
        if self._is_read_only() or not self._can_redo:
            return
        self.manager.send("REDO", {})
        self._can_redo = False
        self._update_undo_redo()
        self._mark_dirty()

    def _update_undo_redo(self) -> None:
        enabled = not self._is_read_only() and self.manager.state == "connected"
        self.undo_button.setEnabled(enabled and self._can_undo)
        self.redo_button.setEnabled(enabled and self._can_redo)

    def _property_action(self, action: str) -> None:
        if action == "delete":
            ids = [obj["id"] for obj in self.view.selected_objects()]
            if len(ids) >= 5:
                answer = QMessageBox.question(
                    self, "Delete objects?", f"Delete {len(ids)} selected objects?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.view.delete_selected()
        elif action == "duplicate":
            self.view.duplicate_selected()
        elif action == "group":
            objects = self.view.selected_objects()
            if len(objects) >= 2:
                group_id = uuid.uuid4().hex
                self.view.update_selected_style({"group_id": group_id})
                self.toast.show("Objects grouped", "success")
        elif action in ("align_left", "align_center", "align_top"):
            objects = self.view.selected_objects()
            if len(objects) >= 2:
                if action == "align_left":
                    target = min(float(o.get("x", 0)) for o in objects)
                    for obj in objects:
                        self.view.move_object_to(obj["id"], target, float(obj.get("y", 0)))
                elif action == "align_top":
                    target = min(float(obj.get("y", 0)) for obj in objects)
                    for obj in objects:
                        self.view.move_object_to(obj["id"], float(obj.get("x", 0)), target)
                else:
                    left = min(float(o.get("x", 0)) for o in objects)
                    right = max(float(o.get("x", 0)) + float(o.get("w", 0)) for o in objects)
                    center = (left + right) / 2
                    for obj in objects:
                        self.view.move_object_to(
                            obj["id"], center - float(obj.get("w", 0)) / 2, float(obj.get("y", 0))
                        )
        elif action == "bring_forward":
            objects = self.view.selected_objects()
            if objects:
                self.view.update_selected_style({"z": max(int(o.get("z", 0)) for o in objects) + 1})
        elif action == "send_backward":
            objects = self.view.selected_objects()
            if objects:
                self.view.update_selected_style({"z": max(0, min(int(o.get("z", 0)) for o in objects) - 1)})
        elif action == "edit_text":
            objects = self.view.selected_objects()
            if objects:
                self._edit_object_text(objects[0]["id"])
        self._mark_dirty()

    # ==================================================================
    # Presentation
    # ==================================================================
    def _start_presentation(self) -> None:
        if self._role != ROLE_HOST:
            return
        self._enter_presentation(host=True, presenter=self._self_participant.get("name", "Host"))
        QTimer.singleShot(
            180,
            lambda: self.manager.send(
                "PRESENTATION_START",
                {"viewport": self.view.camera_viewport()},
            ),
        )

    def _enter_presentation(self, *, host: bool, presenter: str) -> None:
        if self._presentation_active:
            self._presentation_is_host = host
            return
        self._presentation_active = True
        self._presentation_is_host = host
        self._presentation_host_name = presenter
        self._presentation_opted_out = False
        self._presentation_slide = 0
        self._preexisting_maximized = self.isMaximized()
        self._presentation_previous_geometry = self.geometry()
        self.top_bar.hide()
        self.left_toolbar.hide()
        self.tool_options.parentWidget().hide() if self.tool_options.parentWidget() else None
        self.sidebar.hide()
        self.bottom_bar.hide()
        self.offline_banner.hide()
        self.presentation_bar.show()
        self.zoom_bar.hide()
        self.minimap_frame.hide()
        self.pres_prev.setVisible(host)
        self.pres_next.setVisible(host)
        self.pres_stop.setVisible(host)
        self.pres_exit.setVisible(not host)
        self.pres_follow.setVisible(not host)
        self.pres_follow.setChecked(self._following_presenter)
        self.presentation_label.setText("PRESENTING LIVE" if host else "PRESENTATION MODE")
        self.presentation_page.setText(
            f"Sharing your view" if host else f"Following {presenter}"
        )
        self.view.set_read_only(True)
        self.view.set_host_presenting(host)
        self._pres_prev_shortcut.setEnabled(host)
        self._pres_next_shortcut.setEnabled(host)
        self._esc_shortcut.setEnabled(True)
        self.showFullScreen()
        QTimer.singleShot(120, self._position_overlays)

    def _exit_presentation_local(self) -> None:
        if not self._presentation_active:
            return
        host = self._presentation_is_host
        self._presentation_active = False
        self._presentation_is_host = False
        if not host:
            self._presentation_opted_out = True
        self.presentation_bar.hide()
        self.top_bar.show()
        self.left_toolbar.show()
        if self.tool_options.parentWidget():
            self.tool_options.parentWidget().show()
        self.sidebar.show()
        self.bottom_bar.show()
        self.zoom_bar.show()
        self.view.set_host_presenting(False)
        self._pres_prev_shortcut.setEnabled(False)
        self._pres_next_shortcut.setEnabled(False)
        self._esc_shortcut.setEnabled(False)
        if host:
            self._presentation_opted_out = False
            self.manager.send("PRESENTATION_STOP", {})
        self.showNormal()
        if self._presentation_previous_geometry is not None:
            self.setGeometry(self._presentation_previous_geometry)
        if self._preexisting_maximized:
            self.showMaximized()
        self._presentation_previous_geometry = None
        self._update_interaction_state()
        if self._presentation_state.get("active"):
            self._btn_join_presentation.show()
        self._position_overlays()

    def _stop_presentation(self) -> None:
        if not self._presentation_is_host:
            return
        self._exit_presentation_local()

    def _on_presentation_state(self, payload: dict[str, Any]) -> None:
        self._presentation_state = payload
        active = bool(payload.get("active"))
        if active:
            presenter = payload.get("presenter_name", "Host")
            if not self._presentation_active:
                if self._role != ROLE_HOST and not self._presentation_opted_out:
                    self._enter_presentation(host=False, presenter=presenter)
            self.presentation_page.setText(
                "Sharing your view" if self._presentation_is_host else f"Following {presenter}"
            )
            viewport = payload.get("viewport")
            if not self._presentation_is_host and viewport and self._following_presenter:
                self.view.apply_camera_viewport(viewport)
            self._update_interaction_state()
        else:
            self._presentation_opted_out = False
            if self._presentation_active:
                self._presentation_active = False
                self._presentation_is_host = False
                self.presentation_bar.hide()
                self.top_bar.show()
                self.left_toolbar.show()
                if self.tool_options.parentWidget():
                    self.tool_options.parentWidget().show()
                self.sidebar.show()
                self.bottom_bar.show()
                self.zoom_bar.show()
                self.view.set_host_presenting(False)
                self._pres_prev_shortcut.setEnabled(False)
                self._pres_next_shortcut.setEnabled(False)
                self._esc_shortcut.setEnabled(False)
                self.showNormal()
                if self._presentation_previous_geometry is not None:
                    self.setGeometry(self._presentation_previous_geometry)
                self._presentation_previous_geometry = None
                self.toast.show("Presentation ended", "info")
            self._btn_join_presentation.hide()
            self._update_interaction_state()

    def _toggle_follow(self, checked: bool) -> None:
        self._following_presenter = checked
        self.store.settings.presentation_follow = checked
        self.store.save()
        if checked and self._presentation_state.get("viewport"):
            self.view.apply_camera_viewport(self._presentation_state["viewport"])
        self.pres_follow.setText("Following" if checked else "Free look")

    def _rejoin_presentation(self) -> None:
        state = self._presentation_state or {}
        if state.get("active"):
            self._presentation_opted_out = False
            self._enter_presentation(host=False, presenter=state.get("presenter_name", "Host"))
            if self._following_presenter and state.get("viewport"):
                self.view.apply_camera_viewport(state["viewport"])

    def _navigate_presentation(self, direction: int) -> None:
        if not self._presentation_active or not self._presentation_is_host:
            return
        viewport = self.view.camera_viewport()
        step = viewport["w"] * 0.8 * direction
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.view.centerOn(center.x() + step, center.y())
        self._presentation_slide += direction
        self.presentation_page.setText(f"View {max(1, self._presentation_slide + 1)}")
        self.manager.send("PRESENTATION_UPDATE", {"viewport": self.view.camera_viewport()})

    def _on_host_viewport(self, viewport: dict[str, Any]) -> None:
        if not self._presentation_active or not self._presentation_is_host:
            return
        if self.manager.state != "connected":
            return
        now = time.monotonic()
        if now - self._last_presentation_sent < 0.22:
            return
        self._last_presentation_sent = now
        self.manager.send("PRESENTATION_UPDATE", {"viewport": viewport}, queue_if_offline=False)

    # ==================================================================
    # Board controls / dialogs
    # ==================================================================
    def _toggle_lock(self) -> None:
        if self._role != ROLE_HOST:
            self._btn_lock.setChecked(self.model.locked)
            return
        self.manager.send("SET_BOARD_LOCK", {"locked": not self.model.locked})

    def _copy_code(self) -> None:
        code = self.manager.session_code
        if code:
            QGuiApplication.clipboard().setText(code)
            self.toast.show(f"Room code {code} copied", "success", 1800)

    def _show_versions(self) -> None:
        self._open_dialog("versions", lambda: VersionHistoryDialog(self.manager, self))

    def _show_network(self) -> None:
        self._open_dialog("network", lambda: NetworkDashboardDialog(self.manager, self))

    def _show_participants(self) -> None:
        self._open_dialog("participants", lambda: ParticipantManagerDialog(self.manager, self))

    def _open_dialog(self, key: str, factory) -> None:
        existing = self._dialogs.get(key)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = factory()
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda: self._dialogs.pop(key, None))
        self._dialogs[key] = dialog
        dialog.show()

    def _show_export(self) -> None:
        dialog = ExportImportDialog(self.view, self.model, self.manager, self.store, self)
        dialog.exec()

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.store, self)
        dialog.settings_changed.connect(self._settings_changed)
        dialog.exec()

    def _settings_changed(self, values: dict[str, Any]) -> None:
        apply_theme(QApplication.instance(), values.get("theme", "midnight"))
        self._apply_settings()

    def _apply_settings(self, initial: bool = False) -> None:
        settings = self.store.settings
        self.view.set_grid(settings.show_grid)
        self.view.set_snap(settings.snap_to_grid)
        self.view.set_show_cursors(settings.show_cursors)
        self.view.set_style(
            stroke=settings.stroke_color,
            width=settings.stroke_width,
            fill=settings.fill_color,
            sticky=settings.sticky_color,
            font_size=settings.font_size,
        )
        self.tool_options.set_grid(settings.show_grid)
        self.tool_options.set_snap(settings.snap_to_grid)
        self.tool_options.set_width(settings.stroke_width)
        self._following_presenter = settings.presentation_follow

    def _show_templates(self) -> None:
        if self._is_read_only():
            self.toast.show("Viewers cannot insert templates", "warning")
            return
        dialog = FlowchartTemplatesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_template:
            self._insert_template(dialog.selected_template)

    def _insert_template(self, template) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        id_map: dict[str, str] = {}
        nodes = []
        for node in template.nodes:
            object_id = uuid.uuid4().hex
            id_map[node.key] = object_id
            kind = node.kind
            obj = {
                "id": object_id,
                "kind": kind,
                "x": round(center.x() + node.x, 2),
                "y": round(center.y() + node.y, 2),
                "w": node.w,
                "h": node.h,
                "points": [],
                "text": node.text,
                "z": self.model.next_z(),
                "style": {
                    "stroke": "#f4f4f5" if kind != "text" else "none",
                    "fill": node.fill if kind != "text" else "none",
                    "stroke_width": 2,
                    "opacity": 1.0,
                    "dashed": False,
                    "font_size": 16,
                    "font_family": "Inter",
                    "font_weight": "bold",
                    "text_color": "#f4f4f5",
                },
                "owner_id": self._self_participant.get("id", ""),
                "owner_name": self._self_participant.get("name", "You"),
                "rev": 1,
                "created_at": "",
                "updated_at": "",
            }
            nodes.append(obj)
            self.model.add_local_object(obj)
            self.manager.send("CREATE_OBJECT", {"object": self._wire_object(obj)})
        for from_key, to_key in template.edges:
            if from_key not in id_map or to_key not in id_map:
                continue
            obj = {
                "id": uuid.uuid4().hex,
                "kind": KIND_CONNECTOR,
                "x": 0.0,
                "y": 0.0,
                "w": 100.0,
                "h": 100.0,
                "points": [],
                "text": "",
                "z": 0,
                "style": {
                    "stroke": "#71717a",
                    "fill": "none",
                    "stroke_width": 2,
                    "opacity": 1.0,
                    "dashed": False,
                    "font_size": 12,
                    "text_color": "#a1a1aa",
                },
                "connector": {"from_id": id_map[from_key], "to_id": id_map[to_key], "arrow": True, "label": ""},
                "owner_id": self._self_participant.get("id", ""),
                "owner_name": self._self_participant.get("name", "You"),
                "rev": 1,
                "created_at": "",
                "updated_at": "",
            }
            self.model.add_local_object(obj)
            self.manager.send("CREATE_OBJECT", {"object": self._wire_object(obj)})
        self._mark_dirty()
        self.toast.show(f"Inserted {len(nodes)} template objects", "success")

    def _save_version(self) -> None:
        if self._role == ROLE_VIEWER:
            self.toast.show("Viewers cannot create versions", "warning")
            return
        suggested = f"Version {time.strftime('%d %b %H:%M')}"
        dialog = SaveVersionDialog(suggested, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, description = dialog.values()
        self.manager.send("SNAPSHOT_CREATE", {"name": name, "description": description})
        self._mark_saved()

    def _show_more_menu(self) -> None:
        menu = QMenu(self)
        actions = [
            ("Save version", "save", self._save_version),
            ("Export / import…", "export", self._show_export),
            ("Flowchart templates…", "template", self._show_templates),
            ("Board overview", "minimap", self._toggle_minimap),
            ("Keyboard shortcuts", "command", self._show_help),
            ("Copy room code", "duplicate", self._copy_code),
        ]
        for label, icon_name, callback in actions:
            action = menu.addAction(icon(icon_name, "#d4d4d8", 16), label)
            action.triggered.connect(callback)
        if self._role == ROLE_HOST:
            menu.addSeparator()
            clear = menu.addAction(icon("eraser", "#ef4444", 16), "Clear canvas…")
            clear.triggered.connect(self._clear_canvas)
            lock = menu.addAction(icon("lock", "#d4d4d8", 16), "Lock board" if not self.model.locked else "Unlock board")
            lock.triggered.connect(self._toggle_lock)
        menu.addSeparator()
        about = menu.addAction(icon("server", "#d4d4d8", 16), f"About {APP_NAME}")
        about.triggered.connect(self._show_about)
        menu.exec(self._btn_more.mapToGlobal(self._btn_more.rect().bottomLeft()))

    def _clear_canvas(self) -> None:
        if self._role != ROLE_HOST:
            return
        answer = QMessageBox.question(
            self,
            "Clear the canvas?",
            "This removes every object from the shared board for all participants.\n\n"
            "A safety version is saved first. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.send("CLEAR_CANVAS", {})
            self._mark_dirty()

    def _command_palette(self) -> None:
        commands = [
            PaletteCommand("Select tool", "Tools", "V", lambda: self._select_tool(TOOL_SELECT)),
            PaletteCommand("Pencil", "Tools", "P", lambda: self._select_tool(TOOL_PENCIL)),
            PaletteCommand("Brush", "Tools", "B", lambda: self._select_tool(TOOL_BRUSH)),
            PaletteCommand("Eraser", "Tools", "E", lambda: self._select_tool(TOOL_ERASER)),
            PaletteCommand("Text", "Tools", "T", lambda: self._select_tool(TOOL_TEXT)),
            PaletteCommand("Rectangle", "Tools", "R", lambda: self._select_tool(TOOL_RECT)),
            PaletteCommand("Ellipse", "Tools", "O", lambda: self._select_tool(TOOL_ELLIPSE)),
            PaletteCommand("Sticky note", "Tools", "S", lambda: self._select_tool(TOOL_STICKY)),
            PaletteCommand("Mind-map node", "Tools", "M", lambda: self._select_tool(TOOL_MINDNODE)),
            PaletteCommand("Laser pointer", "Tools", "L", lambda: self._select_tool(TOOL_LASER)),
            PaletteCommand("Fit board to screen", "View", "", self.view.fit_content),
            PaletteCommand("Reset zoom", "View", "Ctrl+0", self.view.reset_zoom),
            PaletteCommand("Toggle grid", "View", "Ctrl+G", lambda: self.tool_options.grid_button.toggle()),
            PaletteCommand("Toggle overview map", "View", "", self._toggle_minimap),
            PaletteCommand("Save version", "Collaboration", "Ctrl+S", self._save_version),
            PaletteCommand("Version history", "Collaboration", "", self._show_versions),
            PaletteCommand("Network monitoring", "Collaboration", "", self._show_network),
            PaletteCommand("Participants", "Collaboration", "", self._show_participants),
            PaletteCommand("Export / import", "Files", "Ctrl+E", self._show_export),
            PaletteCommand("Flowchart templates", "Insert", "", self._show_templates),
            PaletteCommand("Settings", "Application", "", self._show_settings),
            PaletteCommand("Keyboard shortcuts", "Help", "F1", self._show_help),
        ]
        if self._role == ROLE_HOST:
            commands.append(PaletteCommand("Start presentation", "Present", "", self._start_presentation))
            commands.append(PaletteCommand("Lock / unlock board", "Host", "", self._toggle_lock))
            commands.append(PaletteCommand("Clear canvas", "Host", "", self._clear_canvas))
        palette = CommandPalette(commands, self)
        palette.exec()

    def _show_help(self) -> None:
        ShortcutsDialog(self).exec()

    def _show_about(self) -> None:
        from client.ui.dialog_text import AboutDialog

        AboutDialog(self).exec()

    def _show_server_help(self) -> None:
        QMessageBox.information(
            self,
            "Run a SynCanvas server",
            "On the host computer (same Wi-Fi/LAN):\n\n"
            "1. Open a terminal in the project folder.\n"
            "2. Run:  python run_server.py\n"
            "3. Note the LAN IP printed by the server, for example 192.168.1.10.\n"
            "4. Share the IP, port (default 45871) and room code.\n\n"
            "Windows builds: run SynCanvasServer.exe.\n"
            "If other devices cannot connect, allow TCP port 45871 in the firewall.\n\n"
            "The server never needs an internet connection.",
        )

    def _copy_invite(self) -> None:
        text = (
            f"Join my SynCanvas board\nServer: {self.manager.host}:{self.manager.port}\n"
            f"Room code: {self.manager.session_code}\n"
        )
        QGuiApplication.clipboard().setText(text)
        self.toast.show("Connection details copied", "success", 2000)

    # ==================================================================
    # Presentation helpers / state
    # ==================================================================
    def _is_read_only(self) -> bool:
        if self._presentation_active or self._presentation_state.get("active"):
            return True
        if self._role == ROLE_VIEWER:
            return True
        if self.model.locked and self._role != ROLE_HOST:
            return True
        return False

    def _update_interaction_state(self) -> None:
        read_only = self._is_read_only()
        self.view.set_read_only(read_only)
        self._btn_lock.setVisible(self._role == ROLE_HOST)
        self._btn_lock.blockSignals(True)
        self._btn_lock.setChecked(self.model.locked)
        self._btn_lock.setIcon(icon("lock" if self.model.locked else "unlock", "#94a3b8", 18))
        self._btn_lock.blockSignals(False)
        self._btn_present.setVisible(self._role == ROLE_HOST)
        self._btn_join_presentation.setVisible(
            self._role != ROLE_HOST
            and bool(self._presentation_state.get("active"))
            and not self._presentation_active
        )
        self.role_badge.setText({"host": "Host", "editor": "Editor", "viewer": "Viewer"}.get(self._role, self._role.title()))
        self.sidebar.properties.set_self_role(self._role)
        self.tool_options.setEnabled(not read_only)
        for tool, button in self._tool_buttons.items():
            if tool in (TOOL_SELECT, TOOL_PAN):
                button.setEnabled(True)
            else:
                button.setEnabled(not read_only)
        self.shape_button.setEnabled(not read_only)
        self.line_button.setEnabled(not read_only)
        self.flow_button.setEnabled(not read_only)
        if read_only and self._current_tool not in (TOOL_SELECT, TOOL_PAN):
            self._select_tool(TOOL_SELECT)
        self._update_undo_redo()
        if self.model.locked and not read_only:
            self.tool_options.setEnabled(True)
        self.save_indicator.setToolTip(
            "The server autosaves the board after changes." if not read_only else "Read-only access"
        )

    def _update_participant_colors(self) -> None:
        self._participant_colors = getattr(self, "_participant_colors", {})

    def _color_for(self, participant_id: str | None) -> str:
        if not participant_id:
            return "#a1a1aa"
        return getattr(self, "_participant_colors", {}).get(participant_id, "#a1a1aa")

    def _top_people_count(self, participants: list[dict[str, Any]]) -> None:
        self.sidebar.participants.count_badge.setText(str(len(participants)))

    def _top_people_count_from_count(self, count: int) -> None:
        self.sidebar.participants.count_badge.setText(str(count))

    def _update_object_count(self) -> None:
        count = self.model.object_count()
        self.bottom_objects.setText(f"{count} object{'s' if count != 1 else ''}")

    def _mark_dirty(self) -> None:
        if self.stack.currentIndex() != 1:
            return
        if self.manager.state == "connected":
            self.save_indicator.setText("Saving…")
        else:
            self.save_indicator.setText("Queued offline")
        self._save_timer.start()

    def _mark_saved(self) -> None:
        if self.manager.state == "connected":
            self.save_indicator.setText("Saved")
            self.save_indicator.setProperty("role", "faint")
        else:
            self.save_indicator.setText("Offline")

    def _saved_now(self) -> None:
        if self.manager.state == "connected":
            self.save_indicator.setText("Autosaved")

    # ==================================================================
    # Helpers / geometry / lifecycle
    # ==================================================================
    def _toggle_minimap(self) -> None:
        if self.minimap_frame.isVisible():
            self.minimap_frame.hide()
        else:
            self.minimap_frame.show()
            self.minimap_frame.raise_()
            self._position_overlays()

    def _position_overlays(self) -> None:
        if not hasattr(self, "zoom_bar"):
            return
        width = self.view.width()
        height = self.view.height()
        self.zoom_bar.adjustSize()
        self.zoom_bar.move(max(8, width - self.zoom_bar.width() - 16), max(8, height - self.zoom_bar.height() - 14))
        self.zoom_bar.raise_()
        self.minimap_frame.move(
            max(8, width - self.minimap_frame.width() - 16),
            max(8, self.zoom_bar.y() - self.minimap_frame.height() - 8),
        )
        self.presentation_bar.adjustSize()
        self.presentation_bar.move(
            max(8, (width - self.presentation_bar.width()) // 2),
            16,
        )
        self.presentation_bar.raise_()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.view and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._position_overlays)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_overlays()
        try:
            self.toast._position()
        except Exception:
            pass

    def _return_to_lobby(self) -> None:
        if self._presentation_active:
            self._presentation_active = False
            self._presentation_is_host = False
            self.presentation_bar.hide()
            self.showNormal()
        self._session_active = False
        self.manager._auto_reconnect = False
        self.model.clear()
        self.view.set_hidden()
        self.sidebar.chat.set_history([])
        self.sidebar.activity.set_history([])
        self.sidebar.participants.set_participants([])
        self.sidebar.properties.set_selection([])
        self.stack.setCurrentIndex(0)
        self.welcome.reset_connection_state()
        self.welcome.reload_settings()
        self.setWindowTitle(f"{APP_NAME} · LAN collaborative whiteboard")
        self._presentation_state = {}
        self._btn_join_presentation.hide()
        self._session_active = False

    def _leave_board(self) -> None:
        answer = QMessageBox.question(
            self,
            "Leave this board?",
            "Leave the shared board? Your reconnect token is kept in case you return.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.manager.leave_session()
        self._return_to_lobby()

    def closeEvent(self, event) -> None:
        if self._presentation_active:
            self._exit_presentation_local()
        self.store.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.store.save()
        self.manager.shutdown()
        super().closeEvent(event)
