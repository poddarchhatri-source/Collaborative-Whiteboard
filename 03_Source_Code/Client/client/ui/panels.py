"""Right-hand collaboration sidebar: participants, chat, activity, properties."""

from __future__ import annotations

import html
import time
from datetime import datetime
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import icon, pixmap
from client.ui.widgets import Avatar, ColorButton, EmptyState, SectionHeader
from shared.constants import APP_NAME, PALETTE, STICKY_COLORS

TOOL_LABELS = {
    "select": "selecting",
    "hand": "panning",
    "pencil": "drawing",
    "brush": "brushing",
    "eraser": "erasing",
    "text": "typing",
    "rect": "drawing",
    "ellipse": "drawing",
    "arrow": "drawing",
    "connector": "connecting",
    "sticky": "writing a note",
    "mindnode": "mapping",
    "image": "placing an image",
    "laser": "pointing",
    "pan": "panning",
}

KIND_LABELS = {
    "stroke": "Freehand stroke",
    "line": "Line",
    "rect": "Rectangle",
    "rounded_rect": "Rounded rectangle",
    "ellipse": "Ellipse",
    "circle": "Circle",
    "diamond": "Diamond",
    "triangle": "Triangle",
    "arrow": "Arrow",
    "connector": "Connector",
    "text": "Text",
    "sticky": "Sticky note",
    "image": "Image",
    "mindnode": "Mind-map node",
    "flow_terminator": "Flow terminal",
    "flow_process": "Flow process",
    "flow_decision": "Flow decision",
    "flow_io": "Flow input/output",
    "flow_document": "Flow document",
    "flow_database": "Flow database",
}


class ParticipantsPanel(QWidget):
    manageRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._participants: list[dict[str, Any]] = []
        self._self_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.count_label = QLabel("PARTICIPANTS")
        self.count_label.setProperty("role", "section")
        header.addWidget(self.count_label)
        header.addStretch(1)
        self.count_badge = QLabel("0")
        self.count_badge.setProperty("role", "badge")
        header.addWidget(self.count_badge)
        self.manage_button = QToolButton()
        self.manage_button.setIcon(icon("sliders", "#a1a1aa", 17))
        self.manage_button.setToolTip("Manage participants")
        self.manage_button.clicked.connect(self.manageRequested)
        header.addWidget(self.manage_button)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setSpacing(1)
        layout.addWidget(self.list, 1)
        self.empty = EmptyState("users", "Nobody else here yet", "Share the room code to invite classmates.")
        layout.addWidget(self.empty)

    def set_self(self, participant: dict[str, Any]) -> None:
        self._self_id = participant.get("id", "")

    def set_participants(self, participants: list[dict[str, Any]]) -> None:
        self._participants = [p for p in participants if p.get("online", True)]
        self.count_badge.setText(str(len(self._participants)))
        self.list.clear()
        for participant in self._participants:
            item = QListWidgetItem()
            item.setSizeHint(QSize(250, 52))
            self.list.addItem(item)
            self.list.setItemWidget(item, self._row(participant))
        self.empty.setVisible(not self._participants)
        self.list.setVisible(bool(self._participants))

    def _row(self, participant: dict[str, Any]) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(9)
        avatar = Avatar(participant.get("name", "?"), participant.get("color", "#27272a"), 32)
        layout.addWidget(avatar)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name = QLabel(participant.get("name", "Guest"))
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        name_row.addWidget(name)
        if participant.get("id") == self._self_id:
            you = QLabel("you")
            you.setProperty("role", "faint")
            name_row.addWidget(you)
        name_row.addStretch(1)
        text_col.addLayout(name_row)
        role = participant.get("role", "editor")
        role_text = {"host": "Host", "editor": "Editor", "viewer": "Viewer"}.get(role, role.title())
        tool = TOOL_LABELS.get(participant.get("tool", "select"), "online")
        subtitle = QLabel(f"{role_text} · {tool}")
        subtitle.setProperty("role", "faint")
        subtitle.setStyleSheet("font-size: 10px;")
        text_col.addWidget(subtitle)
        layout.addLayout(text_col, 1)
        if role == "host":
            crown = QLabel()
            crown.setPixmap(pixmap("shield", "#fbbf24", 16))
            crown.setToolTip("Host controls the session")
            layout.addWidget(crown)
        return container


