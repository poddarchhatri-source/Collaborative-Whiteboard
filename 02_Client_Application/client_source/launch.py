#!/usr/bin/env python3
"""Launch the SynCanvas server control centre and client."""

from __future__ import annotations

import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER_MODE = "--_syncanvas-server"
CLIENT_MODE = "--_syncanvas-client"


def _show_error(message: str) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    QMessageBox.critical(app.activeWindow(), "SynCanvas", message)


def _dispatch_embedded_mode() -> None:
    mode = sys.argv[1]
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    if mode == SERVER_MODE:
        from run_server import main
    elif mode == CLIENT_MODE:
        from run_client import main
    else:
        return
    raise SystemExit(main())


def _child_command(mode: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, str(Path(__file__).resolve()), mode]


def _stop_server(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


def main() -> int:
    server = subprocess.Popen(
        _child_command(SERVER_MODE),
        cwd=ROOT,
        creationflags=0x08000000 if platform.system() == "Windows" else 0,
    )
    time.sleep(2)
    if server.poll() is not None:
        _show_error("The server could not start. Please check the server configuration.")
        return 1

    try:
        client = subprocess.Popen(
            _child_command(CLIENT_MODE),
            cwd=ROOT,
            creationflags=0x08000000 if platform.system() == "Windows" else 0,
        )
        return client.wait()
    except OSError as exc:
        _show_error(f"The SynCanvas client could not start:\n{exc}")
        return 1
    finally:
        _stop_server(server)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in (SERVER_MODE, CLIENT_MODE):
        _dispatch_embedded_mode()
    raise SystemExit(main())
