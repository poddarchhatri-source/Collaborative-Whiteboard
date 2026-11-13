#!/usr/bin/env python3
"""Launch the SynCanvas Server Control Centre (GUI)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for source_dir in (ROOT / "Server", ROOT / "Client", ROOT):
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))

from server.config import ServerConfig
from server.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Open the SynCanvas server control centre")
    parser.add_argument(
        "--start",
        action="store_true",
        help="start listening automatically after opening the control centre",
    )
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication
    from client.ui.icons import app_icon
    from client.ui.theme import apply_theme
    from server.gui import ServerWindow

    config = ServerConfig.from_env()
    setup_logging(config.log_dir, config.log_level)
    app = QApplication(sys.argv)
    app.setApplicationName("SynCanvas Server")
    app.setOrganizationName("SynCanvas")
    app.setWindowIcon(app_icon())
    apply_theme(app, "midnight")
    window = ServerWindow(config)
    window.show()
    if args.start:
        window.start_button.click()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
