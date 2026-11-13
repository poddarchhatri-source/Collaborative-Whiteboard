"""Application settings panel with real, persisted preferences."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from client.config import SettingsStore
from client.ui.icons import icon, pixmap


class ColorField(QWidget):
    changed = Signal(str)

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.button = QPushButton()
        self.button.setFixedSize(34, 28)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._choose)
        layout.addWidget(self.button)
        self.label = QLabel(color.upper())
        self.label.setProperty("role", "code")
        layout.addWidget(self.label)
        layout.addStretch(1)
        self._refresh()

    def _refresh(self) -> None:
        color = QColor(self.color)
        text_color = "#09090b" if color.lightness() > 150 else "#f4f4f5"
        self.button.setStyleSheet(
            f"QPushButton {{ background: {self.color}; border: 1px solid rgba(255,255,255,0.25); "
            f"border-radius: 7px; color: {text_color}; }}"
        )
        self.label.setText(self.color.upper())

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.color), self, "Choose colour")
        if chosen.isValid():
            self.color = chosen.name()
            self._refresh()
            self.changed.emit(self.color)


class SettingsDialog(QDialog):
    settings_changed = Signal(dict)

    def __init__(self, store: SettingsStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        settings = store.settings
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(660, 720)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(22, 20, 22, 18)
        shell.setSpacing(12)
        outer = shell
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("settings", "#f4f4f5", 24))
        header.addWidget(icon_label)
        title = QLabel("Settings")
        title.setProperty("role", "h2")
        header.addWidget(title)
        header.addStretch(1)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 6, 0)
        outer.setSpacing(12)

        # Appearance -----------------------------------------------------
        appearance = QFrame()
        appearance.setProperty("role", "card")
        form = QFormLayout(appearance)
        form.setContentsMargins(16, 14, 16, 16)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        form.addRow(self._section("Appearance"))
        self.theme = QComboBox()
        self.theme.addItem("Midnight (dark)", "midnight")
        self.theme.addItem("Cloud (light)", "cloud")
        index = self.theme.findData(settings.theme)
        self.theme.setCurrentIndex(max(0, index))
        form.addRow("Theme", self.theme)
        self.grid = QCheckBox("Show canvas grid")
        self.grid.setChecked(settings.show_grid)
        form.addRow("Canvas", self.grid)
        self.snap = QCheckBox("Snap shapes and notes to the grid")
        self.snap.setChecked(settings.snap_to_grid)
        form.addRow("", self.snap)
        self.cursors = QCheckBox("Show live cursors of other participants")
        self.cursors.setChecked(settings.show_cursors)
        form.addRow("Presence", self.cursors)
        self.follow = QCheckBox("Follow the host during presentation mode")
        self.follow.setChecked(settings.presentation_follow)
        form.addRow("", self.follow)
        self.reduce_motion = QCheckBox("Reduce animations")
        self.reduce_motion.setChecked(settings.reduce_motion)
        form.addRow("Motion", self.reduce_motion)
        outer.addWidget(appearance)

        # Drawing defaults ------------------------------------------------
        drawing = QFrame()
        drawing.setProperty("role", "card")
        dform = QFormLayout(drawing)
        dform.setContentsMargins(16, 14, 16, 16)
        dform.setHorizontalSpacing(24)
        dform.setVerticalSpacing(10)
        dform.addRow(self._section("Drawing defaults"))
        self.stroke_color = ColorField(settings.stroke_color)
        dform.addRow("Stroke colour", self.stroke_color)
        self.fill_color = ColorField(settings.fill_color)
        dform.addRow("Shape fill", self.fill_color)
        self.sticky_color = ColorField(settings.sticky_color)
        dform.addRow("Sticky note", self.sticky_color)
        width_row = QHBoxLayout()
        self.width = QSlider(Qt.Orientation.Horizontal)
        self.width.setRange(1, 32)
        self.width.setValue(settings.stroke_width)
        self.width_value = QLabel(str(settings.stroke_width))
        self.width_value.setFixedWidth(28)
        self.width.valueChanged.connect(lambda v: self.width_value.setText(str(v)))
        width_row.addWidget(self.width, 1)
        width_row.addWidget(self.width_value)
        width_widget = QWidget()
        width_widget.setLayout(width_row)
        width_row.setContentsMargins(0, 0, 0, 0)
        dform.addRow("Brush width", width_widget)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 96)
        self.font_size.setValue(settings.font_size)
        self.font_size.setSuffix(" px")
        dform.addRow("Text size", self.font_size)
        outer.addWidget(drawing)

        # Connection defaults --------------------------------------------
        connection = QFrame()
        connection.setProperty("role", "card")
        cform = QFormLayout(connection)
        cform.setContentsMargins(16, 14, 16, 16)
        cform.setHorizontalSpacing(24)
        cform.setVerticalSpacing(10)
        cform.addRow(self._section("Connection defaults"))
        server_row = QHBoxLayout()
        self.host = QLineEdit(settings.host)
        self.host.setPlaceholderText("192.168.1.10 or 127.0.0.1")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(settings.port)
        self.port.setFixedWidth(96)
        server_row.addWidget(self.host, 1)
        server_row.addWidget(self.port)
        server_widget = QWidget()
        server_widget.setLayout(server_row)
        server_row.setContentsMargins(0, 0, 0, 0)
        cform.addRow("Default server", server_widget)
        export_row = QHBoxLayout()
        from client.config import default_export_dir

        self.export_dir = QLineEdit(settings.export_directory or str(default_export_dir()))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_export_dir)
        export_row.addWidget(self.export_dir, 1)
        export_row.addWidget(browse)
        export_widget = QWidget()
        export_widget.setLayout(export_row)
        export_row.setContentsMargins(0, 0, 0, 0)
        cform.addRow("Export folder", export_widget)
        note = QLabel(
            "Session passwords are never saved. Reconnect tokens are kept locally so you can resume "
            "a session after a temporary network interruption."
        )
        note.setProperty("role", "faint")
        note.setWordWrap(True)
        cform.addRow("", note)
        outer.addWidget(connection)
        outer.addStretch(1)
        scroll.setWidget(content)
        shell.addWidget(scroll, 1)

        buttons = QDialogButtonBox()
        reset = buttons.addButton("Restore defaults", QDialogButtonBox.ButtonRole.ResetRole)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        save = buttons.addButton("Save settings", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("primaryButton")
        reset.clicked.connect(self._reset)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._save)
        shell.addWidget(buttons)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setProperty("role", "section")
        return label

    def _browse_export_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose export folder", self.export_dir.text() or str(Path.cwd())
        )
        if directory:
            self.export_dir.setText(directory)

    def _reset(self) -> None:
        from client.config import ClientSettings

        defaults = ClientSettings()
        self.theme.setCurrentIndex(max(0, self.theme.findData(defaults.theme)))
        self.grid.setChecked(defaults.show_grid)
        self.snap.setChecked(defaults.snap_to_grid)
        self.cursors.setChecked(defaults.show_cursors)
        self.follow.setChecked(defaults.presentation_follow)
        self.reduce_motion.setChecked(defaults.reduce_motion)
        self.stroke_color.color = defaults.stroke_color
        self.stroke_color._refresh()
        self.fill_color.color = defaults.fill_color
        self.fill_color._refresh()
        self.sticky_color.color = defaults.sticky_color
        self.sticky_color._refresh()
        self.width.setValue(defaults.stroke_width)
        self.font_size.setValue(defaults.font_size)
        self.host.setText(defaults.host)
        self.port.setValue(defaults.port)

    def _save(self) -> None:
        values = {
            "theme": self.theme.currentData(),
            "show_grid": self.grid.isChecked(),
            "snap_to_grid": self.snap.isChecked(),
            "show_cursors": self.cursors.isChecked(),
            "presentation_follow": self.follow.isChecked(),
            "reduce_motion": self.reduce_motion.isChecked(),
            "stroke_color": self.stroke_color.color,
            "fill_color": self.fill_color.color,
            "sticky_color": self.sticky_color.color,
            "stroke_width": self.width.value(),
            "font_size": self.font_size.value(),
            "host": self.host.text().strip() or "127.0.0.1",
            "port": self.port.value(),
            "export_directory": self.export_dir.text().strip(),
        }
        self.store.update(**values)
        self.settings_changed.emit(values)
        self.accept()
