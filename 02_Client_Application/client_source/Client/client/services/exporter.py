"""PNG / PDF / JSON export and safe JSON import."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter

from client.canvas.items import BoardItem
from shared.constants import APP_NAME, APP_VERSION


class ExportError(Exception):
    pass


def board_bounds(scene, padding: float = 70.0) -> QRectF:
    items = [item for item in scene.items() if isinstance(item, BoardItem)]
    if not items:
        return QRectF(-400, -300, 800, 600)
    rect = QRectF()
    for item in items:
        rect = item.sceneBoundingRect() if rect.isNull() else rect.united(item.sceneBoundingRect())
    rect = rect.adjusted(-padding, -padding, padding, padding)
    if rect.width() < 100 or rect.height() < 100:
        rect = rect.adjusted(-200, -150, 200, 150)
    return rect


def render_board_image(
    scene,
    *,
    background: str = "dark",
    scale: float = 1.0,
    max_pixels: int = 40_000_000,
) -> QImage:
    bounds = board_bounds(scene)
    width = max(1, int(bounds.width() * max(0.1, scale)))
    height = max(1, int(bounds.height() * max(0.1, scale)))
    if width * height > max_pixels:
        factor = (max_pixels / float(width * height)) ** 0.5
        width = max(1, int(width * factor))
        height = max(1, int(height * factor))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if background == "transparent":
        image.fill(Qt.GlobalColor.transparent)
    elif background == "light":
        image.fill(QColor("#ffffff"))
    else:
        image.fill(QColor("#0f0f11"))
    selected = list(scene.selectedItems())
    for item in selected:
        item.setSelected(False)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(painter, QRectF(0, 0, width, height), bounds)
    painter.end()
    for item in selected:
        item.setSelected(True)
    return image


def export_png(scene, path: str | Path, *, background: str = "dark", scale: float = 1.0) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    image = render_board_image(scene, background=background, scale=scale)
    if not image.save(str(path), "PNG"):
        raise ExportError(f"Could not write {path}")
    return path


def export_pdf(
    scene,
    path: str | Path,
    *,
    background: str = "dark",
    title: str = "SynCanvas board",
) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    bounds = board_bounds(scene)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    printer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    printer.setCreator(APP_NAME)
    printer.setDocName(title)
    painter = QPainter()
    if not painter.begin(printer):
        raise ExportError("Could not initialise the PDF printer")
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    page = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
    if background == "transparent":
        painter.fillRect(page, QColor("#0f0f11"))
    elif background == "light":
        painter.fillRect(page, QColor("#ffffff"))
    else:
        painter.fillRect(page, QColor("#0f0f11"))
    margin = 18
    target = page.adjusted(margin, margin, -margin, -margin)
    aspect = bounds.width() / max(1.0, bounds.height())
    if target.width() / max(1.0, target.height()) > aspect:
        height = target.height()
        width = height * aspect
        target = QRectF(target.center().x() - width / 2, target.top(), width, height)
    else:
        width = target.width()
        height = width / aspect
        target = QRectF(target.left(), target.center().y() - height / 2, width, height)
    selected = list(scene.selectedItems())
    for item in selected:
        item.setSelected(False)
    scene.render(painter, target, bounds)
    for item in selected:
        item.setSelected(True)
    painter.end()
    return path


def export_json(path: str | Path, snapshot: dict[str, Any], *, meta: dict[str, Any] | None = None) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    payload = {
        "format": "syncanvas-board",
        "version": 1,
        "application": APP_NAME,
        "application_version": APP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta or {},
        "objects": snapshot.get("objects", []),
    }
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def import_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExportError(f"Could not read {path}: {exc}") from exc
    if len(text.encode("utf-8")) > 60 * 1024 * 1024:
        raise ExportError("Board file is too large")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExportError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExportError("Board file must contain a JSON object")
    objects = data.get("objects")
    if not isinstance(objects, list):
        raise ExportError("Board file has no 'objects' array")
    if len(objects) > 5000:
        raise ExportError("Board file contains too many objects")
    clean = [obj for obj in objects if isinstance(obj, dict) and isinstance(obj.get("kind"), str)]
    if not clean:
        raise ExportError("Board file contains no valid objects")
    return {
        "objects": clean,
        "meta": data.get("meta", {}),
        "application": data.get("application"),
        "exported_at": data.get("exported_at"),
    }


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".syncanvas-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            try:
                os.unlink(temp)
            except OSError:
                pass
