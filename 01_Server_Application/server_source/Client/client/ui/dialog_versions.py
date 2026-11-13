"""Version history with server-side snapshots and real previews."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.canvas.board_view import BoardView
from client.canvas.model import BoardModel
from client.services.exporter import render_board_image
from client.ui.dialog_text import TextEditorDialog
from client.ui.icons import icon, pixmap
from client.ui.widgets import EmptyState, SectionHeader


def _friendly_time(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d %b %Y · %H:%M")
    except (ValueError, TypeError):
        return value or "Unknown time"


def _kind_label(kind: str) -> str:
    return {
        "manual": "Manual",
        "auto": "Automatic",
        "safety": "Safety copy",
    }.get(kind, kind.title())


class SaveVersionDialog(QDialog):
    """Name + description for a manual server-side snapshot."""

    def __init__(self, suggested_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save version")
        self.setModal(True)
        self.resize(500, 330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel("Save a version")
        title.setProperty("role", "h2")
        layout.addWidget(title)
        subtitle = QLabel(
            "A snapshot is stored in SQLite on the server. Add a short note so your team can "
            "recognise this checkpoint later."
        )
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addWidget(QLabel("Name"))
        self.name = QLineEdit(suggested_name)
        self.name.setMaxLength(120)
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Description"))
        self.description = QTextEdit()
        self.description.setPlaceholderText("What changed in this version?")
        self.description.setMaximumHeight(90)
        layout.addWidget(self.description)
        buttons = QDialogButtonBox()
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        save = buttons.addButton("Save version", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("primaryButton")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._accept)
        layout.addWidget(buttons)
        self.name.setFocus()
        self.name.selectAll()

    def _accept(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.name.text().strip(), self.description.toPlainText().strip()


class VersionHistoryDialog(QDialog):
    def __init__(self, manager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Version history")
        self.setModal(False)
        self.resize(980, 640)
        self._snapshots: list[dict[str, Any]] = []
        self._selected_id: str | None = None
        self._role = (getattr(manager, "self_participant", {}) or {}).get("role", "viewer")
        self._preview_model = BoardModel(self)
        self._preview_view = BoardView(self._preview_model, self)
        self._preview_view.set_grid(False)
        self._preview_view.set_read_only(True)
        self._preview_view.set_show_cursors(False)
        self._preview_view.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("history", "#f4f4f5", 24))
        header.addWidget(icon_label)
        title = QLabel("Version history")
        title.setProperty("role", "h2")
        header.addWidget(title)
        header.addSpacing(8)
        self.count_badge = QLabel("0 versions")
        self.count_badge.setProperty("role", "badge")
        header.addWidget(self.count_badge)
        header.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("ghostButton")
        refresh.clicked.connect(self.manager.request_snapshot_list)
        header.addWidget(refresh)
        self.snapshot_button = QPushButton("Save version")
        self.snapshot_button.setObjectName("primaryButton")
        self.snapshot_button.setIcon(icon("save", "#09090b", 16))
        self.snapshot_button.clicked.connect(self._create_snapshot)
        self.snapshot_button.setEnabled(self._role in ("host", "editor"))
        header.addWidget(self.snapshot_button)
        outer.addLayout(header)
        subtitle = QLabel(
            "Snapshots are stored in SQLite on the server. Restoring an earlier version automatically "
            "captures a safety copy first, so the current board is never lost."
        )
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setChildrenCollapsible(False)
        left = QFrame()
        left.setProperty("role", "card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(SectionHeader("Saved versions"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selection_changed)
        left_layout.addWidget(self.list, 1)
        split.addWidget(left)

        right = QFrame()
        right.setProperty("role", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(9)
        self.preview_title = QLabel("Select a version to preview")
        self.preview_title.setProperty("role", "h2")
        right_layout.addWidget(self.preview_title)
        self.preview_meta = QLabel("")
        self.preview_meta.setProperty("role", "faint")
        right_layout.addWidget(self.preview_meta)
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(420, 300)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "background: #0f0f11; border: 1px solid #242428; border-radius: 10px; color: #71717a;"
        )
        self.preview_label.setText("Version preview appears here")
        right_layout.addWidget(self.preview_label, 1)
        self.description = QLabel("")
        self.description.setProperty("role", "muted")
        self.description.setWordWrap(True)
        right_layout.addWidget(self.description)
        action_row = QHBoxLayout()
        self.restore_button = QPushButton("Restore this version")
        self.restore_button.setObjectName("primaryButton")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._restore)
        action_row.addWidget(self.restore_button)
        action_row.addStretch(1)
        if self._role != "host":
            hint = QLabel("Only the host can restore versions")
            hint.setProperty("role", "faint")
            action_row.addWidget(hint)
        right_layout.addLayout(action_row)
        split.addWidget(right)
        split.setSizes([330, 620])
        outer.addWidget(split, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        outer.addWidget(buttons)

        self.manager.snapshot_list.connect(self._on_snapshots)
        self.manager.snapshot_preview.connect(self._on_preview)
        self.manager.snapshot_saved.connect(lambda _: self.manager.request_snapshot_list())
        self.manager.version_restored.connect(lambda _: self.manager.request_snapshot_list())
        QTimer.singleShot(80, self.manager.request_snapshot_list)

    def _on_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        self._snapshots = snapshots
        self.count_badge.setText(f"{len(snapshots)} version{'s' if len(snapshots) != 1 else ''}")
        previous = self._selected_id
        self.list.blockSignals(True)
        self.list.clear()
        for snapshot in snapshots:
            label = (
                f"{snapshot.get('name', 'Snapshot')}\n"
                f"{_kind_label(snapshot.get('kind', ''))} · {_friendly_time(snapshot.get('created_at', ''))}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, snapshot)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if snapshots:
            index = 0
            if previous:
                for i, snapshot in enumerate(snapshots):
                    if snapshot.get("id") == previous:
                        index = i
                        break
            self.list.setCurrentRow(index)
        else:
            self.preview_title.setText("No saved versions yet")
            self.preview_meta.setText("")
            self.preview_label.setText("Save a version to start building a timeline.")
            self.description.setText("")
            self.restore_button.setEnabled(False)

    def _selection_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        snapshot = current.data(Qt.ItemDataRole.UserRole)
        self._selected_id = snapshot.get("id")
        self.preview_title.setText(snapshot.get("name", "Snapshot"))
        size_kb = int(snapshot.get("state_bytes", 0)) // 1024
        self.preview_meta.setText(
            f"{_kind_label(snapshot.get('kind', ''))} · {_friendly_time(snapshot.get('created_at', ''))} · "
            f"seq {snapshot.get('seq', 0)} · {size_kb} KB"
        )
        self.description.setText(snapshot.get("description", "") or "No description")
        self.preview_label.setText("Loading preview…")
        self.restore_button.setEnabled(self._role == "host")
        self.manager.request_snapshot(self._selected_id)

    def _on_preview(self, payload: dict[str, Any]) -> None:
        snapshot = payload.get("snapshot", {})
        if snapshot.get("id") != self._selected_id:
            return
        board = payload.get("board", {})
        self._preview_model.load_board(board)
        image = render_board_image(self._preview_view.scene(), background="dark", scale=0.9, max_pixels=1_500_000)
        if image.isNull():
            self.preview_label.setText("Preview unavailable")
            return
        pixmap = QPixmap.fromImage(image)
        target = self.preview_label.size()
        scaled = pixmap.scaled(
            max(120, target.width() - 8),
            max(100, target.height() - 8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        objects = len(board.get("objects", [])) if isinstance(board, dict) else 0

    def _create_snapshot(self) -> None:
        dialog = SaveVersionDialog(f"Version {datetime.now().strftime('%d %b %H:%M')}", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, description = dialog.values()
        self.manager.send("SNAPSHOT_CREATE", {"name": name, "description": description})

    def _restore(self) -> None:
        if not self._selected_id:
            return
        snapshot = next((s for s in self._snapshots if s.get("id") == self._selected_id), None)
        if not snapshot:
            return
        answer = QMessageBox.question(
            self,
            "Restore version?",
            f"Restore “{snapshot.get('name', 'this version')}”?\n\n"
            "The current board will be saved as a safety snapshot first. "
            "All participants will receive the restored canvas.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.send("SNAPSHOT_RESTORE", {"snapshot_id": self._selected_id})
