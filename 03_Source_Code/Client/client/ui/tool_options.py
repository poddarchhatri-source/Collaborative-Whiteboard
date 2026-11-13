"""Floating canvas options: colour palette, width, grid and templates."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import icon
from client.ui.widgets import ColorButton
from shared.constants import PALETTE


class ToolOptionsBar(QFrame):
    colorChanged = Signal(str)
    fillChanged = Signal(str)
    stickyChanged = Signal(str)
    widthChanged = Signal(int)
    fontChanged = Signal(int)
    gridToggled = Signal(bool)
    snapToggled = Signal(bool)
    templatesRequested = Signal()
    laserToggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolOptionsBar")
        self.setStyleSheet(
            "QFrame#ToolOptionsBar { background: rgba(24,24,27,0.96); border: 1px solid #2e2e33;"
            " border-radius: 11px; }"
        )
        self._color = "#f4f4f5"
        self._fill = "#27272a"
        self._sticky = "#fef9c3"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(5)
        self.tool_label = QLabel("Select")
        self.tool_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #f4f4f5; padding-right: 2px;")
        self.tool_label.setMinimumWidth(48)
        layout.addWidget(self.tool_label)
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: #2e2e33;")
        layout.addWidget(divider)
        self.swatches: list[ColorButton] = []
        for index, color in enumerate(PALETTE[:8]):
            button = ColorButton(color, 18)
            button.setToolTip(f"Stroke colour {color}")
            button.clicked.connect(lambda checked=False, c=color: self.set_stroke_color(c))
            self.swatches.append(button)
            layout.addWidget(button)
        custom = QToolButton()
        custom.setText("+")
        custom.setToolTip("Custom stroke colour")
        custom.setFixedSize(27, 27)
        custom.clicked.connect(self._pick_custom)
        layout.addWidget(custom)
        self.fill_button = QToolButton()
        self.fill_button.setToolTip("Shape fill colour")
        self.fill_button.setFixedSize(27, 27)
        self._refresh_fill_icon()
        self.fill_button.clicked.connect(self._pick_fill)
        layout.addWidget(self.fill_button)
        self.sticky_button = QToolButton()
        self.sticky_button.setToolTip("Sticky note colour")
        self.sticky_button.setFixedSize(27, 27)
        self._refresh_sticky_icon()
        self.sticky_button.clicked.connect(self._pick_sticky)
        layout.addWidget(self.sticky_button)
        divider2 = QFrame()
        divider2.setFixedWidth(1)
        divider2.setStyleSheet("background: #2e2e33;")
        layout.addWidget(divider2)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(1, 32)
        self.width_slider.setValue(3)
        self.width_slider.setFixedWidth(74)
        self.width_slider.setToolTip("Brush / border width")
        self.width_slider.valueChanged.connect(self._width_changed)
        layout.addWidget(self.width_slider)
        self.width_label = QLabel("3")
        self.width_label.setFixedWidth(16)
        self.width_label.setProperty("role", "faint")
        layout.addWidget(self.width_label)
        divider3 = QFrame()
        divider3.setFixedWidth(1)
        divider3.setStyleSheet("background: #2e2e33;")
        layout.addWidget(divider3)
        self.grid_button = QToolButton()
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        self.grid_button.setIcon(icon("grid", "#f4f4f5", 17))
        self.grid_button.setToolTip("Toggle grid (G)")
        self.grid_button.toggled.connect(self.gridToggled)
        layout.addWidget(self.grid_button)
        self.snap_button = QToolButton()
        self.snap_button.setCheckable(True)
        self.snap_button.setIcon(icon("fit", "#a1a1aa", 17))
        self.snap_button.setToolTip("Snap shapes to the grid")
        self.snap_button.toggled.connect(self.snapToggled)
        layout.addWidget(self.snap_button)
        self.laser_button = QToolButton()
        self.laser_button.setCheckable(True)
        self.laser_button.setIcon(icon("laser", "#ef4444", 17))
        self.laser_button.setToolTip("Laser pointer (L)")
        self.laser_button.toggled.connect(self.laserToggled)
        layout.addWidget(self.laser_button)
        self.template_button = QToolButton()
        self.template_button.setIcon(icon("template", "#f4f4f5", 17))
        self.template_button.setToolTip("Flowchart templates")
        self.template_button.clicked.connect(self.templatesRequested)
        layout.addWidget(self.template_button)

    def set_tool(self, tool: str, label: str) -> None:
        self.tool_label.setText(label[:14])
        self.laser_button.blockSignals(True)
        self.laser_button.setChecked(tool == "laser")
        self.laser_button.blockSignals(False)

    def set_stroke_color(self, color: str) -> None:
        self._color = color
        self.colorChanged.emit(color)

    def set_width(self, width: int) -> None:
        self.width_slider.blockSignals(True)
        self.width_slider.setValue(max(1, min(32, int(width))))
        self.width_label.setText(str(self.width_slider.value()))
        self.width_slider.blockSignals(False)

    def set_grid(self, visible: bool) -> None:
        self.grid_button.blockSignals(True)
        self.grid_button.setChecked(bool(visible))
        self.grid_button.blockSignals(False)

    def set_snap(self, enabled: bool) -> None:
        self.snap_button.blockSignals(True)
        self.snap_button.setChecked(bool(enabled))
        self.snap_button.blockSignals(False)

    def set_laser(self, active: bool) -> None:
        self.laser_button.blockSignals(True)
        self.laser_button.setChecked(bool(active))
        self.laser_button.blockSignals(False)

    def _width_changed(self, value: int) -> None:
        self.width_label.setText(str(value))
        self.widthChanged.emit(value)

    def _pick_custom(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Stroke colour")
        if color.isValid():
            self.set_stroke_color(color.name())

    def _pick_fill(self) -> None:
        color = QColorDialog.getColor(QColor(self._fill), self, "Fill colour")
        if color.isValid():
            self._fill = color.name()
            self._refresh_fill_icon()
            self.fillChanged.emit(self._fill)

    def _pick_sticky(self) -> None:
        color = QColorDialog.getColor(QColor(self._sticky), self, "Sticky note colour")
        if color.isValid():
            self._sticky = color.name()
            self._refresh_sticky_icon()
            self.stickyChanged.emit(self._sticky)

    def _refresh_fill_icon(self) -> None:
        self.fill_button.setStyleSheet(
            f"QToolButton {{ background: {self._fill}; border: 1px solid rgba(255,255,255,0.28); border-radius: 6px; }}"
        )

    def _refresh_sticky_icon(self) -> None:
        self.sticky_button.setStyleSheet(
            f"QToolButton {{ background: {self._sticky}; border: 1px solid rgba(255,255,255,0.28); border-radius: 6px; }}"
        )
