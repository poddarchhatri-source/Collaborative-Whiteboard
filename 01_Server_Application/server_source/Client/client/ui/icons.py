"""Crisp vector icons rendered from inline SVG (no external icon package)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Stroke-style icon bodies on a 24x24 viewbox. Keeping the paths in code makes
# packaging simple and guarantees consistent sizing/colour across platforms.
_PATHS: dict[str, str] = {
    "select": '<path d="M5 3l6.5 16 2.5-6.3 6.5-2.4z"/>',
    "hand": '<path d="M8 13V5.5a1.5 1.5 0 013 0V11"/><path d="M11 10V4.5a1.5 1.5 0 013 0V10"/><path d="M14 10V6a1.5 1.5 0 013 0v7"/><path d="M17 9.5a1.5 1.5 0 013 0V15a6 6 0 01-6 6h-1.5a5.5 5.5 0 01-4.3-2l-3-4a1.6 1.6 0 012.5-2l2.3 2.1"/>',
    "pencil": '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4z"/>',
    "brush": '<path d="M9.1 11.9l8.1-8.1a2.9 2.9 0 014.1 4.1l-8.1 8.1"/><path d="M9.1 11.9L4 20c2 .3 4.5-.3 6-1.8 1.7-1.7 1.7-4.4-.9-6.3z"/>',
    "eraser": '<path d="M7 21l-4-4a2 2 0 010-2.8l9-9a2 2 0 012.8 0l4.2 4.2a2 2 0 010 2.8L13 19"/><path d="M22 21H7"/><path d="M5 13l6 6"/>',
    "text": '<path d="M4 6V4h16v2"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    "line": '<path d="M5 19L19 5"/><circle cx="5" cy="19" r="1.3"/><circle cx="19" cy="5" r="1.3"/>',
    "square": '<rect x="4" y="4" width="16" height="16" rx="2"/>',
    "rounded": '<rect x="3" y="4" width="18" height="16" rx="6"/>',
    "circle": '<circle cx="12" cy="12" r="8.5"/>',
    "diamond": '<path d="M12 3l9 9-9 9-9-9z"/>',
    "triangle": '<path d="M12 3l9 17H3z"/>',
    "arrow": '<path d="M5 19L19 5"/><path d="M11 5h8v8"/>',
    "connector": '<circle cx="5" cy="12" r="2.5"/><circle cx="19" cy="12" r="2.5"/><path d="M7.5 12h9"/><path d="M13 8.5l4 3.5-4 3.5"/>',
    "flow": '<rect x="8" y="3" width="8" height="5" rx="2.5"/><rect x="3" y="16" width="8" height="5" rx="1"/><path d="M12 8v4"/><path d="M12 12H7v4"/><path d="M12 12h5v4"/>',
    "sticky": '<path d="M4 4h16v10l-6 6H4z"/><path d="M20 14h-6v6"/>',
    "mindmap": '<circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="7" r="2"/><circle cx="18" cy="18" r="2"/><path d="M9 10L6.5 7.5"/><path d="M15 10l2.5-2"/><path d="M14 14l2.5 3"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.7"/><path d="M21 16l-5-5-6 6-2-2-5 5"/>',
    "laser": '<path d="M12 3v3"/><path d="M5.6 5.6l2.1 2.1"/><path d="M3 12h3"/><path d="M18.4 5.6l-2.1 2.1"/><path d="M21 12h-3"/><circle cx="12" cy="13" r="3"/><path d="M12 16v5"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 12l9 5 9-5"/><path d="M3 16l9 5 9-5"/>',
    "fit": '<path d="M8 3H5a2 2 0 00-2 2v3"/><path d="M16 3h3a2 2 0 012 2v3"/><path d="M8 21H5a2 2 0 01-2-2v-3"/><path d="M16 21h3a2 2 0 002-2v-3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "history": '<path d="M3 12a9 9 0 109-9 9.7 9.7 0 00-6.4 2.6L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    "network": '<rect x="2" y="14" width="6" height="6" rx="1"/><rect x="16" y="14" width="6" height="6" rx="1"/><rect x="9" y="3" width="6" height="6" rx="1"/><path d="M12 9v3"/><path d="M12 12H5v2"/><path d="M12 12h7v2"/>',
    "present": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v5"/><path d="M8 21h8"/><path d="M9 9l3-2 3 4 2-2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.6v.2h-4v-.2a1.7 1.7 0 00-1.1-1.6 1.7 1.7 0 00-1.9.3l-.1.1L4.1 17l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.6-1H2.7v-4h.2a1.7 1.7 0 001.6-1.1 1.7 1.7 0 00-.3-1.9L4.1 7 6.9 4.2l.1.1a1.7 1.7 0 001.9.3A1.7 1.7 0 0010 3V2.8h4V3a1.7 1.7 0 001 1.6 1.7 1.7 0 001.9-.3l.1-.1L19.9 7l-.1.1a1.7 1.7 0 00-.3 1.9 1.7 1.7 0 001.6 1h.2v4h-.2a1.7 1.7 0 00-1.6 1z"/>',
    "lock": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 018 0v3"/>',
    "unlock": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 017.5-2"/>',
    "export": '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v3h16v-3"/>',
    "import": '<path d="M12 15V3"/><path d="M7 8l5 5 5-5"/><path d="M4 17v3h16v-3"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 21a6.5 6.5 0 0113 0"/><path d="M16 5a3 3 0 010 6"/><path d="M18 14a5 5 0 013.5 7"/>',
    "chat": '<path d="M21 12a8.5 8.5 0 01-12.4 7.5L3 21l1.5-5.2A8.5 8.5 0 1121 12z"/><path d="M8 11h8M8 14h5"/>',
    "activity": '<path d="M3 12h4l2.5-7 5 14 2.5-7h4"/>',
    "sliders": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "send": '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/>',
    "save": '<path d="M4 3h13l3 3v15H4z"/><path d="M8 3v6h8V3"/><path d="M8 21v-7h8v7"/>',
    "template": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M9 9v11"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 15H6L5 6"/><path d="M10 11v6M14 11v6"/>',
    "duplicate": '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 00-2-2H5a2 2 0 00-2 2v9a2 2 0 002 2h3"/>',
    "group": '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/><path d="M11 7h5a2 2 0 012 2v4"/>',
    "backward": '<path d="M12 5l-7 7 7 7"/><path d="M19 5l-7 7 7 7"/>',
    "forward": '<path d="M12 5l7 7-7 7"/><path d="M5 5l7 7-7 7"/>',
    "check": '<path d="M4 12l5 5L20 6"/>',
    "warning": '<path d="M12 3l10 18H2z"/><path d="M12 9v5M12 18h.01"/>',
    "wifi": '<path d="M2 8.5a15 15 0 0120 0"/><path d="M5 12a10.5 10.5 0 0114 0"/><path d="M8.5 15.5a6 6 0 017 0"/><circle cx="12" cy="19" r="1"/>',
    "logout": '<path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
    "minimap": '<rect x="3" y="4" width="18" height="16" rx="2"/><rect x="6" y="7" width="7" height="5" rx="1"/><circle cx="16.5" cy="15.5" r="2.5"/>',
    "command": '<path d="M9 6a3 3 0 10-3 3h12a3 3 0 10-3-3v12a3 3 0 103-3H6a3 3 0 103 3z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "chevron_down": '<path d="M6 9l6 6 6-6"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "server": '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/><path d="M12 7h5M12 17h5"/>',
}


@lru_cache(maxsize=512)
def _render(name: str, color: str, size: int) -> QPixmap:
    body = _PATHS.get(name, _PATHS["square"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">'
        f'<g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</g></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon(name: str, color: str = "#d4d4d8", size: int = 22) -> QIcon:
    return QIcon(_render(name, color, size))


def pixmap(name: str, color: str = "#d4d4d8", size: int = 22) -> QPixmap:
    return _render(name, color, size)


def logo_pixmap(size: int = 96) -> QPixmap:
    """Brand mark: a collaborative canvas with three monochrome cursors."""
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 96 96">
      <rect x="3" y="3" width="90" height="90" rx="26" fill="#18181b" stroke="#2e2e33" stroke-width="2"/>
      <rect x="18" y="22" width="60" height="46" rx="11" fill="#0d0d0f" stroke="#27272a" stroke-width="1.5"/>
      <path d="M28 54c6-12 12 6 18-5s10 9 17-3" fill="none" stroke="#f4f4f5" stroke-width="4" stroke-linecap="round"/>
      <circle cx="31" cy="35" r="4.2" fill="#f4f4f5"/>
      <path d="M54 31l12 6-5.4 1.7 3.2 5.3-3.4 2-3.2-5.3-3.2 4.3z" fill="#a1a1aa"/>
      <path d="M63 51l10 4-4.5 1.4 2.6 4.4-2.8 1.6-2.6-4.4-2.7 3.6z" fill="#71717a"/>
      <circle cx="35" cy="64" r="2.4" fill="#52525b"/>
      <circle cx="45" cy="64" r="2.4" fill="#71717a"/>
      <circle cx="55" cy="64" r="2.4" fill="#a1a1aa"/>
    </svg>
    """
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pm


def app_icon() -> QIcon:
    icon_obj = QIcon()
    for size in (16, 32, 64, 128, 256):
        icon_obj.addPixmap(logo_pixmap(size))
    return icon_obj
