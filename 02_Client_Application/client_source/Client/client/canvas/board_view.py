"""Interactive Qt Graphics View canvas with collaborative tools.

The view owns only presentation state and local interaction.  Document state
always lives in :class:`BoardModel`; every optimistic edit is reconciled by a
canonical server event.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

from PySide6.QtCore import QElapsedTimer, QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
)

from client.canvas.items import BoardItem, make_item
from client.canvas.model import BoardModel
from shared.constants import (
    DEFAULT_STROKE,
    GRID_SIZE,
    KIND_ARROW,
    KIND_CIRCLE,
    KIND_CONNECTOR,
    KIND_DIAMOND,
    KIND_ELLIPSE,
    KIND_FLOW_DECISION,
    KIND_FLOW_DOCUMENT,
    KIND_FLOW_IO,
    KIND_FLOW_PROCESS,
    KIND_FLOW_TERMINATOR,
    KIND_LINE,
    KIND_MINDNODE,
    KIND_RECT,
    KIND_ROUNDED_RECT,
    KIND_STROKE,
    KIND_STICKY,
    KIND_TEXT,
    KIND_TRIANGLE,
    MAX_ZOOM,
    MIN_ZOOM,
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
)
from shared.models import clone_object

SHAPE_TOOLS = {
    TOOL_RECT: KIND_RECT,
    TOOL_ROUNDED_RECT: KIND_ROUNDED_RECT,
    TOOL_ELLIPSE: KIND_ELLIPSE,
    TOOL_CIRCLE: KIND_CIRCLE,
    TOOL_DIAMOND: KIND_DIAMOND,
    TOOL_TRIANGLE: KIND_TRIANGLE,
    TOOL_LINE: KIND_LINE,
    TOOL_ARROW: KIND_ARROW,
    TOOL_FLOW_START: KIND_FLOW_TERMINATOR,
    TOOL_FLOW_PROCESS: KIND_FLOW_PROCESS,
    TOOL_FLOW_DECISION: KIND_FLOW_DECISION,
    TOOL_FLOW_IO: KIND_FLOW_IO,
}


class BoardView(QGraphicsView):
    cursorMoved = Signal(float, float, str)
    createObjectRequested = Signal(dict)
    updateObjectRequested = Signal(str, dict, int)
    deleteObjectsRequested = Signal(list)
    strokeAppendRequested = Signal(str, list)
    strokeEndRequested = Signal(str)
    textInsertRequested = Signal(float, float)
    stickyInsertRequested = Signal(float, float)
    mindNodeInsertRequested = Signal(float, float, str)
    imageInsertRequested = Signal(float, float)
    textEditRequested = Signal(str)
    selectionObjectsChanged = Signal(list)
    zoomChanged = Signal(float)
    laserMoved = Signal(float, float)
    viewportChanged = Signal(dict)
    notice = Signal(str, str)

    def __init__(self, model: BoardModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.setScene(QGraphicsScene(self))
        self.setSceneRect(-6000, -6000, 12000, 12000)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheNone)
        self.setBackgroundBrush(QBrush(QColor("#0f0f11")))

        self.tool = TOOL_SELECT
        self.stroke_color = DEFAULT_STROKE
        self.stroke_width = 3
        self.fill_color = "#27272a"
        self.sticky_color = "#fef9c3"
        self.font_size = 18
        self.show_grid = True
        self.snap_to_grid = False
        self.read_only = False
        self.presentation_follow = True
        self.self_participant: dict[str, Any] = {}
        self._items: dict[str, BoardItem] = {}
        self._connector_source: str | None = None
        self._active_stroke: dict[str, Any] | None = None
        self._stroke_origin = QPointF()
        self._stroke_last_sent = 0
        self._stroke_pending: list[list[float]] = []
        self._stroke_last_flush = 0.0
        self._shape_start: QPointF | None = None
        self._shape_current: QPointF | None = None
        self._preview: QGraphicsPathItem | None = None
        self._erasing = False
        self._erase_ids: set[str] = set()
        self._lasering = False
        self._middle_panning = False
        self._pan_last = QPointF()
        self._space_panning = False
        self._zoom = 1.0
        self.grid_size = GRID_SIZE
        self._last_cursor_emit = 0.0
        self._alignment_guides: list[tuple[QPointF, QPointF]] = []
        self._remote_cursors: dict[str, dict[str, Any]] = {}
        self._laser_trails: dict[str, list[tuple[float, QPointF, QColor, str]]] = {}
        self._presentation_viewport: dict[str, float] | None = None
        self._is_host_presenting = False
        self._last_viewport_emit = 0.0
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(60)
        self._cursor_timer.timeout.connect(lambda: self.viewport().update())
        self._cursor_timer.start()

        model.object_added.connect(self._on_object_added)
        model.object_updated.connect(self._on_object_updated)
        model.object_removed.connect(self._on_object_removed)
        model.canvas_reset.connect(self._rebuild)
        self.scene().selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------
    # Public canvas controls
    # ------------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self.tool = tool
        if tool == TOOL_PAN:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif tool in (TOOL_SELECT,):
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        self._connector_source = None
        self._cancel_shape()

    def set_style(
        self,
        *,
        stroke: str | None = None,
        width: int | None = None,
        fill: str | None = None,
        sticky: str | None = None,
        font_size: int | None = None,
    ) -> None:
        if stroke:
            self.stroke_color = stroke
        if width:
            self.stroke_width = max(1, int(width))
        if fill:
            self.fill_color = fill
        if sticky:
            self.sticky_color = sticky
        if font_size:
            self.font_size = max(6, int(font_size))

    def set_grid(self, visible: bool) -> None:
        self.show_grid = bool(visible)
        self.viewport().update()

    def set_snap(self, enabled: bool) -> None:
        self.snap_to_grid = bool(enabled)

    def set_self_participant(self, participant: dict[str, Any]) -> None:
        self.self_participant = participant or {}

    def set_participants(self, participants: list[dict[str, Any]]) -> None:
        for participant in participants:
            pid = participant.get("id")
            if not pid:
                continue
            existing = self._remote_cursors.get(pid, {})
            existing.update(participant)
            self._remote_cursors[pid] = existing
        online = {p.get("id") for p in participants}
        for pid in list(self._remote_cursors):
            if pid not in online:
                self._remote_cursors.pop(pid, None)
        self.viewport().update()

    def update_remote_cursor(self, payload: dict[str, Any]) -> None:
        pid = payload.get("participant_id")
        cursor = payload.get("cursor")
        if not pid or not isinstance(cursor, dict):
            return
        self._remote_cursors[pid] = {
            "id": pid,
            "name": payload.get("name", "Guest"),
            "color": payload.get("color", "#a1a1aa"),
            "cursor": cursor,
            "tool": payload.get("tool", "select"),
            "last_cursor_at": time.monotonic(),
            "typing": payload.get("typing", False),
        }
        if self.self_participant and pid == self.self_participant.get("id"):
            return
        self.viewport().update()

    def add_laser_point(self, payload: dict[str, Any]) -> None:
        pid = payload.get("participant_id", "remote")
        try:
            point = QPointF(float(payload.get("x", 0)), float(payload.get("y", 0)))
        except (TypeError, ValueError):
            return
        color = QColor(payload.get("color", "#ef4444"))
        trail = self._laser_trails.setdefault(pid, [])
        trail.append((time.monotonic(), point, color, payload.get("name", "")))
        if len(trail) > 80:
            del trail[:-80]
        self.viewport().update()

    def set_hidden(self) -> None:
        """Clear remote/laser overlays when leaving a board."""
        self._remote_cursors.clear()
        self._laser_trails.clear()
        self._items.clear()
        self.scene().clear()

    # ------------------------------------------------------------------
    # Zoom / viewport
    # ------------------------------------------------------------------
    def zoom_in(self) -> None:
        self._apply_zoom(self._zoom * 1.18)

    def zoom_out(self) -> None:
        self._apply_zoom(self._zoom / 1.18)

    def reset_zoom(self) -> None:
        self._apply_zoom(1.0)

    def zoom_percent(self) -> int:
        return int(round(self._zoom * 100))

    def _apply_zoom(self, target: float) -> None:
        target = max(MIN_ZOOM, min(MAX_ZOOM, target))
        if abs(target - self._zoom) < 0.0001:
            return
        factor = target / self._zoom
        self.scale(factor, factor)
        self._zoom = target
        self.zoomChanged.emit(self._zoom)
        if self._is_host_presenting:
            self._emit_viewport(force=True)

    def fit_content(self) -> None:
        items = [item for item in self._items.values() if item.isVisible()]
        if not items:
            self.reset_zoom()
            self.centerOn(0, 0)
            return
        rect = QRectF()
        for item in items:
            rect = item.sceneBoundingRect() if rect.isNull() else rect.united(item.sceneBoundingRect())
        if rect.width() < 10 or rect.height() < 10:
            rect = rect.adjusted(-200, -150, 200, 150)
        self.fitInView(rect.adjusted(-80, -80, 80, 80), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.transform().m11()))
        self.zoomChanged.emit(self._zoom)
        if self._is_host_presenting:
            self._emit_viewport(force=True)

    def camera_viewport(self) -> dict[str, float]:
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return {"x": rect.x(), "y": rect.y(), "w": rect.width(), "h": rect.height()}

    def apply_camera_viewport(self, viewport: dict[str, Any]) -> None:
        if not isinstance(viewport, dict):
            return
        try:
            w = max(20.0, float(viewport.get("w", 1200)))
            h = max(20.0, float(viewport.get("h", 800)))
            x = float(viewport.get("x", 0))
            y = float(viewport.get("y", 0))
        except (TypeError, ValueError):
            return
        self._presentation_viewport = {"x": x, "y": y, "w": w, "h": h}
        view_w = max(1, self.viewport().width())
        view_h = max(1, self.viewport().height())
        scale = min(view_w / w, view_h / h)
        scale = max(MIN_ZOOM, min(MAX_ZOOM, scale))
        self.resetTransform()
        self.scale(scale, scale)
        self._zoom = scale
        self.centerOn(x + w / 2, y + h / 2)
        self.zoomChanged.emit(self._zoom)

    def set_host_presenting(self, active: bool) -> None:
        self._is_host_presenting = active
        if active:
            self._emit_viewport(force=True)

    def _emit_viewport(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_viewport_emit < 0.15:
            return
        self._last_viewport_emit = now
        self.viewportChanged.emit(self.camera_viewport())

    # ------------------------------------------------------------------
    # Item syncing
    # ------------------------------------------------------------------
    def _on_object_added(self, obj: dict[str, Any]) -> None:
        if obj.get("id") in self._items:
            self._on_object_updated(obj)
            return
        item = make_item(obj, self)
        self.scene().addItem(item)
        self._items[obj["id"]] = item
        item.geometryCommitted.connect(self._on_geometry_committed)
        item.editRequested.connect(self._on_item_edit_requested)
        self._refresh_connectors()
        if self.read_only:
            item.setFlag(BoardItem.GraphicsItemFlag.ItemIsMovable, False)
            item.setFlag(BoardItem.GraphicsItemFlag.ItemIsSelectable, True)

    def _on_object_updated(self, obj: dict[str, Any]) -> None:
        item = self._items.get(obj.get("id"))
        if item is None:
            self._on_object_added(obj)
            return
        item.apply_object(obj)
        self._refresh_connectors()
        self._sync_item_flags(item)

    def _on_object_removed(self, object_id: str) -> None:
        item = self._items.pop(object_id, None)
        if item is not None:
            self.scene().removeItem(item)
            item.deleteLater()
        self._refresh_connectors()

    def _rebuild(self) -> None:
        for item in list(self._items.values()):
            self.scene().removeItem(item)
            item.deleteLater()
        self._items.clear()
        for obj in self.model.objects.values():
            self._on_object_added(obj)
        self._refresh_connectors()

    def item_for_id(self, object_id: str | None) -> BoardItem | None:
        if not object_id:
            return None
        return self._items.get(object_id)

    def _refresh_connectors(self) -> None:
        for item in self._items.values():
            if item.kind == KIND_CONNECTOR:
                item.refresh_connector()

    def _sync_item_flags(self, item: BoardItem) -> None:
        if self.read_only:
            item.setFlag(BoardItem.GraphicsItemFlag.ItemIsMovable, False)

    def set_read_only(self, read_only: bool) -> None:
        self.read_only = bool(read_only)
        for item in self._items.values():
            item.setFlag(BoardItem.GraphicsItemFlag.ItemIsMovable, not read_only)
            if read_only and item.isSelected():
                item.setSelected(False)
        if read_only and self._active_stroke:
            self._finish_stroke()

    def selected_objects(self) -> list[dict[str, Any]]:
        return [
            clone_object(item.obj)
            for item in self.scene().selectedItems()
            if isinstance(item, BoardItem)
        ]

    def _on_selection_changed(self) -> None:
        self.selectionObjectsChanged.emit(self.selected_objects())

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        if self.tool == TOOL_PAN or self._space_panning:
            super().mousePressEvent(event)
            return
        if self.read_only:
            super().mousePressEvent(event)
            return
        if self.tool == TOOL_SELECT and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            clicked = self._item_at(scene_pos)
            group_id = clicked.obj.get("group_id") if clicked is not None else None
            if group_id:
                group_items = [
                    item for item in self._items.values()
                    if item.obj.get("group_id") == group_id and item.kind != KIND_CONNECTOR
                ]
                if group_items and not clicked.isSelected():
                    self.scene().clearSelection()
                    for item in group_items:
                        item.setSelected(True)
        if self.tool in (TOOL_PENCIL, TOOL_BRUSH):
            self._start_stroke(scene_pos)
            event.accept()
            return
        if self.tool == TOOL_ERASER:
            self._erasing = True
            self._erase_ids.clear()
            self._erase_at(scene_pos)
            event.accept()
            return
        if self.tool in SHAPE_TOOLS:
            self._shape_start = scene_pos
            self._shape_current = scene_pos
            self._show_shape_preview()
            event.accept()
            return
        if self.tool == TOOL_CONNECTOR:
            item = self._item_at(scene_pos)
            if item is not None:
                if self._connector_source is None:
                    self._connector_source = item.object_id
                    item.setSelected(True)
                    self.notice.emit("Choose the second object to connect", "info")
                elif item.object_id != self._connector_source:
                    self._create_connector(self._connector_source, item.object_id)
                    self._connector_source = None
                event.accept()
                return
            self._connector_source = None
            event.accept()
            return
        if self.tool == TOOL_TEXT:
            self.textInsertRequested.emit(scene_pos.x(), scene_pos.y())
            event.accept()
            return
        if self.tool == TOOL_STICKY:
            self.stickyInsertRequested.emit(scene_pos.x(), scene_pos.y())
            event.accept()
            return
        if self.tool == TOOL_MINDNODE:
            selected = self.selected_objects()
            parent = selected[0]["id"] if selected else ""
            self.mindNodeInsertRequested.emit(scene_pos.x(), scene_pos.y(), parent)
            event.accept()
            return
        if self.tool == TOOL_IMAGE:
            self.imageInsertRequested.emit(scene_pos.x(), scene_pos.y())
            event.accept()
            return
        if self.tool == TOOL_LASER:
            self._lasering = True
            self.laserMoved.emit(scene_pos.x(), scene_pos.y())
            self.add_laser_point(
                {
                    "participant_id": self.self_participant.get("id", "me"),
                    "name": "You",
                    "color": self.self_participant.get("color", "#ef4444"),
                    "x": scene_pos.x(),
                    "y": scene_pos.y(),
                }
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        now = time.monotonic()
        if now - self._last_cursor_emit > 0.05:
            self._last_cursor_emit = now
            self.cursorMoved.emit(scene_pos.x(), scene_pos.y(), self.tool)
        if self._middle_panning:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        if self._active_stroke:
            self._extend_stroke(scene_pos)
            event.accept()
            return
        if self._erasing:
            self._erase_at(scene_pos)
            event.accept()
            return
        if self._shape_start is not None:
            self._shape_current = scene_pos
            self._show_shape_preview()
            event.accept()
            return
        if self._lasering:
            self.laserMoved.emit(scene_pos.x(), scene_pos.y())
            self.add_laser_point(
                {
                    "participant_id": self.self_participant.get("id", "me"),
                    "name": "You",
                    "color": self.self_participant.get("color", "#ef4444"),
                    "x": scene_pos.x(),
                    "y": scene_pos.y(),
                }
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_panning:
            self._middle_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self._is_host_presenting:
                self._emit_viewport(force=True)
            event.accept()
            return
        if self._active_stroke:
            self._finish_stroke()
            event.accept()
            return
        if self._erasing:
            self._erasing = False
            if self._erase_ids:
                ids = list(self._erase_ids)
                self.model.remove_local_objects(ids)
                self.deleteObjectsRequested.emit(ids)
                self._erase_ids.clear()
            event.accept()
            return
        if self._shape_start is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._finish_shape(scene_pos)
            event.accept()
            return
        if self._lasering:
            self._lasering = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._alignment_guides.clear()
        if self._is_host_presenting:
            self._emit_viewport(force=True)
        self.viewport().update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.0015 ** delta
        target = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        self._apply_zoom(target)
        event.accept()
        if self._is_host_presenting:
            self._emit_viewport()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_panning = True
            self._previous_drag_mode = self.dragMode()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not self.read_only:
                ids = [obj["id"] for obj in self.selected_objects()]
                if ids:
                    self.model.remove_local_objects(ids)
                    self.deleteObjectsRequested.emit(ids)
                    event.accept()
                    return
        if event.key() == Qt.Key.Key_Escape:
            if self._active_stroke:
                self._finish_stroke()
            self._cancel_shape()
            self._connector_source = None
            self._alignment_guides.clear()
            self.viewport().update()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_panning = False
            self.setDragMode(getattr(self, "_previous_drag_mode", QGraphicsView.DragMode.RubberBandDrag))
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self.tool == TOOL_PAN else Qt.CursorShape.ArrowCursor
            )
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # Stroke creation
    # ------------------------------------------------------------------
    def _start_stroke(self, scene_pos: QPointF) -> None:
        origin = scene_pos
        object_id = uuid.uuid4().hex
        width = self.stroke_width * (2.2 if self.tool == TOOL_BRUSH else 1.0)
        obj = {
            "id": object_id,
            "kind": KIND_STROKE,
            "x": round(origin.x(), 2),
            "y": round(origin.y(), 2),
            "w": 1.0,
            "h": 1.0,
            "points": [[0.0, 0.0]],
            "text": "",
            "z": self.model.next_z(),
            "style": {
                "stroke": self.stroke_color,
                "fill": "none",
                "stroke_width": round(width, 2),
                "opacity": 1.0,
                "dashed": False,
                "font_size": self.font_size,
                "text_color": self.stroke_color,
            },
            "owner_id": self.self_participant.get("id", ""),
            "owner_name": self.self_participant.get("name", "You"),
            "rev": 1,
            "created_at": "",
            "updated_at": "",
        }
        self._active_stroke = obj
        self._stroke_origin = origin
        self._stroke_pending = []
        self._stroke_last_flush = time.monotonic()
        self.model.add_local_object(obj)
        self.createObjectRequested.emit(clone_object(obj))

    def _extend_stroke(self, scene_pos: QPointF) -> None:
        if not self._active_stroke:
            return
        relative = [
            round(scene_pos.x() - self._stroke_origin.x(), 2),
            round(scene_pos.y() - self._stroke_origin.y(), 2),
        ]
        points = self._active_stroke.setdefault("points", [])
        if points:
            last = points[-1]
            if abs(last[0] - relative[0]) < 1.2 and abs(last[1] - relative[1]) < 1.2:
                return
        points.append(relative)
        self._stroke_pending.append(relative)
        self.model.append_local_stroke(self._active_stroke["id"], [relative])
        now = time.monotonic()
        if len(self._stroke_pending) >= 5 or now - self._stroke_last_flush > 0.035:
            self._flush_stroke_batch()

    def _flush_stroke_batch(self) -> None:
        if not self._active_stroke or not self._stroke_pending:
            return
        batch = self._stroke_pending
        self._stroke_pending = []
        self._stroke_last_flush = time.monotonic()
        object_id = self._active_stroke["id"]
        self.strokeAppendRequested.emit(object_id, batch)

    def _finish_stroke(self) -> None:
        if not self._active_stroke:
            return
        object_id = self._active_stroke["id"]
        self._flush_stroke_batch()
        self._active_stroke = None
        self._stroke_pending = []
        self.strokeEndRequested.emit(object_id)

    # ------------------------------------------------------------------
    # Shapes
    # ------------------------------------------------------------------
    def _show_shape_preview(self) -> None:
        if self._shape_start is None or self._shape_current is None:
            return
        rect = QRectF(self._shape_start, self._shape_current).normalized()
        kind = SHAPE_TOOLS.get(self.tool, KIND_RECT)
        path = self._shape_path(kind, rect)
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(QColor("#f4f4f5"), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self._preview.setBrush(QBrush(QColor(255, 255, 255, 18)))
            self._preview.setZValue(1_000_000)
            self._preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene().addItem(self._preview)
        self._preview.setPath(path)
        self._preview.show()

    def _shape_path(self, kind: str, rect: QRectF) -> QPainterPath:
        path = QPainterPath()
        if kind in (KIND_LINE, KIND_ARROW):
            path.moveTo(self._shape_start)
            path.lineTo(self._shape_current)
            return path
        if kind == KIND_DIAMOND:
            path.moveTo(rect.center().x(), rect.top())
            path.lineTo(rect.right(), rect.center().y())
            path.lineTo(rect.center().x(), rect.bottom())
            path.lineTo(rect.left(), rect.center().y())
            path.closeSubpath()
        elif kind == KIND_TRIANGLE:
            path.moveTo(rect.center().x(), rect.top())
            path.lineTo(rect.right(), rect.bottom())
            path.lineTo(rect.left(), rect.bottom())
            path.closeSubpath()
        elif kind in (KIND_ELLIPSE, KIND_CIRCLE):
            path.addEllipse(rect)
        elif kind in (KIND_ROUNDED_RECT, KIND_FLOW_TERMINATOR):
            path.addRoundedRect(rect, 18, 18)
        elif kind == KIND_FLOW_DECISION:
            path.moveTo(rect.center().x(), rect.top())
            path.lineTo(rect.right(), rect.center().y())
            path.lineTo(rect.center().x(), rect.bottom())
            path.lineTo(rect.left(), rect.center().y())
            path.closeSubpath()
        elif kind == KIND_FLOW_IO:
            offset = min(24.0, rect.width() * 0.18)
            path.moveTo(rect.left() + offset, rect.top())
            path.lineTo(rect.right(), rect.top())
            path.lineTo(rect.right() - offset, rect.bottom())
            path.lineTo(rect.left(), rect.bottom())
            path.closeSubpath()
        elif kind == KIND_FLOW_DOCUMENT:
            path.addRoundedRect(rect, 6, 6)
        else:
            path.addRoundedRect(rect, 6, 6)
        return path

    def _finish_shape(self, scene_pos: QPointF) -> None:
        if self._shape_start is None:
            return
        end = scene_pos
        start = self._shape_start
        kind = SHAPE_TOOLS.get(self.tool, KIND_RECT)
        if kind == KIND_CIRCLE:
            size = max(abs(end.x() - start.x()), abs(end.y() - start.y()))
            end = QPointF(start.x() + (math.copysign(size, end.x() - start.x()) or size),
                          start.y() + (math.copysign(size, end.y() - start.y()) or size))
        if abs(end.x() - start.x()) < 3 and abs(end.y() - start.y()) < 3:
            end = QPointF(start.x() + 180, start.y() + 100)
        if self.snap_to_grid:
            start = QPointF(round(start.x() / GRID_SIZE) * GRID_SIZE, round(start.y() / GRID_SIZE) * GRID_SIZE)
            end = QPointF(round(end.x() / GRID_SIZE) * GRID_SIZE, round(end.y() / GRID_SIZE) * GRID_SIZE)
        if kind in (KIND_LINE, KIND_ARROW):
            x, y = min(start.x(), end.x()), min(start.y(), end.y())
            w, h = max(abs(end.x() - start.x()), 2.0), max(abs(end.y() - start.y()), 2.0)
            points = [
                [round(start.x() - x, 2), round(start.y() - y, 2)],
                [round(end.x() - x, 2), round(end.y() - y, 2)],
            ]
        else:
            x, y = min(start.x(), end.x()), min(start.y(), end.y())
            w, h = max(abs(end.x() - start.x()), 16.0), max(abs(end.y() - start.y()), 16.0)
            points = []
        obj = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "x": round(x, 2),
            "y": round(y, 2),
            "w": round(w, 2),
            "h": round(h, 2),
            "points": points,
            "text": "",
            "z": self.model.next_z(),
            "style": {
                "stroke": self.stroke_color,
                "fill": self.fill_color,
                "stroke_width": self.stroke_width,
                "opacity": 1.0,
                "dashed": False,
                "font_size": self.font_size,
                "text_color": "#f4f4f5",
            },
            "owner_id": self.self_participant.get("id", ""),
            "owner_name": self.self_participant.get("name", "You"),
            "rev": 1,
            "created_at": "",
            "updated_at": "",
        }
        self._cancel_shape()
        self.model.add_local_object(obj)
        self.createObjectRequested.emit(clone_object(obj))

    def _cancel_shape(self) -> None:
        self._shape_start = None
        self._shape_current = None
        if self._preview is not None:
            # QGraphicsPathItem is a QGraphicsItem, not a QObject, so it has no
            # deleteLater(). Remove it from the scene and drop our reference;
            # Qt/PySide then destroys the wrapper and its C++ item.
            preview = self._preview
            self._preview = None
            self.scene().removeItem(preview)

    # ------------------------------------------------------------------
    # Eraser / connector
    # ------------------------------------------------------------------
    def _item_at(self, scene_pos: QPointF) -> BoardItem | None:
        for item in self.scene().items(scene_pos):
            if isinstance(item, BoardItem):
                if item.kind == KIND_CONNECTOR:
                    continue
                return item
        return None

    def _erase_at(self, scene_pos: QPointF) -> None:
        path = QPainterPath()
        path.addEllipse(scene_pos, max(8.0, self.stroke_width * 2.3), max(8.0, self.stroke_width * 2.3))
        for item in self.scene().items(path):
            if isinstance(item, BoardItem):
                self._erase_ids.add(item.object_id)
                item.setOpacity(0.35)
        self.viewport().update()

    def _create_connector(self, from_id: str, to_id: str) -> None:
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
                "stroke": self.stroke_color,
                "fill": "none",
                "stroke_width": max(2, self.stroke_width),
                "opacity": 1.0,
                "dashed": False,
                "font_size": self.font_size,
                "text_color": self.stroke_color,
            },
            "connector": {"from_id": from_id, "to_id": to_id, "arrow": True, "label": ""},
            "owner_id": self.self_participant.get("id", ""),
            "owner_name": self.self_participant.get("name", "You"),
            "rev": 1,
            "created_at": "",
            "updated_at": "",
        }
        self.model.add_local_object(obj)
        self.createObjectRequested.emit(clone_object(obj))

    def _on_geometry_committed(self, object_id: str, patch: dict[str, Any]) -> None:
        if self.read_only:
            return
        obj = self.model.objects.get(object_id)
        if obj is None:
            return
        base_rev = int(obj.get("rev", 1))
        obj.update({k: v for k, v in patch.items() if k != "rev"})
        obj["rev"] = base_rev + 1
        obj["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.model.changed.emit()
        self.updateObjectRequested.emit(object_id, patch, base_rev)
        self._refresh_connectors()

    def _on_item_edit_requested(self, object_id: str) -> None:
        self.textEditRequested.emit(object_id)

    # ------------------------------------------------------------------
    # Programmatic object creation (text, sticky, mind map, image)
    # ------------------------------------------------------------------
    def create_text_object(self, x: float, y: float, text: str, *, kind: str = KIND_TEXT,
                           w: float = 320, h: float = 80, parent_id: str = "",
                           sticky_color: str | None = None) -> None:
        filled = kind in (KIND_STICKY, KIND_MINDNODE)
        obj = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "x": float(x),
            "y": float(y),
            "w": float(w),
            "h": float(h),
            "points": [],
            "text": text,
            "parent_id": parent_id or None,
            "z": self.model.next_z(),
            "style": {
                "stroke": "#f4f4f5" if not filled else (sticky_color or self.sticky_color),
                "fill": (sticky_color or self.sticky_color) if kind == KIND_STICKY else (
                    self.fill_color if filled else "none"
                ),
                "stroke_width": self.stroke_width,
                "opacity": 1.0,
                "dashed": False,
                "font_size": self.font_size,
                "font_family": "Inter",
                "font_weight": "bold" if kind == KIND_MINDNODE else "normal",
                "text_color": "#18181b" if kind == KIND_STICKY else "#f4f4f5",
            },
            "owner_id": self.self_participant.get("id", ""),
            "owner_name": self.self_participant.get("name", "You"),
            "rev": 1,
            "created_at": "",
            "updated_at": "",
        }
        self.model.add_local_object(obj)
        self.createObjectRequested.emit(clone_object(obj))
        if kind == KIND_MINDNODE and parent_id:
            self._create_connector(parent_id, obj["id"])
        QTimer.singleShot(80, self._refresh_connectors)

    def create_image_object(self, x: float, y: float, data: dict[str, Any], w: float, h: float) -> None:
        obj = {
            "id": uuid.uuid4().hex,
            "kind": "image",
            "x": float(x),
            "y": float(y),
            "w": float(w),
            "h": float(h),
            "points": [],
            "text": "",
            "z": self.model.next_z(),
            "style": {
                "stroke": "#71717a",
                "fill": "none",
                "stroke_width": 1,
                "opacity": 1.0,
                "dashed": False,
                "font_size": 14,
                "text_color": "#f4f4f5",
            },
            "image": data,
            "owner_id": self.self_participant.get("id", ""),
            "owner_name": self.self_participant.get("name", "You"),
            "rev": 1,
            "created_at": "",
            "updated_at": "",
        }
        self.model.add_local_object(obj)
        self.createObjectRequested.emit(clone_object(obj))

    def update_object_text(self, object_id: str, text: str) -> None:
        obj = self.model.objects.get(object_id)
        if obj is None:
            return
        base_rev = int(obj.get("rev", 1))
        if obj.get("kind") == KIND_CONNECTOR:
            connector = dict(obj.get("connector", {}))
            connector["label"] = text
            patch = {"connector": connector}
            obj["connector"] = connector
        else:
            patch = {"text": text}
            obj["text"] = text
        obj["rev"] = base_rev + 1
        self.updateObjectRequested.emit(object_id, patch, base_rev)
        item = self.item_for_id(object_id)
        if item:
            item.apply_object(obj)
            self._refresh_connectors()

    def move_object_to(self, object_id: str, x: float, y: float) -> None:
        obj = self.model.objects.get(object_id)
        if obj is None or self.read_only:
            return
        base_rev = int(obj.get("rev", 1))
        patch = {"x": round(float(x), 2), "y": round(float(y), 2)}
        obj["x"], obj["y"] = patch["x"], patch["y"]
        obj["rev"] = base_rev + 1
        item = self.item_for_id(object_id)
        if item is not None:
            item._suppress_commit = True
            item.setPos(patch["x"], patch["y"])
            item._suppress_commit = False
            item._last_committed = (patch["x"], patch["y"], item.obj.get("w", 100), item.obj.get("h", 100))
        self.updateObjectRequested.emit(object_id, patch, base_rev)
        self._refresh_connectors()

    def update_selected_style(self, patch: dict[str, Any], *, geometry: bool = False) -> None:
        for obj in self.selected_objects():
            base_rev = int(obj.get("rev", 1))
            new_patch = dict(patch)
            if "style" in new_patch:
                style = dict(obj.get("style", {}))
                style.update(new_patch["style"])
                new_patch["style"] = style
            if "x" in new_patch or "y" in new_patch or "w" in new_patch or "h" in new_patch:
                new_patch = {k: v for k, v in new_patch.items() if k in ("x", "y", "w", "h")}
            self.updateObjectRequested.emit(obj["id"], new_patch, base_rev)
            for key, value in new_patch.items():
                if key == "style":
                    obj.setdefault("style", {}).update(value)
                else:
                    obj[key] = value
            obj["rev"] = base_rev + 1
            item = self.item_for_id(obj["id"])
            if item:
                item.apply_object(obj)
        self._refresh_connectors()

    def delete_selected(self) -> None:
        ids = [obj["id"] for obj in self.selected_objects()]
        if ids and not self.read_only:
            self.model.remove_local_objects(ids)
            self.deleteObjectsRequested.emit(ids)

    def duplicate_selected(self) -> None:
        if self.read_only:
            return
        originals = self.selected_objects()
        id_map = {
            obj["id"]: uuid.uuid4().hex
            for obj in originals
            if obj.get("kind") != KIND_CONNECTOR
        }
        copies: list[dict[str, Any]] = []
        for obj in originals:
            copy_obj = clone_object(obj)
            if obj.get("kind") == KIND_CONNECTOR:
                connector = dict(copy_obj.get("connector", {}))
                source = id_map.get(connector.get("from_id"))
                target = id_map.get(connector.get("to_id"))
                if not source or not target:
                    continue
                connector["from_id"] = source
                connector["to_id"] = target
                copy_obj["connector"] = connector
                copy_obj["id"] = uuid.uuid4().hex
            else:
                copy_obj["id"] = id_map[obj["id"]]
            copy_obj["x"] = float(copy_obj.get("x", 0)) + 28
            copy_obj["y"] = float(copy_obj.get("y", 0)) + 28
            copy_obj["z"] = self.model.next_z()
            copy_obj["rev"] = 1
            copy_obj["owner_id"] = self.self_participant.get("id", "")
            copy_obj["owner_name"] = self.self_participant.get("name", "You")
            copy_obj.pop("_local_pending", None)
            copies.append(copy_obj)
        # Create endpoints before connectors.
        copies.sort(key=lambda item: item.get("kind") == KIND_CONNECTOR)
        for copy_obj in copies:
            self.model.add_local_object(copy_obj)
            self.createObjectRequested.emit(copy_obj)

    # ------------------------------------------------------------------
    # Drawing overlays
    # ------------------------------------------------------------------
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#0f0f11"))
        if not self.show_grid:
            return
        zoom = self.transform().m11()
        if zoom < 0.28:
            return
        step = GRID_SIZE
        if zoom < 0.55:
            step *= 2
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        pen = QPen(QColor(255, 255, 255, 14 if zoom > 0.6 else 8), 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x = left
        while x < rect.right():
            painter.drawLine(x, rect.top(), x, rect.bottom())
            x += step
        y = top
        while y < rect.bottom():
            painter.drawLine(rect.left(), y, rect.right(), y)
            y += step
        # major grid
        major = step * 5
        pen = QPen(QColor(255, 255, 255, 24), 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x = math.floor(rect.left() / major) * major
        while x < rect.right():
            painter.drawLine(x, rect.top(), x, rect.bottom())
            x += major
        y = math.floor(rect.top() / major) * major
        while y < rect.bottom():
            painter.drawLine(rect.left(), y, rect.right(), y)
            y += major

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        zoom = max(0.05, self.transform().m11())
        now = time.monotonic()
        # Alignment guides
        if self._alignment_guides:
            pen = QPen(QColor(244, 244, 245, 180), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for start, end in self._alignment_guides:
                painter.drawLine(start, end)
        # Laser trails
        for pid, points in list(self._laser_trails.items()):
            alive = [entry for entry in points if now - entry[0] < 1.1]
            self._laser_trails[pid] = alive
            if not alive:
                continue
            for index in range(1, len(alive)):
                age = now - alive[index][0]
                alpha = int(max(0, 255 * (1 - age / 1.1)))
                color = QColor(alive[index][2])
                color.setAlpha(alpha)
                pen = QPen(color, max(2.0, 6.0 / zoom), Qt.PenStyle.SolidLine)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(alive[index - 1][1], alive[index][1])
            last = alive[-1]
            radius = max(4.0, 8.0 / zoom)
            color = QColor(last[2])
            color.setAlpha(230)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(last[1], radius, radius)
        # Remote cursors
        if not self.show_cursors_enabled():
            return
        for pid, data in self._remote_cursors.items():
            if self.self_participant and pid == self.self_participant.get("id"):
                continue
            cursor = data.get("cursor")
            if not isinstance(cursor, dict):
                continue
            last = data.get("last_cursor_at", 0)
            if last and now - last > 12:
                continue
            try:
                pos = QPointF(float(cursor["x"]), float(cursor["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            color = QColor(data.get("color", "#a1a1aa"))
            name = str(data.get("name", "Guest"))[:24]
            painter.save()
            painter.translate(pos)
            painter.scale(1 / zoom, 1 / zoom)
            painter.setPen(QPen(QColor(15, 15, 17), 1))
            cursor_path = QPainterPath()
            cursor_path.moveTo(0, 0)
            cursor_path.lineTo(0, 18)
            cursor_path.lineTo(4.5, 13.5)
            cursor_path.lineTo(8.5, 21)
            cursor_path.lineTo(11.5, 19.5)
            cursor_path.lineTo(7.5, 12)
            cursor_path.lineTo(13, 12)
            cursor_path.closeSubpath()
            painter.setBrush(QBrush(color))
            painter.drawPath(cursor_path)
            painter.setFont(QFont("Inter", 9))
            metrics = painter.fontMetrics()
            text_width = metrics.horizontalAdvance(name) + 14
            badge = QRectF(12, 16, text_width, 19)
            painter.setPen(Qt.PenStyle.NoPen)
            badge_color = QColor(color)
            badge_color.setAlpha(225)
            painter.setBrush(QBrush(badge_color))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, name)
            typing = data.get("typing")
            if typing:
                painter.setPen(QPen(QColor("#f4f4f5")))
                painter.drawText(QPointF(14, 49), "typing...")
            painter.restore()

    def show_cursors_enabled(self) -> bool:
        return getattr(self, "_show_cursors", True)

    def set_show_cursors(self, enabled: bool) -> None:
        self._show_cursors = bool(enabled)
        self.viewport().update()

    # ------------------------------------------------------------------
    # Alignment
    # ------------------------------------------------------------------
    def update_alignment_guides(self, item: BoardItem) -> None:
        guides: list[tuple[QPointF, QPointF]] = []
        moving = item.sceneBoundingRect()
        moving_points = {
            "left": moving.left(), "center": moving.center().x(), "right": moving.right(),
            "top": moving.top(), "vcenter": moving.center().y(), "bottom": moving.bottom(),
        }
        tolerance = 7.0 / max(0.2, self.transform().m11())
        for other in self._items.values():
            if other is item or other.kind == KIND_CONNECTOR or other.isSelected():
                continue
            rect = other.sceneBoundingRect()
            other_points = {
                "left": rect.left(), "center": rect.center().x(), "right": rect.right(),
                "top": rect.top(), "vcenter": rect.center().y(), "bottom": rect.bottom(),
            }
            for key, value in moving_points.items():
                for other_key, other_value in other_points.items():
                    if abs(value - other_value) < tolerance:
                        if key in ("left", "center", "right") and other_key in ("left", "center", "right"):
                            x = other_value
                            guides.append((QPointF(x, min(moving.top(), rect.top()) - 40), QPointF(x, max(moving.bottom(), rect.bottom()) + 40)))
                        elif key in ("top", "vcenter", "bottom") and other_key in ("top", "vcenter", "bottom"):
                            y = other_value
                            guides.append((QPointF(min(moving.left(), rect.left()) - 40, y), QPointF(max(moving.right(), rect.right()) + 40, y)))
        # De-duplicate
        unique: list[tuple[QPointF, QPointF]] = []
        for guide in guides:
            if not any(
                abs(guide[0].x() - existing[0].x()) < 0.1
                and abs(guide[0].y() - existing[0].y()) < 0.1
                and abs(guide[1].x() - existing[1].x()) < 0.1
                and abs(guide[1].y() - existing[1].y()) < 0.1
                for existing in unique
            ):
                unique.append(guide)
        self._alignment_guides = unique[:8]
        self.viewport().update()

    def commit_selected_moves(self) -> None:
        if self.read_only:
            return
        for item in self.scene().selectedItems():
            if isinstance(item, BoardItem) and item.kind != KIND_CONNECTOR:
                item.commit_move_if_needed()
        self._alignment_guides.clear()
        self.viewport().update()

    def refresh_style(self) -> None:
        for item in self._items.values():
            item.update()