class ChatPanel(QWidget):
    messageSubmitted = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._messages: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self._self_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        label = QLabel("GROUP CHAT")
        label.setProperty("role", "section")
        header.addWidget(label)
        header.addStretch(1)
        self.count = QLabel("0")
        self.count.setProperty("role", "faint")
        header.addWidget(self.count)
        layout.addLayout(header)
        self.output = QTextBrowser()
        self.output.setOpenExternalLinks(False)
        self.output.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        layout.addWidget(self.output, 1)
        composer = QFrame()
        composer.setProperty("role", "softCard")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(8, 6, 6, 6)
        composer_layout.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message everyone…")
        self.input.setMaxLength(1000)
        self.input.returnPressed.connect(self._submit)
        self.input.setStyleSheet("QLineEdit { border: none; background: transparent; padding: 4px; }")
        composer_layout.addWidget(self.input, 1)
        self.send_button = QToolButton()
        self.send_button.setIcon(icon("send", "#f4f4f5", 18))
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._submit)
        composer_layout.addWidget(self.send_button)
        layout.addWidget(composer)

    def set_self(self, participant: dict[str, Any]) -> None:
        self._self_id = participant.get("id", "")

    def set_history(self, messages: list[dict[str, Any]]) -> None:
        self._messages = []
        self._seen.clear()
        self.output.clear()
        for message in messages:
            self.add_message(message)

    def add_message(self, message: dict[str, Any]) -> None:
        message_id = message.get("id", "")
        if message_id and message_id in self._seen:
            return
        if message_id:
            self._seen.add(message_id)
        self._messages.append(message)
        if len(self._messages) > 400:
            self._messages = self._messages[-300:]
            self.set_history(self._messages)
            return
        self._append_html(message)
        self.count.setText(str(len(self._messages)))

    def _append_html(self, message: dict[str, Any]) -> None:
        sender = html.escape(str(message.get("sender", "Guest")))
        text = html.escape(str(message.get("text", ""))).replace("\n", "<br>")
        color = html.escape(str(message.get("color", "#a1a1aa")))
        timestamp = str(message.get("timestamp", ""))
        try:
            display_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except (ValueError, TypeError):
            display_time = timestamp[11:16] if len(timestamp) > 16 else ""
        mine = message.get("sender_id") == self._self_id
        initial = html.escape((message.get("sender", "?") or "?")[:1].upper())
        background = "rgba(255,255,255,0.06)" if mine else "rgba(255,255,255,0.03)"
        border = "#f4f4f5" if mine else "rgba(255,255,255,0.12)"
        block = f"""
        <table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0;">
          <tr>
            <td width="26" valign="top">
              <div style="width:22px;height:22px;border-radius:11px;background:{color};color:white;
                          font-size:11px;font-weight:700;text-align:center;line-height:22px;">{initial}</div>
            </td>
            <td style="padding-left:2px;">
              <span style="color:{color};font-size:11px;font-weight:700;">{sender}</span>
              <span style="color:#71717a;font-size:10px;">&nbsp;&nbsp;{display_time}</span>
              <div style="background:{background};border-left:2px solid {border};border-radius:5px;
                          padding:6px 8px;margin-top:3px;color:#f4f4f5;font-size:12px;">{text}</div>
            </td>
          </tr>
        </table>
        """
        self.output.moveCursor(self.output.textCursor().MoveOperation.End)
        self.output.insertHtml(block)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def add_system(self, text: str) -> None:
        block = (
            f"<div style='color:#71717a;font-size:10px;text-align:center;margin:7px 0;'>"
            f"{html.escape(text)}</div>"
        )
        self.output.moveCursor(self.output.textCursor().MoveOperation.End)
        self.output.insertHtml(block)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.messageSubmitted.emit(text)


class ActivityPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        label = QLabel("ACTIVITY LOG")
        label.setProperty("role", "section")
        header.addWidget(label)
        header.addStretch(1)
        self.count = QLabel("0")
        self.count.setProperty("role", "faint")
        header.addWidget(self.count)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        layout.addWidget(self.list, 1)
        self.empty = EmptyState("activity", "No activity yet", "Canvas edits, joins and version restores appear here.")
        layout.addWidget(self.empty)
        self.empty.setVisible(False)

    def set_history(self, entries: list[dict[str, Any]]) -> None:
        self._entries = []
        self.list.clear()
        for entry in entries:
            self.add_activity(entry, scroll=False)
        self._update_empty()

    def add_activity(self, entry: dict[str, Any], *, scroll: bool = True) -> None:
        entry_id = entry.get("id", "")
        if entry_id and any(existing.get("id") == entry_id for existing in self._entries):
            return
        self._entries.append(entry)
        if len(self._entries) > 400:
            self._entries = self._entries[-300:]
            self.set_history(self._entries)
            return
        kind = entry.get("kind", "activity")
        icon_name = {
            "object_created": "plus",
            "object_updated": "pencil",
            "objects_deleted": "trash",
            "canvas_cleared": "eraser",
            "canvas_replaced": "import",
            "undo": "undo",
            "redo": "redo",
            "version_restored": "history",
            "user_joined": "users",
            "user_left": "logout",
            "board_locked": "lock",
            "presentation": "present",
            "snapshot": "save",
            "role": "shield",
            "kick": "logout",
        }.get(kind, "activity")
        timestamp = str(entry.get("timestamp", ""))
        try:
            display_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except (ValueError, TypeError):
            display_time = ""
        actor = entry.get("actor", "System")
        detail = entry.get("detail", "")
        item = QListWidgetItem()
        item.setIcon(icon(icon_name, "#a1a1aa", 16))
        item.setText(f"{display_time}  {detail}")
        item.setToolTip(f"{actor} · {timestamp}")
        self.list.addItem(item)
        self.count.setText(str(len(self._entries)))
        self._update_empty()
        if scroll:
            self.list.scrollToBottom()

    def _update_empty(self) -> None:
        self.empty.setVisible(not self._entries)
        self.list.setVisible(bool(self._entries))


