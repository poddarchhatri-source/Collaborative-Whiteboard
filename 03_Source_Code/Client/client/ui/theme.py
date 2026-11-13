"""Application-wide visual theme and typography."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
CHECK_ICON = (RESOURCE_ROOT / "assets" / "check.svg").as_posix()

THEMES = {
    "midnight": {
        "bg": "#11151b",
        "bg_alt": "#171d25",
        "panel": "#1b222c",
        "panel_alt": "#222c39",
        "field": "#151c25",
        "border": "#303c4b",
        "border_soft": "#27313e",
        "text": "#e8edf3",
        "muted": "#aab6c5",
        "faint": "#8190a2",
        "accent": "#668fc2",
        "accent_hover": "#7ba3d4",
        "accent_soft": "#293b52",
        "accent_text": "#ffffff",
        "checkbox_checked": "#668fc2",
        "slider_handle": "#8eb1dc",
        "cyan": "#8eb1dc",
        "success": "#22c55e",
        "danger": "#ef4444",
        "warning": "#f59e0b",
        "selection": "#293b52",
    },
    "cloud": {
        "bg": "#fcfcfc",
        "bg_alt": "#f4f4f5",
        "panel": "#ffffff",
        "panel_alt": "#f4f4f5",
        "field": "#f4f4f5",
        "border": "#e4e4e7",
        "border_soft": "#ececee",
        "text": "#09090b",
        "muted": "#71717a",
        "faint": "#a1a1aa",
        "accent": "#426f9f",
        "accent_hover": "#345d87",
        "accent_soft": "#e7eff8",
        "accent_text": "#ffffff",
        "checkbox_checked": "#426f9f",
        "slider_handle": "#426f9f",
        "cyan": "#426f9f",
        "success": "#16a34a",
        "danger": "#dc2626",
        "warning": "#d97706",
        "selection": "#e4e4e7",
    },
}


def font_family() -> str:
    families = set(QFontDatabase.families())
    for candidate in ("Inter", "SF Pro Text", "Segoe UI Variable", "Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans"):
        if candidate in families:
            return candidate
    return QApplication.font().family()


def build_stylesheet(theme_name: str = "midnight") -> str:
    t = THEMES.get(theme_name, THEMES["midnight"])
    family = font_family()
    return f"""
    * {{
        font-family: "{family}", "Segoe UI", "Ubuntu", "DejaVu Sans";
        font-size: 13px;
        color: {t['text']};
        outline: none;
    }}
    QMainWindow, QDialog, QWidget#Root {{
        background: {t['bg']};
    }}
    QWidget#Panel, QFrame[role="panel"] {{
        background: {t['panel']};
        border: 1px solid {t['border_soft']};
        border-radius: 12px;
    }}
    QFrame[role="card"] {{
        background: {t['panel']};
        border: 1px solid {t['border']};
        border-radius: 16px;
    }}
    QFrame[role="softCard"] {{
        background: {t['panel_alt']};
        border: 1px solid {t['border_soft']};
        border-radius: 12px;
    }}
    QFrame#TopBar {{
        background: {t['panel']};
        border-bottom: 1px solid {t['border_soft']};
    }}
    QFrame#LeftToolbar {{
        background: {t['panel']};
        border-right: 1px solid {t['border_soft']};
    }}
    QFrame#RightSidebar {{
        background: {t['panel']};
        border-left: 1px solid {t['border_soft']};
    }}
    QFrame#BottomBar {{
        background: {t['panel']};
        border-top: 1px solid {t['border_soft']};
    }}
    QLabel {{
        background: transparent;
    }}
    QLabel[role="title"] {{
        font-size: 24px;
        font-weight: 700;
        color: {t['text']};
    }}
    QLabel[role="h1"] {{
        font-size: 30px;
        font-weight: 800;
    }}
    QLabel[role="h2"] {{
        font-size: 18px;
        font-weight: 700;
    }}
    QLabel[role="section"] {{
        font-size: 11px;
        font-weight: 700;
        color: {t['faint']};
        letter-spacing: 1px;
    }}
    QLabel[role="muted"] {{
        color: {t['muted']};
    }}
    QLabel[role="faint"] {{
        color: {t['faint']};
        font-size: 12px;
    }}
    QLabel[role="fieldLabel"] {{
        color: {t['muted']};
        font-size: 12px;
        font-weight: 600;
        padding: 0px;
        margin: 0px;
    }}
    QLabel[role="brand"] {{
        font-size: 19px;
        font-weight: 800;
        letter-spacing: 0.2px;
    }}
    QLabel[role="code"] {{
        font-family: "JetBrains Mono", "SF Mono", "Cascadia Code", monospace;
        font-size: 12px;
        color: {t['cyan']};
    }}
    QLabel[role="badge"] {{
        background: {t['accent_soft']};
        color: {t['text']};
        border-radius: 9px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel[role="successBadge"] {{
        background: rgba(34,197,94,0.16);
        color: {t['success']};
        border-radius: 9px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel[role="errorBadge"] {{
        background: rgba(244,63,94,0.16);
        color: {t['danger']};
        border-radius: 9px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel[role="warningBadge"] {{
        background: rgba(245,158,11,0.16);
        color: {t['warning']};
        border-radius: 9px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 700;
    }}
    QPushButton {{
        background: {t['panel_alt']};
        border: 1px solid {t['border']};
        border-radius: 9px;
        padding: 8px 14px;
        font-weight: 600;
        color: {t['text']};
    }}
    QPushButton:hover {{
        background: {t['field']};
        border-color: {t['accent']};
    }}
    QPushButton:pressed {{
        background: {t['accent_soft']};
    }}
    QPushButton:disabled {{
        color: {t['faint']};
        background: {t['bg_alt']};
        border-color: {t['border_soft']};
    }}
    QPushButton#primaryButton {{
        background: {t['accent']};
        border: 1px solid {t['accent']};
        color: {t.get('accent_text', '#09090b')};
        padding: 10px 18px;
        font-weight: 700;
    }}
    QPushButton#primaryButton:hover {{
        background: {t['accent_hover']};
    }}
    QPushButton#primaryButton:disabled {{
        background: {t['border']};
        border-color: {t['border']};
        color: {t['faint']};
    }}
    QPushButton#dangerButton {{
        background: rgba(244,63,94,0.12);
        border: 1px solid rgba(244,63,94,0.38);
        color: {t['danger']};
    }}
    QPushButton#dangerButton:hover {{
        background: rgba(244,63,94,0.22);
    }}
    QPushButton#dangerButton:disabled {{
        background: {t['bg_alt']};
        border-color: {t['border_soft']};
        color: {t['faint']};
    }}
    QPushButton#ghostButton, QPushButton[role="ghost"] {{
        background: transparent;
        border: 1px solid transparent;
        color: {t['muted']};
    }}
    QPushButton#ghostButton:hover, QPushButton[role="ghost"]:hover {{
        background: {t['panel_alt']};
        color: {t['text']};
        border-color: {t['border_soft']};
    }}
    QPushButton#linkButton {{
        background: transparent;
        border: none;
        color: {t['cyan']};
        padding: 2px;
        font-weight: 600;
    }}
    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        padding: 7px;
        color: {t['muted']};
    }}
    QToolButton:hover {{
        background: {t['panel_alt']};
        border-color: {t['border_soft']};
        color: {t['text']};
    }}
    QToolButton:checked {{
        background: {t['accent_soft']};
        border-color: {t['accent']};
    }}
    QToolButton:pressed {{
        background: {t['accent']};
    }}
    QToolButton#dangerTool:hover {{
        background: rgba(244,63,94,0.16);
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {t['field']};
        border: 1px solid {t['border']};
        border-radius: 9px;
        padding: 0px 12px;
        color: {t['text']};
        font-size: 13px;
        min-height: 42px;
        selection-background-color: {t['accent']};
        selection-color: {t['accent_text']};
    }}
    QSpinBox, QDoubleSpinBox, QComboBox {{
        max-height: 42px;
    }}
    QPlainTextEdit {{
        background: {t['field']};
        border: 1px solid {t['border']};
        border-radius: 9px;
        padding: 10px 12px;
        color: {t['text']};
        font-size: 13px;
        selection-background-color: {t['accent']};
        selection-color: {t['accent_text']};
    }}
    QTextEdit {{
        background: {t['field']};
        border: 1px solid {t['border']};
        border-radius: 9px;
        padding: 10px 12px;
        color: {t['text']};
        font-size: 13px;
        selection-background-color: {t['accent']};
        selection-color: {t['accent_text']};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border-color: {t['accent']};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        color: {t['faint']};
        background: {t['bg_alt']};
        border-color: {t['border_soft']};
    }}
    QLineEdit[role="search"] {{
        padding-left: 12px;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {t['panel']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 4px;
        selection-background-color: {t['accent_soft']};
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        width: 16px;
        border: none;
        background: transparent;
    }}
    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {t['muted']};
        padding: 9px 14px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        color: {t['text']};
        border-bottom: 2px solid {t['accent']};
    }}
    QTabBar::tab:hover {{
        color: {t['text']};
    }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: transparent;
        border: none;
        outline: none;
        alternate-background-color: {t['panel_alt']};
    }}
    QListWidget::item {{
        border-radius: 8px;
        padding: 3px;
        margin: 1px 0;
    }}
    QListWidget::item:selected {{
        background: {t['accent_soft']};
        color: {t['text']};
    }}
    QListWidget::item:hover {{
        background: {t['panel_alt']};
    }}
    QTreeWidget::item {{
        padding: 5px;
        border-radius: 6px;
    }}
    QTreeWidget::item:selected {{
        background: {t['accent_soft']};
    }}
    QHeaderView::section {{
        background: {t['panel_alt']};
        color: {t['muted']};
        border: none;
        border-bottom: 1px solid {t['border']};
        padding: 7px;
        font-weight: 700;
        font-size: 11px;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 9px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t['border']};
        min-height: 28px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t['faint']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 9px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t['border']};
        min-width: 28px;
        border-radius: 4px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QToolTip {{
        background: {t['panel']};
        color: {t['text']};
        border: 1px solid {t['border']};
        border-radius: 6px;
        padding: 6px 8px;
    }}
    QMenu {{
        background: {t['panel']};
        border: 1px solid {t['border']};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 26px 7px 10px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background: {t['accent_soft']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t['border_soft']};
        margin: 5px 8px;
    }}
    QCheckBox {{
        spacing: 8px;
        color: {t['text']};
    }}
    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border-radius: 5px;
        border: 1px solid {t['border']};
        background: {t['field']};
    }}
    QCheckBox::indicator:checked {{
        background: {t.get('checkbox_checked', t['accent_soft'])};
        border-color: {t['border']};
        image: url("{CHECK_ICON}");
    }}
    QSlider::groove:horizontal {{
        height: 5px;
        background: {t['border']};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 16px;
        height: 16px;
        margin: -6px 0;
        border-radius: 8px;
        background: {t.get('slider_handle', t['text'])};
    }}
    QSlider::sub-page:horizontal {{
        background: {t['accent_soft']};
        border-radius: 2px;
    }}
    QProgressBar {{
        border: none;
        background: {t['field']};
        border-radius: 4px;
        height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {t['text']};
        border-radius: 4px;
    }}
    QSplitter::handle {{
        background: {t['border_soft']};
        width: 1px;
    }}
    QDialogButtonBox QPushButton {{
        min-width: 84px;
    }}
    QGroupBox {{
        border: 1px solid {t['border_soft']};
        border-radius: 10px;
        margin-top: 14px;
        padding-top: 10px;
        font-weight: 700;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 5px;
        color: {t['muted']};
    }}
    QTableWidget {{
        background: {t['panel']};
        border: 1px solid {t['border_soft']};
        border-radius: 8px;
        gridline-color: {t['border_soft']};
    }}
    QTableWidget::item {{
        padding: 6px;
    }}
    QTableWidget::item:selected {{
        background: {t['accent_soft']};
    }}
    QTextBrowser {{
        background: transparent;
        border: none;
    }}
    QStatusBar {{
        background: {t['panel']};
        color: {t['muted']};
    }}
    """


def apply_theme(app: QApplication, theme_name: str = "midnight") -> None:
    theme = THEMES.get(theme_name, THEMES["midnight"])
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme["field"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme["panel_alt"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme["panel_alt"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme["selection"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme["text"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme["panel"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme["text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme["faint"]))
    app.setPalette(palette)
    font = QFont(font_family(), 10)
    app.setFont(font)
    app.setStyleSheet(build_stylesheet(theme_name))


def theme_colors(name: str = "midnight") -> dict[str, str]:
    return THEMES.get(name, THEMES["midnight"])
