"""Qt Graphics View items used by the collaborative canvas.

Items are deliberately data-driven: every visual property comes from the
shared object dictionary, so events from the server can be applied without
rebuilding the scene. Freehand strokes keep points relative to the object
origin, which lets the whole stroke move as one item.
"""

from __future__ import annotations

import base64
import html
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from shared.constants import (
    FILLABLE_KINDS,
    KIND_ARROW,
    KIND_CIRCLE,
    KIND_CONNECTOR,
    KIND_DIAMOND,
    KIND_ELLIPSE,
    KIND_FLOW_DATABASE,
    KIND_FLOW_DECISION,
    KIND_FLOW_DOCUMENT,
    KIND_FLOW_IO,
    KIND_FLOW_PROCESS,
    KIND_FLOW_TERMINATOR,
    KIND_IMAGE,
    KIND_LINE,
    KIND_MINDNODE,
    KIND_RECT,
    KIND_ROUNDED_RECT,
    KIND_STICKY,
    KIND_STROKE,
    KIND_TEXT,
    KIND_TRIANGLE,
    STICKY_COLORS,
)
from shared.models import clone_object

RESIZE_HANDLE = 9.0
PADDING = 6.0


def _color(value: str, fallback: str = "#f4f4f5") -> QColor:
    color = QColor(value)
    return color if color.isValid() else QColor(fallback)


def _font(style: dict[str, Any], scale: float = 1.0) -> QFont:
    family = style.get("font_family", "Inter")
    font = QFont(family)
    if not QFontDatabase.families() or font.family() != family:
        font = QFont("DejaVu Sans")
    font.setPixelSize(max(6, int(float(style.get("font_size", 18)) * scale)))
    if style.get("font_weight") == "bold":
        font.setWeight(QFont.Weight.DemiBold)
    return font


