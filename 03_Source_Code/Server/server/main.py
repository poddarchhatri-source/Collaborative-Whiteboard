"""Headless SynCanvas server entry point.

Usage:
    python server/main.py --host 0.0.0.0 --port 45871
    python server/main.py --gui
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.config import ServerConfig  # noqa: E402
from server.logging_setup import setup_logging  # noqa: E402
from server.network import SynCanvasServer, lan_ip_addresses  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SynCanvas LAN whiteboard server")
    parser.add_argument("--host", default=None, help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="TCP port (default 45871)")
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--gui", action="store_true", help="Open the graphical server control centre")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING or ERROR")
    return parser.parse_args()


async def run_headless(config: ServerConfig) -> None:
    server = SynCanvasServer(config)
    port = await server.start()
    print("=" * 70)
    print("  SynCanvas server is running")
    print(f"  Bind address : {config.host}:{port}")
    print("  Local LAN IP :")
    for address in lan_ip_addresses():
        print(f"                 {address}:{port}")
    print(f"  Database     : {config.db_path}")
    print("  Press Ctrl+C to stop.")
    print("=" * 70)
    stop_event = asyncio.Event()

    def request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop_event.wait()
    finally:
        await server.stop()


def main() -> int:
    args = parse_args()
    config = ServerConfig.from_env()
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    if args.db is not None:
        config.db_path = Path(args.db).expanduser().resolve()
    if args.log_level:
        config.log_level = args.log_level.upper()
    setup_logging(config.log_dir, config.log_level)
    if args.gui:
        return run_gui(config)
    try:
        asyncio.run(run_headless(config))
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"Could not start server: {exc}", file=sys.stderr)
        return 1
    return 0


def run_gui(config: ServerConfig) -> int:
    from PySide6.QtWidgets import QApplication

    from client.ui.icons import app_icon
    from client.ui.theme import apply_theme
    from server.gui import ServerWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SynCanvas Server")
    app.setOrganizationName("SynCanvas")
    app.setWindowIcon(app_icon())
    apply_theme(app, "midnight")
    window = ServerWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
