"""SynCanvas desktop client entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from client.config import SettingsStore, default_log_dir  # noqa: E402
from client.ui.icons import app_icon  # noqa: E402
from client.ui.main_window import MainWindow  # noqa: E402
from client.ui.theme import apply_theme  # noqa: E402
from server.logging_setup import setup_logging  # noqa: E402
from shared.constants import APP_NAME, APP_VERSION  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SynCanvas LAN collaborative whiteboard")
    parser.add_argument("--host", default=None, help="Default server IP address")
    parser.add_argument("--port", type=int, default=None, help="Default server port")
    parser.add_argument("--theme", choices=["midnight", "cloud"], default=None)
    parser.add_argument("--reset-settings", action="store_true", help="Start with fresh local preferences")
    return parser.parse_args()


def _install_exception_hook() -> None:
    def handle(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("syncanvas.client").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "Unexpected error",
                    f"SynCanvas hit an unexpected error:\n\n{exc_value}\n\n"
                    "Details were written to logs/client.log.",
                )
        except Exception:
            pass

    sys.excepthook = handle


def main() -> int:
    args = parse_args()
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "SynCanvas.Whiteboard.1"
            )
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("SynCanvas")
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(app_icon())
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    setup_logging(default_log_dir(), "INFO", "client.log")
    _install_exception_hook()

    store = SettingsStore()
    if args.reset_settings:
        store.settings = type(store.settings)()
        store.save()
    if args.host:
        store.settings.host = args.host
    if args.port:
        store.settings.port = int(args.port)
    theme = args.theme or store.settings.theme or "midnight"
    apply_theme(app, theme)
    window = MainWindow(store)
    window.show()
    logging.getLogger("syncanvas.client").info(
        "Client started · %s %s · platform=%s · Python=%s",
        APP_NAME,
        APP_VERSION,
        sys.platform,
        sys.version.split()[0],
    )
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