class PropertiesPanel(QWidget):
    styleChangeRequested = Signal(dict)
    actionRequested = Signal(str)
    defaultStyleChanged = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selection: list[dict[str, Any]] = []
        self._read_only = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        label = QLabel("PROPERTIES")
        label.setProperty("role", "section")
        header.addWidget(label)
        header.addStretch(1)
        self.selection_label = QLabel("No selection")
        self.selection_label.setProperty("role", "faint")
        header.addWidget(self.selection_label)
        layout.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 4, 0)
        body.setSpacing(10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        self.type_card = QFrame()
        self.type_card.setProperty("role", "softCard")
        type_layout = QVBoxLayout(self.type_card)
        type_layout.setContentsMargins(12, 10, 12, 10)
        type_layout.setSpacing(3)
        self.type_label = QLabel("Nothing selected")
        self.type_label.setStyleSheet("font-weight: 700;")
        type_layout.addWidget(self.type_label)
        self.type_hint = QLabel("Choose a tool or select an object on the canvas.")
        self.type_hint.setProperty("role", "faint")
        self.type_hint.setWordWrap(True)
        type_layout.addWidget(self.type_hint)
        body.addWidget(self.type_card)

        # Style ---------------------------------------------------------
        style_card = QFrame()
        style_card.setProperty("role", "softCard")
        style_layout = QVBoxLayout(style_card)
        style_layout.setContentsMargins(12, 10, 12, 12)
        style_layout.setSpacing(8)
        stroke_header = QHBoxLayout()
        stroke_header.addWidget(QLabel("Stroke"))
        self.stroke_value = QLabel("#f4f4f5")
        self.stroke_value.setProperty("role", "code")
        stroke_header.addStretch(1)
        stroke_header.addWidget(self.stroke_value)
        self.stroke_pick = QPushButton("Pick")
        self.stroke_pick.setObjectName("linkButton")
        self.stroke_pick.clicked.connect(lambda: self._pick_color("stroke"))
        stroke_header.addWidget(self.stroke_pick)
        style_layout.addLayout(stroke_header)
        self.palette_row = QHBoxLayout()
        self.palette_row.setSpacing(4)
        for color in PALETTE[:10]:
            button = ColorButton(color, 18)
            button.clicked.connect(lambda checked=False, c=color: self._set_color("stroke", c))
            self.palette_row.addWidget(button)
        style_layout.addLayout(self.palette_row)

        fill_header = QHBoxLayout()
        fill_header.addWidget(QLabel("Fill"))
        self.fill_value = QLabel("#27272a")
        self.fill_value.setProperty("role", "code")
        fill_header.addStretch(1)
        fill_header.addWidget(self.fill_value)
        self.fill_pick = QPushButton("Pick")
        self.fill_pick.setObjectName("linkButton")
        self.fill_pick.clicked.connect(lambda: self._pick_color("fill"))
        fill_header.addWidget(self.fill_pick)
        none_button = QPushButton("None")
        none_button.setObjectName("linkButton")
        none_button.clicked.connect(lambda: self._set_color("fill", "none"))
        fill_header.addWidget(none_button)
        style_layout.addLayout(fill_header)
        self.fill_row = QHBoxLayout()
        self.fill_row.setSpacing(4)
        for color in PALETTE[1:9]:
            button = ColorButton(color, 18)
            button.clicked.connect(lambda checked=False, c=color: self._set_color("fill", c))
            self.fill_row.addWidget(button)
        style_layout.addLayout(self.fill_row)

        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("Width"))
        self.width = QSlider(Qt.Orientation.Horizontal)
        self.width.setRange(1, 40)
        self.width.setValue(3)
        self.width_value = QLabel("3 px")
        self.width_value.setFixedWidth(42)
        self.width.sliderMoved.connect(lambda value: self.width_value.setText(f"{value} px"))
        self.width.sliderReleased.connect(lambda: self._emit_style({"stroke_width": self.width.value()}))
        width_row.addWidget(self.width, 1)
        width_row.addWidget(self.width_value)
        style_layout.addLayout(width_row)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(10, 100)
        self.opacity.setValue(100)
        self.opacity_value = QLabel("100%")
        self.opacity_value.setFixedWidth(42)
        self.opacity.sliderMoved.connect(lambda value: self.opacity_value.setText(f"{value}%"))
        self.opacity.sliderReleased.connect(
            lambda: self._emit_style({"opacity": self.opacity.value() / 100.0})
        )
        opacity_row.addWidget(self.opacity, 1)
        opacity_row.addWidget(self.opacity_value)
        style_layout.addLayout(opacity_row)
        body.addWidget(style_card)

        # Text ----------------------------------------------------------
        self.text_card = QFrame()
        self.text_card.setProperty("role", "softCard")
        text_layout = QVBoxLayout(self.text_card)
        text_layout.setContentsMargins(12, 10, 12, 12)
        text_layout.setSpacing(6)
        text_header = QHBoxLayout()
        text_header.addWidget(QLabel("Text"))
        text_header.addStretch(1)
        edit = QPushButton("Edit…")
        edit.setObjectName("linkButton")
        edit.clicked.connect(lambda: self.actionRequested.emit("edit_text"))
        text_header.addWidget(edit)
        text_layout.addLayout(text_header)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Size"))
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 96)
        self.font_size.setValue(18)
        self.font_size.setSuffix(" px")
        self.font_size.editingFinished.connect(
            lambda: self._emit_style({"font_size": self.font_size.value()})
        )
        font_row.addWidget(self.font_size)
        font_row.addStretch(1)
        text_layout.addLayout(font_row)
        body.addWidget(self.text_card)

        # Arrange -------------------------------------------------------
        arrange_card = QFrame()
        arrange_card.setProperty("role", "softCard")
        arrange_layout = QVBoxLayout(arrange_card)
        arrange_layout.setContentsMargins(12, 10, 12, 12)
        arrange_layout.setSpacing(7)
        arrange_layout.addWidget(QLabel("Arrange"))
        align_row = QHBoxLayout()
        for label, action in (("Align left", "align_left"), ("Center", "align_center"), ("Align top", "align_top")):
            button = QPushButton(label)
            button.setObjectName("ghostButton")
            button.clicked.connect(lambda _=False, a=action: self.actionRequested.emit(a))
            align_row.addWidget(button)
        arrange_layout.addLayout(align_row)
        row1 = QHBoxLayout()
        for label, action, icon_name in (
            ("Forward", "bring_forward", "forward"),
            ("Backward", "send_backward", "backward"),
        ):
            button = QPushButton(label)
            button.setIcon(icon(icon_name, "#d4d4d8", 15))
            button.clicked.connect(lambda _=False, a=action: self.actionRequested.emit(a))
            row1.addWidget(button)
        arrange_layout.addLayout(row1)
        row2 = QHBoxLayout()
        for label, action, icon_name in (
            ("Duplicate", "duplicate", "duplicate"),
            ("Group", "group", "group"),
        ):
            button = QPushButton(label)
            button.setIcon(icon(icon_name, "#d4d4d8", 15))
            button.clicked.connect(lambda _=False, a=action: self.actionRequested.emit(a))
            row2.addWidget(button)
        arrange_layout.addLayout(row2)
        self.delete_button = QPushButton("Delete selection")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setIcon(icon("trash", "#ef4444", 15))
        self.delete_button.clicked.connect(lambda: self.actionRequested.emit("delete"))
        arrange_layout.addWidget(self.delete_button)
        body.addWidget(arrange_card)
        body.addStretch(1)
        self._set_enabled(False)

    def set_self_role(self, role: str) -> None:
        self._read_only = role == "viewer"
        self._set_enabled(bool(self._selection) and not self._read_only)

    def set_selection(self, objects: list[dict[str, Any]]) -> None:
        self._selection = objects
        self._set_enabled(bool(objects) and not self._read_only)
        if not objects:
            self.selection_label.setText("No selection")
            self.type_label.setText("Nothing selected")
            self.type_hint.setText("Choose a tool or select an object on the canvas.")
            self.stroke_value.setText("—")
            self.fill_value.setText("—")
            return
        first = objects[0]
        if len(objects) == 1:
            self.selection_label.setText("1 object")
            self.type_label.setText(KIND_LABELS.get(first.get("kind", ""), "Canvas object"))
            owner = first.get("owner_name", "unknown")
            self.type_hint.setText(f"Owned by {owner} · revision {first.get('rev', 1)}")
        else:
            self.selection_label.setText(f"{len(objects)} objects")
            self.type_label.setText(f"{len(objects)} objects selected")
            self.type_hint.setText("Style changes apply to every selected object.")
        style = first.get("style", {})
        self.stroke_value.setText(str(style.get("stroke", "—")).upper())
        self.fill_value.setText(str(style.get("fill", "—")).upper())
        self.width.blockSignals(True)
        self.width.setValue(int(float(style.get("stroke_width", 3))))
        self.width_value.setText(f"{self.width.value()} px")
        self.width.blockSignals(False)
        opacity = int(float(style.get("opacity", 1.0)) * 100)
        self.opacity.blockSignals(True)
        self.opacity.setValue(opacity)
        self.opacity_value.setText(f"{opacity}%")
        self.opacity.blockSignals(False)
        self.font_size.blockSignals(True)
        self.font_size.setValue(int(float(style.get("font_size", 18))))
        self.font_size.blockSignals(False)
        text_visible = first.get("kind") in ("text", "sticky", "mindnode") or any(
            "flow" in str(obj.get("kind")) or obj.get("text") for obj in objects
        )
        self.text_card.setVisible(text_visible)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.stroke_pick, self.fill_pick, self.width, self.opacity, self.font_size, self.delete_button):
            widget.setEnabled(enabled)
        if enabled:
            self.type_card.setVisible(True)

    def _pick_color(self, field: str) -> None:
        current = self.stroke_value.text() if field == "stroke" else self.fill_value.text()
        color = QColorDialog.getColor(QColor(current), self, "Choose colour")
        if color.isValid():
            self._set_color(field, color.name())

    def _set_color(self, field: str, color: str) -> None:
        if field == "stroke":
            self.stroke_value.setText(color.upper())
        else:
            self.fill_value.setText(color.upper())
        if self._selection:
            self._emit_style({field: color})

    def _emit_style(self, patch: dict[str, Any]) -> None:
        if self._read_only:
            return
        if self._selection:
            self.styleChangeRequested.emit(patch)
        else:
            self.defaultStyleChanged.emit(patch)


class BoardSidebar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RightSidebar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.participants = ParticipantsPanel()
        self.chat = ChatPanel()
        self.activity = ActivityPanel()
        self.properties = PropertiesPanel()
        self.tabs.addTab(self.participants, icon("users", "#a1a1aa", 17), "People")
        self.tabs.addTab(self.chat, icon("chat", "#a1a1aa", 17), "Chat")
        self.tabs.addTab(self.activity, icon("activity", "#a1a1aa", 17), "Activity")
        self.tabs.addTab(self.properties, icon("sliders", "#a1a1aa", 17), "Style")
        layout.addWidget(self.tabs)
