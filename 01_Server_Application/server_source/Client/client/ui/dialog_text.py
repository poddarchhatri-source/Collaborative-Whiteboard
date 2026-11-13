"""Text editing, flowchart templates, shortcuts and the command palette."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import icon, pixmap
from client.ui.widgets import SectionHeader
from shared.constants import APP_NAME, APP_TAGLINE, APP_VERSION, SHORTCUTS


class TextEditorDialog(QDialog):
    def __init__(
        self,
        title: str = "Edit text",
        text: str = "",
        *,
        placeholder: str = "Type something…",
        multiline: bool = True,
        confirm_text: str = "Save",
        parent=None,
        max_length: int = 8000,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(560 if multiline else 460, 360 if multiline else 180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("text", "#f4f4f5", 22))
        header.addWidget(icon_label)
        heading = QLabel(title)
        heading.setProperty("role", "h2")
        header.addWidget(heading)
        header.addStretch(1)
        layout.addLayout(header)

        self.editor = QTextEdit()
        self.editor.setPlainText(text)
        self.editor.setPlaceholderText(placeholder)
        self.editor.setAcceptRichText(False)
        self.editor.setMinimumHeight(170 if multiline else 70)
        self.editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.editor, 1)

        hint = QLabel("Ctrl+Enter to save · Esc to cancel")
        hint.setProperty("role", "faint")
        layout.addWidget(hint)
        self._max_length = max_length
        self._counter = QLabel(f"{len(text)} / {max_length}")
        self._counter.setProperty("role", "faint")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._counter)

        buttons = QDialogButtonBox()
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        save = buttons.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("primaryButton")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.editor.setFocus()
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.End)
        QWidget.keyPressEvent  # keep type checker calm
        self._save_button = save

    def _on_text_changed(self) -> None:
        text = self.editor.toPlainText()
        if len(text) > self._max_length:
            cursor = self.editor.textCursor()
            position = cursor.position()
            self.editor.setPlainText(text[: self._max_length])
            cursor = self.editor.textCursor()
            cursor.setPosition(min(position, self._max_length))
            self.editor.setTextCursor(cursor)
            text = self.editor.toPlainText()
        self._counter.setText(f"{len(text)} / {self._max_length}")

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.accept()
            return
        super().keyPressEvent(event)

    def text(self) -> str:
        return self.editor.toPlainText().strip()


@dataclass
class TemplateNode:
    key: str
    kind: str
    text: str
    x: float
    y: float
    w: float = 180
    h: float = 74
    fill: str = "#27272a"


@dataclass
class FlowTemplate:
    name: str
    description: str
    nodes: list[TemplateNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


TEMPLATES: list[FlowTemplate] = [
    FlowTemplate(
        "Simple process",
        "Start → one process step → end. A clean starting point.",
        [
            TemplateNode("start", "flow_terminator", "Start", -160, -220, 160, 64, "#18181b"),
            TemplateNode("process", "flow_process", "Do the work", -160, -100, 180, 82, "#27272a"),
            TemplateNode("end", "flow_terminator", "End", -160, 40, 160, 64, "#18181b"),
        ],
        [("start", "process"), ("process", "end")],
    ),
    FlowTemplate(
        "Approval flow",
        "Submit → review → decision with approved and rejected branches.",
        [
            TemplateNode("start", "flow_terminator", "Request submitted", -240, -250, 210, 64, "#18181b"),
            TemplateNode("review", "flow_process", "Review request", -230, -120, 190, 82, "#27272a"),
            TemplateNode("decision", "flow_decision", "Approved?", -250, 20, 230, 130, "#3f3f46"),
            TemplateNode("approved", "flow_process", "Notify approval", 40, 30, 190, 82, "#27272a"),
            TemplateNode("rejected", "flow_process", "Request changes", -500, 30, 190, 82, "#3f3f46"),
            TemplateNode("end", "flow_terminator", "Done", 50, 180, 160, 64, "#18181b"),
        ],
        [("start", "review"), ("review", "decision"), ("decision", "approved"), ("decision", "rejected"), ("approved", "end"), ("rejected", "end")],
    ),
    FlowTemplate(
        "Login flow",
        "Credential check with success and error branches.",
        [
            TemplateNode("start", "flow_terminator", "Open app", -180, -250, 170, 64, "#18181b"),
            TemplateNode("input", "flow_io", "Enter credentials", -200, -120, 210, 82, "#27272a"),
            TemplateNode("decision", "flow_decision", "Valid login?", -210, 20, 220, 130, "#3f3f46"),
            TemplateNode("dashboard", "flow_process", "Open dashboard", 80, 40, 190, 82, "#27272a"),
            TemplateNode("error", "flow_process", "Show error", -480, 40, 180, 82, "#3f3f46"),
            TemplateNode("end", "flow_terminator", "Welcome", 95, 190, 160, 64, "#18181b"),
        ],
        [("start", "input"), ("input", "decision"), ("decision", "dashboard"), ("decision", "error"), ("dashboard", "end"), ("error", "input")],
    ),
    FlowTemplate(
        "Problem solving",
        "Analyse → choose a fix → act → verify → end.",
        [
            TemplateNode("start", "flow_terminator", "Problem found", -180, -280, 190, 64, "#3f3f46"),
            TemplateNode("analyze", "flow_process", "Analyse causes", -190, -160, 200, 82, "#27272a"),
            TemplateNode("decision", "flow_decision", "Fix ready?", -200, -30, 220, 130, "#3f3f46"),
            TemplateNode("action", "flow_process", "Apply fix", 80, -10, 190, 82, "#27272a"),
            TemplateNode("verify", "flow_decision", "Verified?", 80, 120, 210, 130, "#3f3f46"),
            TemplateNode("end", "flow_terminator", "Resolved", 100, 300, 160, 64, "#18181b"),
        ],
        [("start", "analyze"), ("analyze", "decision"), ("decision", "action"), ("action", "verify"), ("verify", "end"), ("verify", "analyze")],
    ),
    FlowTemplate(
        "Swimlane checklist",
        "A compact checklist layout for collaborative planning.",
        [
            TemplateNode("title", "text", "Release checklist", -220, -260, 420, 70),
            TemplateNode("a", "rounded_rect", "1. Design approved", -220, -150, 240, 64, "#27272a"),
            TemplateNode("b", "rounded_rect", "2. Code reviewed", -220, -50, 240, 64, "#27272a"),
            TemplateNode("c", "rounded_rect", "3. Tests passing", -220, 50, 240, 64, "#27272a"),
            TemplateNode("d", "rounded_rect", "4. Demo ready", -220, 150, 240, 64, "#18181b"),
        ],
        [],
    ),
]


class TemplatePreview(QWidget):
    def __init__(self, template: FlowTemplate, parent=None) -> None:
        super().__init__(parent)
        self.template = template
        self.setMinimumHeight(200)
        self.setMinimumWidth(280)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f0f11"))
        nodes = self.template.nodes
        if not nodes:
            return
        min_x = min(n.x for n in nodes)
        min_y = min(n.y for n in nodes)
        max_x = max(n.x + n.w for n in nodes)
        max_y = max(n.y + n.h for n in nodes)
        rect = self.rect().adjusted(18, 14, -18, -14)
        scale = min(rect.width() / max(1, max_x - min_x), rect.height() / max(1, max_y - min_y))
        offset = QPointF(rect.center().x() - (min_x + (max_x - min_x) / 2) * scale,
                         rect.center().y() - (min_y + (max_y - min_y) / 2) * scale)

        def mapped(n: TemplateNode) -> QRectF:
            return QRectF(n.x * scale + offset.x(), n.y * scale + offset.y(), max(4, n.w * scale), max(4, n.h * scale))

        # connectors first
        by_key: dict[str, TemplateNode] = {n.key: n for n in nodes}
        painter.setPen(QPen(QColor("#71717a"), 1.2))
        for from_key, to_key in self.template.edges:
            a, b = by_key.get(from_key), by_key.get(to_key)
            if not a or not b:
                continue
            ra, rb = mapped(a), mapped(b)
            start = ra.center()
            end = rb.center()
            delta = end - start
            if abs(delta.x()) + abs(delta.y()) > 0:
                length = max(1.0, (delta.x() ** 2 + delta.y() ** 2) ** 0.5)
                start = start + delta * (min(ra.width(), ra.height()) / 2 / length)
                end = end - delta * (min(rb.width(), rb.height()) / 2 / length)
            painter.drawLine(start, end)
        for node in nodes:
            r = mapped(node)
            color = QColor(node.fill)
            if node.kind == "text":
                painter.setPen(QPen(QColor("#f4f4f5")))
                painter.setFont(QFont("Inter", max(7, int(14 * scale))))
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, node.text)
                continue
            path = QPainterPath()
            if node.kind in ("flow_terminator",):
                path.addRoundedRect(r, r.height() / 2, r.height() / 2)
            elif node.kind in ("flow_decision",):
                path.moveTo(r.center().x(), r.top())
                path.lineTo(r.right(), r.center().y())
                path.lineTo(r.center().x(), r.bottom())
                path.lineTo(r.left(), r.center().y())
                path.closeSubpath()
            elif node.kind == "flow_io":
                off = r.width() * 0.15
                path.moveTo(r.left() + off, r.top())
                path.lineTo(r.right(), r.top())
                path.lineTo(r.right() - off, r.bottom())
                path.lineTo(r.left(), r.bottom())
                path.closeSubpath()
            else:
                path.addRoundedRect(r, 2, 2)
            painter.setPen(QPen(color.lighter(145), 1))
            painter.setBrush(color)
            painter.drawPath(path)
            painter.setPen(QPen(QColor("#f4f4f5")))
            painter.setFont(QFont("Inter", max(6, int(11 * scale))))
            painter.drawText(r.adjusted(3, 2, -3, -2), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, node.text)


class FlowchartTemplatesDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Flowchart templates")
        self.setModal(True)
        self.resize(820, 580)
        self.selected_template: FlowTemplate | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("flow", "#f4f4f5", 24))
        header.addWidget(icon_label)
        title = QLabel("Start from a template")
        title.setProperty("role", "h2")
        header.addWidget(title)
        header.addStretch(1)
        outer.addLayout(header)
        subtitle = QLabel("Templates insert editable shapes and connectors into your board at the current view.")
        subtitle.setProperty("role", "muted")
        outer.addWidget(subtitle)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.setMaximumWidth(300)
        for template in TEMPLATES:
            item = QListWidgetItem(template.name)
            item.setData(Qt.ItemDataRole.UserRole, template)
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._selected)
        body.addWidget(self.list)
        right = QVBoxLayout()
        right.setSpacing(10)
        self.preview = TemplatePreview(TEMPLATES[0])
        right.addWidget(self.preview, 1)
        self.description = QLabel(TEMPLATES[0].description)
        self.description.setProperty("role", "muted")
        self.description.setWordWrap(True)
        right.addWidget(self.description)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        buttons = QDialogButtonBox()
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        insert = buttons.addButton("Insert template", QDialogButtonBox.ButtonRole.AcceptRole)
        insert.setObjectName("primaryButton")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._accept)
        outer.addWidget(buttons)
        self.list.setCurrentRow(0)
        self._accept_button = insert

    def _selected(self, row: int) -> None:
        if 0 <= row < len(TEMPLATES):
            template = TEMPLATES[row]
            self.preview.template = template
            self.preview.update()
            self.description.setText(template.description)

    def _accept(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(TEMPLATES):
            self.selected_template = TEMPLATES[row]
            self.accept()


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setModal(True)
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel("Work faster")
        title.setProperty("role", "h2")
        layout.addWidget(title)
        subtitle = QLabel("SynCanvas is designed to be used with one hand on the keyboard and one on the canvas.")
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 190)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for shortcut, description in SHORTCUTS:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(shortcut))
            table.setItem(row, 1, QTableWidgetItem(description))
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setModal(True)
        self.resize(480, 330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(pixmap("network", "#f4f4f5", 46))
        layout.addWidget(logo)
        title = QLabel(APP_NAME)
        title.setProperty("role", "h1")
        layout.addWidget(title)
        tagline = QLabel(APP_TAGLINE)
        tagline.setProperty("role", "muted")
        layout.addWidget(tagline)
        version = QLabel(f"Version {APP_VERSION} · Protocol v1 · LAN-only by design")
        version.setProperty("role", "faint")
        layout.addWidget(version)
        description = QLabel(
            "A standalone collaborative whiteboard for classrooms and labs. "
            "A TCP server maintains the authoritative canvas, while Qt clients "
            "exchange validated JSON events over the local network."
        )
        description.setWordWrap(True)
        description.setProperty("role", "muted")
        layout.addWidget(description)
        note = QLabel(
            "No cloud services, online APIs, or internet authentication are used. "
            "LAN-only operation is not automatically secure; always use a session "
            "password on shared networks."
        )
        note.setWordWrap(True)
        note.setProperty("role", "faint")
        layout.addWidget(note)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


@dataclass
class PaletteCommand:
    title: str
    category: str
    shortcut: str
    callback: Callable[[], None]


class CommandPalette(QDialog):
    def __init__(self, commands: list[PaletteCommand], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(560, 420)
        self.commands = commands
        self._filtered = list(commands)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a command…")
        self.search.setProperty("role", "search")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.itemActivated.connect(lambda _: self._run())
        layout.addWidget(self.list, 1)
        hint = QLabel("↑ ↓ to navigate · Enter to run · Esc to close")
        hint.setProperty("role", "faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        self._populate()
        self.search.setFocus()

    def _populate(self) -> None:
        self.list.clear()
        for command in self._filtered:
            item = QListWidgetItem(f"{command.title}    {command.category}")
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(item)
        if self._filtered:
            self.list.setCurrentRow(0)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        self._filtered = [
            c for c in self.commands
            if not needle or needle in c.title.lower() or needle in c.category.lower()
        ][:60]
        self._populate()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Down:
            self.list.setCurrentRow(min(self.list.count() - 1, self.list.currentRow() + 1))
            return
        if event.key() == Qt.Key.Key_Up:
            self.list.setCurrentRow(max(0, self.list.currentRow() - 1))
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._run()
            return
        super().keyPressEvent(event)

    def _run(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        command: PaletteCommand = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        command.callback()