class BoardItem(QGraphicsObject):
    geometryCommitted = Signal(str, dict)
    editRequested = Signal(str)

    def __init__(self, obj: dict[str, Any], view: Any = None) -> None:
        super().__init__()
        self.obj = clone_object(obj)
        self.view = view
        self._pixmap: QPixmap | None = None
        self._doc: QTextDocument | None = None
        self._doc_cache_text = ""
        self._resize_handle: str | None = None
        self._resize_start_rect = QRectF()
        self._resize_start_scene = QPointF()
        self._suppress_commit = False
        self._last_committed = (
            float(self.obj.get("x", 0)),
            float(self.obj.get("y", 0)),
            float(self.obj.get("w", 100)),
            float(self.obj.get("h", 100)),
        )
        flags = (
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self.setPos(float(self.obj.get("x", 0)), float(self.obj.get("y", 0)))
        self.setZValue(float(self.obj.get("z", 0)))
        self.setOpacity(float(self.obj.get("style", {}).get("opacity", 1.0)))
        if self.obj.get("kind") == KIND_IMAGE:
            self._load_pixmap()
        self._rebuild_document()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    @property
    def object_id(self) -> str:
        return self.obj["id"]

    @property
    def kind(self) -> str:
        return self.obj.get("kind", KIND_RECT)

    @property
    def resizable(self) -> bool:
        return self.kind not in (KIND_STROKE, KIND_LINE, KIND_ARROW, KIND_CONNECTOR)

    def rect(self) -> QRectF:
        return QRectF(0, 0, max(2.0, float(self.obj.get("w", 100))), max(2.0, float(self.obj.get("h", 100))))

    def boundingRect(self) -> QRectF:
        if self.kind == KIND_STROKE:
            points = self.obj.get("points", [])
            if points:
                xs = [float(p[0]) for p in points]
                ys = [float(p[1]) for p in points]
                margin = float(self.obj.get("style", {}).get("stroke_width", 3)) / 2 + 10
                return QRectF(min(xs) - margin, min(ys) - margin,
                              max(xs) - min(xs) + margin * 2, max(ys) - min(ys) + margin * 2)
            return QRectF(-5, -5, 10, 10)
        if self.kind in (KIND_LINE, KIND_ARROW) and self.obj.get("points"):
            points = self.obj["points"]
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            margin = float(self.obj.get("style", {}).get("stroke_width", 3)) / 2 + 14
            return QRectF(min(xs) - margin, min(ys) - margin,
                          max(xs) - min(xs) + margin * 2, max(ys) - min(ys) + margin * 2)
        rect = self.rect()
        margin = float(self.obj.get("style", {}).get("stroke_width", 3)) / 2 + 12
        return rect.adjusted(-margin, -margin, margin, margin)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if self.kind == KIND_STROKE:
            stroke_path = QPainterPath()
            points = self.obj.get("points", [])
            if points:
                stroke_path.moveTo(float(points[0][0]), float(points[0][1]))
                for point in points[1:]:
                    stroke_path.lineTo(float(point[0]), float(point[1]))
            stroker = QPainterPathStroker()
            stroker.setWidth(max(10.0, float(self.obj.get("style", {}).get("stroke_width", 3)) + 8))
            return stroker.createStroke(stroke_path)
        if self.kind in (KIND_LINE, KIND_ARROW):
            start, end = self._line_points()
            path.moveTo(start)
            path.lineTo(end)
            stroker = QPainterPathStroker()
            stroker.setWidth(14)
            return stroker.createStroke(path)
        path.addRect(self.rect())
        return path

    def handles(self) -> dict[str, QPointF]:
        if not self.resizable:
            return {}
        rect = self.rect()
        cx, cy = rect.center().x(), rect.center().y()
        return {
            "tl": QPointF(rect.left(), rect.top()),
            "tm": QPointF(cx, rect.top()),
            "tr": QPointF(rect.right(), rect.top()),
            "ml": QPointF(rect.left(), cy),
            "mr": QPointF(rect.right(), cy),
            "bl": QPointF(rect.left(), rect.bottom()),
            "bm": QPointF(cx, rect.bottom()),
            "br": QPointF(rect.right(), rect.bottom()),
        }

    def _handle_at(self, pos: QPointF) -> str | None:
        if not self.isSelected() or (self.view is not None and getattr(self.view, "read_only", False)):
            return None
        radius = RESIZE_HANDLE
        for name, point in self.handles().items():
            if abs(point.x() - pos.x()) <= radius and abs(point.y() - pos.y()) <= radius:
                return name
        return None

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setOpacity(float(self.obj.get("style", {}).get("opacity", 1.0)))
        style = self.obj.get("style", {})
        stroke = _color(style.get("stroke", "#f4f4f5"))
        raw_fill = str(style.get("fill", "none"))
        fill = QColor(raw_fill) if raw_fill not in ("none", "transparent", "") else QColor(0, 0, 0, 0)
        width = max(0.5, float(style.get("stroke_width", 3)))
        pen = QPen(stroke, width, Qt.PenStyle.DashLine if style.get("dashed") else Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        brush = QBrush(fill) if fill.isValid() and fill.alpha() > 0 else Qt.BrushStyle.NoBrush
        rect = self.rect()
        kind = self.kind
        if kind == KIND_STROKE:
            path = QPainterPath()
            points = self.obj.get("points", [])
            if points:
                path.moveTo(float(points[0][0]), float(points[0][1]))
                for point in points[1:]:
                    path.lineTo(float(point[0]), float(point[1]))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif kind in (KIND_LINE, KIND_ARROW):
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            start, end = self._line_points()
            painter.drawLine(start, end)
            if kind == KIND_ARROW:
                self._draw_arrowhead(painter, start, end, stroke, width)
        elif kind == KIND_CONNECTOR:
            self._paint_connector(painter, pen, stroke, width)
        elif kind == KIND_TEXT:
            self._paint_text(painter, rect, style)
        elif kind == KIND_IMAGE:
            self._paint_image(painter, rect, fill, pen)
        elif kind == KIND_STICKY:
            self._paint_sticky(painter, rect, style, pen)
        elif kind == KIND_MINDNODE:
            self._paint_mindnode(painter, rect, style, pen, brush)
        elif kind in (KIND_DIAMOND, KIND_FLOW_DECISION):
            path = self._diamond_path(rect)
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawPath(path)
            self._paint_center_text(painter, rect, style)
        elif kind == KIND_TRIANGLE:
            path = QPainterPath()
            path.moveTo(rect.center().x(), rect.top())
            path.lineTo(rect.right(), rect.bottom())
            path.lineTo(rect.left(), rect.bottom())
            path.closeSubpath()
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawPath(path)
            self._paint_center_text(painter, rect, style)
        elif kind in (KIND_ELLIPSE, KIND_CIRCLE):
            painter.setPen(pen)
            painter.setBrush(brush)
            if kind == KIND_CIRCLE:
                diameter = min(rect.width(), rect.height())
                circle = QRectF(0, 0, diameter, diameter)
                painter.drawEllipse(circle)
                self._paint_center_text(painter, circle, style)
            else:
                painter.drawEllipse(rect)
                self._paint_center_text(painter, rect, style)
        elif kind == KIND_ROUNDED_RECT or kind == KIND_FLOW_TERMINATOR:
            radius = 22 if kind == KIND_FLOW_TERMINATOR else 14
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawRoundedRect(rect, radius, radius)
            self._paint_center_text(painter, rect, style)
        elif kind == KIND_FLOW_IO:
            path = QPainterPath()
            offset = min(24.0, rect.width() * 0.18)
            path.moveTo(rect.left() + offset, rect.top())
            path.lineTo(rect.right(), rect.top())
            path.lineTo(rect.right() - offset, rect.bottom())
            path.lineTo(rect.left(), rect.bottom())
            path.closeSubpath()
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawPath(path)
            self._paint_center_text(painter, rect, style)
        elif kind == KIND_FLOW_DOCUMENT:
            path = QPainterPath()
            wave = min(16.0, rect.height() * 0.12)
            path.moveTo(rect.left(), rect.top())
            path.lineTo(rect.right(), rect.top())
            path.lineTo(rect.right(), rect.bottom() - wave)
            path.cubicTo(
                QPointF(rect.right() - rect.width() * 0.3, rect.bottom() + wave),
                QPointF(rect.left() + rect.width() * 0.3, rect.bottom() - wave * 2),
                QPointF(rect.left(), rect.bottom() - wave),
            )
            path.closeSubpath()
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawPath(path)
            self._paint_center_text(painter, rect.adjusted(0, 0, 0, -wave), style)
        elif kind == KIND_FLOW_DATABASE:
            path = QPainterPath()
            path.addRoundedRect(rect, rect.width() * 0.5, 12)
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawPath(path)
            for y in (rect.top() + rect.height() * 0.3, rect.top() + rect.height() * 0.65):
                painter.drawArc(
                    QRectF(rect.left(), y, rect.width(), rect.height() * 0.22),
                    0, 180 * 16,
                )
            self._paint_center_text(painter, rect, style)
        else:  # rectangle / flow process
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawRoundedRect(rect, 6, 6)
            self._paint_center_text(painter, rect, style)

        if self.isSelected():
            self._paint_selection(painter)

    def _line_points(self) -> tuple[QPointF, QPointF]:
        points = self.obj.get("points")
        if isinstance(points, list) and len(points) >= 2:
            return QPointF(float(points[0][0]), float(points[0][1])), QPointF(
                float(points[1][0]), float(points[1][1])
            )
        return QPointF(0, 0), QPointF(self.rect().width(), self.rect().height())

    def _paint_selection(self, painter: QPainter) -> None:
        painter.save()
        highlight = QColor("#f4f4f5")
        pen = QPen(highlight, 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(-2, -2, 2, 2))
        if self.resizable:
            painter.setPen(QPen(highlight, 1.2))
            painter.setBrush(QBrush(QColor("#18181b")))
            for point in self.handles().values():
                painter.drawRect(
                    QRectF(point.x() - RESIZE_HANDLE / 2, point.y() - RESIZE_HANDLE / 2,
                           RESIZE_HANDLE, RESIZE_HANDLE)
                )
        painter.restore()

    def _paint_connector(self, painter: QPainter, pen: QPen, stroke: QColor, width: float) -> None:
        start, end = self._connector_points()
        if start is None or end is None:
            painter.setPen(QPen(QColor("#71717a"), 2, Qt.PenStyle.DashLine))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "connector")
            return
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(start, end)
        if self.obj.get("connector", {}).get("arrow", True):
            self._draw_arrowhead(painter, start, end, stroke, width)
        label = self.obj.get("connector", {}).get("label", "")
        if label:
            midpoint = (start + end) / 2
            painter.setPen(QPen(QColor("#d4d4d8")))
            painter.setFont(_font({"font_size": 12}))
            painter.drawText(midpoint + QPointF(8, -8), label)

    def _connector_points(self) -> tuple[QPointF, QPointF] | tuple[None, None]:
        if self.scene() is None or self.view is None:
            return None, None
        from_id = self.obj.get("connector", {}).get("from_id")
        to_id = self.obj.get("connector", {}).get("to_id")
        source = self.view.item_for_id(from_id) if from_id else None
        target = self.view.item_for_id(to_id) if to_id else None
        if source is None or target is None:
            return None, None
        start = source.sceneBoundingRect().center()
        end = target.sceneBoundingRect().center()
        # Anchor to the border rather than the center for cleaner diagrams.
        delta = end - start
        if abs(delta.x()) > 1 or abs(delta.y()) > 1:
            source_rect = source.sceneBoundingRect()
            target_rect = target.sceneBoundingRect()
            start = self._border_point(source_rect, delta)
            end = self._border_point(target_rect, -delta)
        return self.mapFromScene(start), self.mapFromScene(end)

    @staticmethod
    def _border_point(rect: QRectF, direction: QPointF) -> QPointF:
        center = rect.center()
        dx, dy = direction.x(), direction.y()
        if dx == 0 and dy == 0:
            return center
        scale = min(
            (rect.width() / 2) / abs(dx) if dx else float("inf"),
            (rect.height() / 2) / abs(dy) if dy else float("inf"),
        )
        return center + QPointF(dx * scale, dy * scale)

    def _draw_arrowhead(self, painter: QPainter, start: QPointF, end: QPointF, color: QColor, width: float) -> None:
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = max(9.0, width * 3)
        left = end - QPointF(math.cos(angle - 0.45) * size, math.sin(angle - 0.45) * size)
        right = end - QPointF(math.cos(angle + 0.45) * size, math.sin(angle + 0.45) * size)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([end, left, right]))

    def _paint_text(self, painter: QPainter, rect: QRectF, style: dict[str, Any]) -> None:
        self._ensure_document()
        if self._doc is None:
            return
        text_height = self._doc.size().height()
        y = max(0.0, (rect.height() - text_height) / 2)
        painter.save()
        painter.translate(0, y)
        self._doc.drawContents(painter, QRectF(0, 0, rect.width(), max(text_height, rect.height())))
        painter.restore()

    def _paint_center_text(self, painter: QPainter, rect: QRectF, style: dict[str, Any]) -> None:
        text = str(self.obj.get("text", "")).strip()
        if not text:
            return
        self._ensure_document()
        if self._doc is None:
            return
        height = self._doc.size().height()
        width = min(rect.width() - 20, self._doc.textWidth())
        x = rect.left() + (rect.width() - width) / 2
        y = rect.center().y() - height / 2
        painter.save()
        painter.translate(x, y)
        self._doc.drawContents(painter, QRectF(0, 0, width, height + 2))
        painter.restore()

    def _paint_sticky(self, painter: QPainter, rect: QRectF, style: dict[str, Any], pen: QPen) -> None:
        painter.save()
        shadow = QPainterPath()
        shadow.addRoundedRect(rect.translated(4, 6), 4, 4)
        painter.fillPath(shadow, QColor(0, 0, 0, 65))
        painter.setPen(QPen(QColor(0, 0, 0, 35), 1))
        painter.setBrush(QBrush(_color(style.get("fill", STICKY_COLORS[0]), STICKY_COLORS[0])))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QPen(_color("#92400e", "#92400e"), 1))
        painter.setFont(_font({"font_size": 10}))
        painter.drawText(rect.adjusted(10, 4, -8, 0), Qt.AlignmentFlag.AlignRight, "●")
        self._ensure_document()
        if self._doc:
            painter.translate(rect.left() + 12, rect.top() + 14)
            self._doc.drawContents(painter, QRectF(0, 0, rect.width() - 24, rect.height() - 24))
        painter.restore()

    def _paint_mindnode(
        self, painter: QPainter, rect: QRectF, style: dict[str, Any], pen: QPen, brush: QBrush
    ) -> None:
        radius = rect.height() / 2
        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(QColor("#f4f4f5")))
        self._paint_center_text(painter, rect, style)

    def _paint_image(self, painter: QPainter, rect: QRectF, fill: QColor, pen: QPen) -> None:
        painter.save()
        shadow = QPainterPath()
        shadow.addRoundedRect(rect.translated(3, 5), 6, 6)
        painter.fillPath(shadow, QColor(0, 0, 0, 60))
        painter.setPen(QPen(QColor(255, 255, 255, 25), 1))
        if self._pixmap and not self._pixmap.isNull():
            painter.setBrush(QBrush(_color("#0f0f11", "#0f0f11")))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setClipPath(self._rounded_path(rect, 6))
            painter.drawPixmap(rect.toRect(), self._pixmap)
        else:
            painter.setBrush(QBrush(QColor("#18181b")))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QPen(QColor("#a1a1aa")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Image")
        painter.restore()

    @staticmethod
    def _rounded_path(rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    @staticmethod
    def _diamond_path(rect: QRectF) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(rect.center().x(), rect.top())
        path.lineTo(rect.right(), rect.center().y())
        path.lineTo(rect.center().x(), rect.bottom())
        path.lineTo(rect.left(), rect.center().y())
        path.closeSubpath()
        return path

    def _ensure_document(self) -> None:
        text = str(self.obj.get("text", ""))
        style = self.obj.get("style", {})
        key = (
            text,
            style.get("font_family"),
            style.get("font_size"),
            style.get("font_weight"),
            style.get("text_color"),
            style.get("stroke"),
            self.obj.get("w"),
        )
        if self._doc is not None and self._doc_cache_text == repr(key):
            return
        doc = QTextDocument()
        doc.setDocumentMargin(2)
        doc.setDefaultFont(_font(style))
        doc.setTextWidth(max(10, float(self.obj.get("w", 100)) - 16))
        doc.setPlainText(text)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        if self.kind == KIND_STICKY:
            option.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            doc.setDocumentMargin(0)
        elif self.kind == KIND_TEXT:
            option.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        else:
            option.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        doc.setDefaultTextOption(option)
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setForeground(QBrush(_color(style.get("text_color") or style.get("stroke") or "#f4f4f5")))
        cursor.mergeCharFormat(fmt)
        self._doc = doc
        self._doc_cache_text = repr(key)

    def _load_pixmap(self) -> None:
        data = self.obj.get("image", {}).get("data", "")
        if not data:
            return
        try:
            raw = base64.b64decode(data)
            image = QImage.fromData(raw)
            if not image.isNull():
                self._pixmap = QPixmap.fromImage(image)
        except Exception:
            self._pixmap = None

    def _rebuild_document(self) -> None:
        self._doc = None
        self._doc_cache_text = ""
        if self.kind == KIND_IMAGE:
            self._pixmap = None
            self._load_pixmap()
        self._ensure_document()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._handle_at(event.pos())
            if handle:
                self._resize_handle = handle
                self._resize_start_rect = self.rect()
                self._resize_start_scene = event.scenePos()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_handle:
            delta = event.scenePos() - self._resize_start_scene
            self._apply_resize(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self.view is not None and self.isSelected():
            self.view.update_alignment_guides(self)

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_handle:
            self._resize_handle = None
            self.setSelected(True)
            self._emit_geometry_patch()
            event.accept()
            return
        if self.view is not None and getattr(self.view, "snap_to_grid", False):
            grid = float(getattr(self.view, "grid_size", 24))
            pos = self.pos()
            self.setPos(round(pos.x() / grid) * grid, round(pos.y() / grid) * grid)
        super().mouseReleaseEvent(event)
        if self.view is not None:
            self.view.commit_selected_moves()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.kind in (KIND_TEXT, KIND_STICKY, KIND_MINDNODE, KIND_CONNECTOR) or str(self.obj.get("text", "")):
            self.editRequested.emit(self.object_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def hoverMoveEvent(self, event) -> None:
        if self.resizable and self.isSelected():
            handle = self._handle_at(event.pos())
            cursors = {
                "tl": Qt.CursorShape.SizeFDiagCursor,
                "br": Qt.CursorShape.SizeFDiagCursor,
                "tr": Qt.CursorShape.SizeBDiagCursor,
                "bl": Qt.CursorShape.SizeBDiagCursor,
                "tm": Qt.CursorShape.SizeVerCursor,
                "bm": Qt.CursorShape.SizeVerCursor,
                "ml": Qt.CursorShape.SizeHorCursor,
                "mr": Qt.CursorShape.SizeHorCursor,
            }
            self.setCursor(cursors.get(handle, Qt.CursorShape.SizeAllCursor if handle else Qt.CursorShape.ArrowCursor))
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def _apply_resize(self, delta: QPointF) -> None:
        rect = QRectF(self._resize_start_rect)
        mode = self._resize_handle or ""
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        if "l" in mode:
            left += delta.x()
        if "r" in mode:
            right += delta.x()
        if "t" in mode:
            top += delta.y()
        if "b" in mode:
            bottom += delta.y()
        if right - left < 16:
            if "l" in mode:
                left = right - 16
            else:
                right = left + 16
        if bottom - top < 16:
            if "t" in mode:
                top = bottom - 16
            else:
                bottom = top + 16
        new_rect = QRectF(left, top, right - left, bottom - top).normalized()
        self.prepareGeometryChange()
        self.obj["w"] = round(new_rect.width(), 2)
        self.obj["h"] = round(new_rect.height(), 2)
        self.setPos(self.pos() + QPointF(new_rect.x(), new_rect.y()))
        self.update()

    def _emit_geometry_patch(self) -> None:
        if self._suppress_commit:
            return
        self._last_committed = (
            float(self.obj.get("x", 0)),
            float(self.obj.get("y", 0)),
            float(self.obj.get("w", 100)),
            float(self.obj.get("h", 100)),
        )
        self.geometryCommitted.emit(
            self.object_id,
            {
                "x": round(self.pos().x(), 2),
                "y": round(self.pos().y(), 2),
                "w": round(float(self.obj.get("w", 100)), 2),
                "h": round(float(self.obj.get("h", 100)), 2),
            },
        )

    def commit_move_if_needed(self) -> None:
        if self._suppress_commit:
            return
        pos = self.pos()
        if (
            abs(pos.x() - self._last_committed[0]) > 0.01
            or abs(pos.y() - self._last_committed[1]) > 0.01
        ):
            self.obj["x"] = round(pos.x(), 2)
            self.obj["y"] = round(pos.y(), 2)
            self._emit_geometry_patch()

    def apply_object(self, obj: dict[str, Any]) -> None:
        self._suppress_commit = True
        try:
            self.obj = clone_object(obj)
            self.setPos(float(obj.get("x", 0)), float(obj.get("y", 0)))
            self.setZValue(float(obj.get("z", 0)))
            self.setOpacity(float(obj.get("style", {}).get("opacity", 1.0)))
            self.prepareGeometryChange()
            self._rebuild_document()
            self.update()
        finally:
            self._suppress_commit = False
        self._last_committed = (
            float(obj.get("x", 0)),
            float(obj.get("y", 0)),
            float(obj.get("w", 100)),
            float(obj.get("h", 100)),
        )

    def refresh_connector(self) -> None:
        if self.kind == KIND_CONNECTOR:
            self.prepareGeometryChange()
            self.update()


class ConnectorItem(BoardItem):
    """A connector whose endpoints follow the linked objects."""

    def __init__(self, obj: dict[str, Any], view: Any = None) -> None:
        super().__init__(obj, view)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)

    def boundingRect(self) -> QRectF:
        start, end = self._connector_points()
        if start is None or end is None:
            return QRectF(-40, -20, 80, 40)
        rect = QRectF(start, end).normalized().adjusted(-14, -14, 14, 14)
        return rect

    def shape(self) -> QPainterPath:
        start, end = self._connector_points()
        path = QPainterPath()
        if start is None or end is None:
            return path
        path.moveTo(start)
        path.lineTo(end)
        stroker = QPainterPathStroker()
        stroker.setWidth(14)
        return stroker.createStroke(path)

    def _border_point(self, rect: QRectF, direction: QPointF) -> QPointF:
        return super()._border_point(rect, direction)

    def _connector_points(self):
        if self.scene() is None or self.view is None:
            return None, None
        from_id = self.obj.get("connector", {}).get("from_id")
        to_id = self.obj.get("connector", {}).get("to_id")
        source = self.view.item_for_id(from_id) if from_id else None
        target = self.view.item_for_id(to_id) if to_id else None
        if source is None or target is None:
            return None, None
        start = source.sceneBoundingRect().center()
        end = target.sceneBoundingRect().center()
        delta = end - start
        if abs(delta.x()) > 1 or abs(delta.y()) > 1:
            start = self._border_point(source.sceneBoundingRect(), delta)
            end = self._border_point(target.sceneBoundingRect(), -delta)
        return self.mapFromScene(start), self.mapFromScene(end)

    def _paint_selection(self, painter: QPainter) -> None:
        start, end = self._connector_points()
        if start is None or end is None:
            return
        painter.save()
        pen = QPen(QColor("#f4f4f5"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(start, end)
        painter.restore()

    @property
    def resizable(self) -> bool:
        return False


def make_item(obj: dict[str, Any], view: Any = None) -> BoardItem:
    if obj.get("kind") == KIND_CONNECTOR:
        return ConnectorItem(obj, view)
    return BoardItem(obj, view)
