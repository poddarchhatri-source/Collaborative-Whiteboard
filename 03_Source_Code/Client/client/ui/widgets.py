"""Reusable widgets for SynCanvas's desktop UI."""

from __future__ import annotations

import math
import time
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
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.ui.icons import pixmap
from client.ui.theme import theme_colors


class Avatar(QWidget):
    def __init__(self, name: str = "?", color: str = "#27272a", size: int = 34, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self.color = color
        self._size = size
        self.setFixedSize(size, size)

    def set_participant(self, name: str, color: str) -> None:
        self.name = name
        self.color = color
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        base = QColor(self.color)
        if not base.isValid():
            base = QColor("#27272a")
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, base.lighter(122))
        gradient.setColorAt(1, base.darker(112))
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
        painter.setBrush(gradient)
        painter.drawEllipse(rect)
        initials = "".join(part[0] for part in self.name.split()[:2]).upper() or "?"
        painter.setPen(QPen(QColor("#f4f4f5")))
        font = QFont(self.font())
        font.setPixelSize(max(10, int(self._size * 0.34)))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initials)


class StatusPill(QFrame):
    def __init__(self, text: str = "Offline", state: str = "offline", parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._text = text
        self.setObjectName("StatusPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFixedHeight(27)
        self._label = QLabel(text, self)
        self._label.setStyleSheet("font-size: 11px; font-weight: 700; background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 2, 11, 2)
        layout.setSpacing(7)
        layout.addWidget(self._label)
        self._colors = {
            "online": "#22c55e",
            "connecting": "#f59e0b",
            "reconnecting": "#f59e0b",
            "offline": "#71717a",
            "error": "#ef4444",
            "presenting": "#f4f4f5",
        }
        self.set_state(state, text)

    def set_state(self, state: str, text: str | None = None) -> None:
        self._state = state
        if text is not None:
            self._text = text
            self._label.setText(text)
        color = self._colors.get(state, "#64748b")
        self._label.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {color}; background: transparent;")
        self.setStyleSheet(
            f"QFrame#StatusPill {{ background: {self._alpha(color, 22)}; "
            f"border: 1px solid {self._alpha(color, 70)}; border-radius: 13px; }}"
        )
        self.update()

    @staticmethod
    def _alpha(hex_color: str, alpha: int) -> str:
        color = QColor(hex_color)
        return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._colors.get(self._state, "#64748b"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        blink = self._state in ("connecting", "reconnecting") and int(time.time() * 2) % 2 == 0
        painter.setOpacity(0.45 if blink else 1.0)
        painter.drawEllipse(QPointF(10.5, self.height() / 2), 3.2, 3.2)


class SectionHeader(QWidget):
    def __init__(self, title: str, action_text: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(title.upper())
        label.setProperty("role", "section")
        layout.addWidget(label)
        layout.addStretch(1)
        self.action = QPushButton(action_text)
        self.action.setObjectName("linkButton")
        self.action.setVisible(bool(action_text))
        layout.addWidget(self.action)


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "—", icon_name: str = "network", color: str = "#e4e4e7", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "softCard")
        self.setMinimumHeight(82)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)
        icon_label = QLabel()
        icon_label.setPixmap(pixmap(icon_name, color, 22))
        icon_label.setFixedSize(24, 24)
        outer.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("font-size: 19px; font-weight: 800;")
        self.caption = QLabel(label)
        self.caption.setProperty("role", "faint")
        col.addWidget(self.value_label)
        col.addWidget(self.caption)
        outer.addLayout(col, 1)
        self._color = color

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class ColorButton(QToolButton):
    def __init__(self, color: str, size: int = 22, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(size + 8, size + 8)
        self.setToolTip(color)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(rect)
        if self.isChecked():
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))


class Sparkline(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.values: list[float] = []
        self.color = "#e4e4e7"
        self.setMinimumHeight(92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_values(self, values: list[float], color: str = "#e4e4e7") -> None:
        self.values = [max(0.0, float(v)) for v in values][-80:]
        self.color = color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        painter.setPen(QPen(QColor(148, 163, 184, 22), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        if not self.values:
            painter.setPen(QPen(QColor("#71717a")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Collecting live samples…")
            return
        values = list(self.values)
        if len(values) == 1:
            values = [values[0], values[0]]
        max_value = max(1.0, max(values))
        step = rect.width() / max(1, len(values) - 1)
        points = [
            QPointF(rect.left() + i * step, rect.bottom() - (value / max_value) * rect.height())
            for i, value in enumerate(values)
        ]
        path = QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        fill_path = QPainterPath(path)
        fill_path.lineTo(rect.bottomRight())
        fill_path.lineTo(rect.bottomLeft())
        fill_path.closeSubpath()
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        base = QColor(self.color)
        top = QColor(base)
        top.setAlpha(90)
        bottom = QColor(base)
        bottom.setAlpha(4)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)
        painter.fillPath(fill_path, gradient)
        painter.setPen(QPen(base, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        last = points[-1]
        painter.setBrush(base)
        painter.setPen(QPen(QColor("#0d0d0f"), 2))
        painter.drawEllipse(last, 3.6, 3.6)


class MiniMap(QWidget):
    """Overview of board objects with the current viewport rectangle."""

    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.setMinimumSize(190, 130)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to navigate the board")
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def _bounds(self) -> QRectF:
        items = list(getattr(self.view, "_items", {}).values())
        if not items:
            return QRectF(-500, -350, 1000, 700)
        rect = QRectF()
        for item in items:
            rect = item.sceneBoundingRect() if rect.isNull() else rect.united(item.sceneBoundingRect())
        rect = rect.adjusted(-120, -100, 120, 100)
        if rect.width() < 40 or rect.height() < 40:
            rect = rect.adjusted(-300, -200, 300, 200)
        return rect

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(35, 48, 75), 1))
        painter.setBrush(QColor(10, 16, 31))
        painter.drawRoundedRect(rect, 9, 9)
        scene_bounds = self._bounds()
        inner = QRectF(rect).adjusted(8, 8, -8, -8)
        scale = min(inner.width() / scene_bounds.width(), inner.height() / scene_bounds.height())
        offset = inner.center() - QPointF(scene_bounds.center().x() * scale, scene_bounds.center().y() * scale)

        def map_rect(r: QRectF) -> QRectF:
            return QRectF(
                r.x() * scale + offset.x(),
                r.y() * scale + offset.y(),
                max(1.5, r.width() * scale),
                max(1.5, r.height() * scale),
            )

        for item in getattr(self.view, "_items", {}).values():
            if item.kind == "connector":
                continue
            color = QColor(item.obj.get("style", {}).get("fill", "#27272a"))
            if not color.isValid() or color.alpha() == 0:
                color = QColor(item.obj.get("style", {}).get("stroke", "#a1a1aa"))
            color.setAlpha(170)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(map_rect(item.sceneBoundingRect()), 1.5, 1.5)
        viewport = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        view_rect = map_rect(viewport)
        painter.setPen(QPen(QColor("#f4f4f5"), 1.5))
        painter.setBrush(QColor(244, 244, 245, 20))
        painter.drawRect(view_rect)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self.rect().adjusted(1, 1, -1, -1)
        scene_bounds = self._bounds()
        inner = QRectF(rect).adjusted(8, 8, -8, -8)
        scale = min(inner.width() / scene_bounds.width(), inner.height() / scene_bounds.height())
        offset = inner.center() - QPointF(scene_bounds.center().x() * scale, scene_bounds.center().y() * scale)
        point = event.position()
        scene_x = (point.x() - offset.x()) / scale
        scene_y = (point.y() - offset.y()) / scale
        self.view.centerOn(scene_x, scene_y)
        if getattr(self.view, "_is_host_presenting", False):
            self.view._emit_viewport(force=True)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 26, 20, 26)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label = QLabel()
        icon_label.setPixmap(pixmap(icon_name, "#71717a", 34))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setProperty("role", "faint")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub.setWordWrap(True)
            layout.addWidget(sub)


class ToastManager:
    """Lightweight, non-blocking in-window notifications."""

    def __init__(self, window: QWidget) -> None:
        self.window = window
        self._toasts: list[QWidget] = []

    def show(self, message: str, kind: str = "info", duration: int = 3200) -> None:
        colors = {
            "info": ("#f4f4f5", "chat"),
            "success": ("#22c55e", "check"),
            "warning": ("#f59e0b", "warning"),
            "error": ("#ef4444", "warning"),
        }
        color, icon_name = colors.get(kind, colors["info"])
        frame = QFrame(self.window)
        frame.setObjectName("Toast")
        frame.setStyleSheet(
            f"QFrame#Toast {{ background: #18181b; border: 1px solid #2e2e33; "
            f"border-left: 3px solid {color}; border-radius: 10px; }}"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(11, 8, 12, 8)
        layout.setSpacing(8)
        icon_label = QLabel()
        icon_label.setPixmap(pixmap(icon_name, color, 17))
        layout.addWidget(icon_label)
        label = QLabel(message)
        label.setWordWrap(True)
        label.setMaximumWidth(420)
        label.setStyleSheet("color: #f4f4f5; font-size: 12px; background: transparent;")
        layout.addWidget(label)
        frame.adjustSize()
        frame.setFixedWidth(min(460, max(260, frame.width())))
        effect = QGraphicsOpacityEffect(frame)
        effect.setOpacity(0.0)
        frame.setGraphicsEffect(effect)
        self._toasts.append(frame)
        self._position()
        frame.show()
        frame.raise_()
        from PySide6.QtCore import QPropertyAnimation

        animation = QPropertyAnimation(effect, b"opacity", frame)
        animation.setDuration(160)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start()
        frame._anim = animation  # type: ignore[attr-defined]
        QTimer.singleShot(duration, lambda: self._dismiss(frame))

    def _dismiss(self, frame: QWidget) -> None:
        if frame not in self._toasts:
            return
        effect = frame.graphicsEffect()
        if effect is not None:
            from PySide6.QtCore import QPropertyAnimation

            animation = QPropertyAnimation(effect, b"opacity", frame)
            animation.setDuration(180)
            animation.setStartValue(effect.opacity())
            animation.setEndValue(0.0)
            animation.finished.connect(lambda: self._remove(frame))
            animation.start()
            frame._anim = animation  # type: ignore[attr-defined]
        else:
            self._remove(frame)

    def _remove(self, frame: QWidget) -> None:
        if frame in self._toasts:
            self._toasts.remove(frame)
        frame.deleteLater()
        self._position()

    def _position(self) -> None:
        margin = 18
        bottom = self.window.height() - 66
        for frame in reversed(self._toasts):
            frame.adjustSize()
            frame.move(max(margin, (self.window.width() - frame.width()) // 2), bottom - frame.height())
            bottom -= frame.height() + 8
            frame.raise_()
