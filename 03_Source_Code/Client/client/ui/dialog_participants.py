"""Host-facing participant management with server-enforced role controls."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import pixmap
from client.ui.widgets import Avatar

TOOL_NAMES = {
    "select": "Selecting",
    "hand": "Panning",
    "pencil": "Pencil",
    "brush": "Brush",
    "eraser": "Erasing",
    "text": "Writing text",
    "rect": "Rectangle",
    "ellipse": "Ellipse",
    "arrow": "Arrow",
    "connector": "Connector",
    "sticky": "Sticky note",
    "mindnode": "Mind map",
    "image": "Image",
    "laser": "Laser pointer",
    "pan": "Panning",
}


class ParticipantManagerDialog(QDialog):
    def __init__(self, manager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Participants")
        self.setModal(False)
        self.resize(770, 560)
        self._participants: list[dict[str, Any]] = []
        self._self = getattr(manager, "self_participant", {}) or {}
        self._is_host = self._self.get("role") == "host"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("users", "#f4f4f5", 24))
        header.addWidget(icon_label)
        title = QLabel("Participants")
        title.setProperty("role", "h2")
        header.addWidget(title)
        self.count_badge = QLabel("0 online")
        self.count_badge.setProperty("role", "badge")
        header.addWidget(self.count_badge)
        header.addStretch(1)
        outer.addLayout(header)
        subtitle = QLabel(
            "The server enforces every role change and removal. Viewers can watch and chat but cannot "
            "modify the canvas; editors can draw and edit; the host controls the session."
        )
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        invite = QFrame()
        invite.setProperty("role", "softCard")
        invite_layout = QHBoxLayout(invite)
        invite_layout.setContentsMargins(14, 10, 14, 10)
        invite_layout.addWidget(QLabel("Room code"))
        code = QLabel(manager.session_code or "—")
        code.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-size: 19px; font-weight: 800; letter-spacing: 2px;"
        )
        invite_layout.addWidget(code)
        invite_layout.addSpacing(16)
        invite_layout.addWidget(QLabel("Server"))
        endpoint = QLabel(f"{manager.host}:{manager.port}")
        endpoint.setProperty("role", "code")
        invite_layout.addWidget(endpoint)
        invite_layout.addStretch(1)
        copy = QPushButton("Copy connection details")
        copy.setObjectName("ghostButton")
        copy.clicked.connect(lambda: self._copy_invite(endpoint.text()))
        invite_layout.addWidget(copy)
        outer.addWidget(invite)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Participant", "Role", "Activity", "Action"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 165)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 120)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(300)
        outer.addWidget(self.table, 1)
        hint = QLabel("Role changes take effect immediately on the server.")
        hint.setProperty("role", "faint")
        outer.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        outer.addWidget(buttons)

        self.manager.presence.connect(self.set_participants)
        initial = getattr(self.manager, "participants", []) or ([self._self] if self._self else [])
        self.set_participants(initial)

    def _copy_invite(self, endpoint: str) -> None:
        QGuiApplication.clipboard().setText(
            f"Join my SynCanvas board\nServer: {endpoint}\nRoom code: {self.manager.session_code}\n"
            "Open SynCanvas → Join a board."
        )

    def set_participants(self, participants: list[dict[str, Any]]) -> None:
        self._participants = [p for p in participants if p.get("online", True)]
        self.count_badge.setText(f"{len(self._participants)} online")
        self._rebuild()

    def _rebuild(self) -> None:
        self.table.setRowCount(len(self._participants))
        for row, participant in enumerate(self._participants):
            self.table.setRowHeight(row, 54)
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(6, 3, 6, 3)
            layout.setSpacing(9)
            avatar = Avatar(participant.get("name", "?"), participant.get("color", "#27272a"), 31)
            layout.addWidget(avatar)
            name_col = QVBoxLayout()
            name_col.setSpacing(0)
            name = QLabel(participant.get("name", "Guest"))
            name.setStyleSheet("font-weight: 700;")
            name_col.addWidget(name)
            if participant.get("id") == self._self.get("id"):
                you = QLabel("You")
                you.setProperty("role", "faint")
                name_col.addWidget(you)
            else:
                joined = QLabel("Connected")
                joined.setProperty("role", "faint")
                name_col.addWidget(joined)
            layout.addLayout(name_col, 1)
            self.table.setCellWidget(row, 0, container)

            role = participant.get("role", "editor")
            if self._is_host and participant.get("id") != self._self.get("id") and role != "host":
                combo = QComboBox()
                combo.addItem("Editor", "editor")
                combo.addItem("Viewer", "viewer")
                combo.setCurrentIndex(max(0, combo.findData(role)))
                combo.currentIndexChanged.connect(
                    lambda _, pid=participant.get("id"), widget=combo: self._change_role(pid, widget.currentData())
                )
                self.table.setCellWidget(row, 1, combo)
            else:
                label = QLabel(
                    {"host": "Admin / Host", "editor": "Editor", "viewer": "Viewer"}.get(role, role.title())
                )
                label.setStyleSheet("font-weight: 600;")
                self.table.setCellWidget(row, 1, label)

            tool = TOOL_NAMES.get(participant.get("tool", "select"), "Idle")
            activity = QLabel(tool)
            activity.setProperty("role", "muted")
            self.table.setCellWidget(row, 2, activity)

            if self._is_host and role != "host" and participant.get("id") != self._self.get("id"):
                remove = QPushButton("Remove")
                remove.setObjectName("dangerButton")
                remove.clicked.connect(lambda _, p=participant: self._kick(p))
                self.table.setCellWidget(row, 3, remove)
            else:
                self.table.setCellWidget(row, 3, QWidget())

    def _change_role(self, participant_id: str, role: str) -> None:
        if not self._is_host or not participant_id:
            return
        self.manager.send("SET_ROLE", {"participant_id": participant_id, "role": role})

    def _kick(self, participant: dict[str, Any]) -> None:
        answer = QMessageBox.question(
            self,
            "Remove participant?",
            f"Remove {participant.get('name', 'this participant')} from the session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.send("KICK_USER", {"participant_id": participant.get("id")})
