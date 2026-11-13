#!/usr/bin/env python3
"""Build separate native SynCanvas server and client applications."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SOURCE_DIR.parent
PLATFORM_DIRS = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
APPLICATIONS = (
    ("server", SOURCE_DIR / "run_server.py", PROJECT_DIR / "01_Server_Application"),
    ("client", SOURCE_DIR / "run_client.py", PROJECT_DIR / "02_Client_Application"),
)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def build() -> None:
    system = platform.system()
    platform_dir = PLATFORM_DIRS.get(system)
    if platform_dir is None:
        raise SystemExit(f"Unsupported operating system: {system}")

    check = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        cwd=SOURCE_DIR,
        check=False,
    )
    if check.returncode:
        raise SystemExit(
            "PyInstaller is required. Install the development dependencies with "
            f'"{sys.executable}" -m pip install -e ".[dev]" from {SOURCE_DIR}.'
        )

    mode = "--onedir" if system == "Darwin" else "--onefile"
    for name, entry_point, output_dir in APPLICATIONS:
        output = output_dir / name
        _remove(output)
        _remove(output.with_suffix(".app"))
        _remove(output.with_suffix(".exe"))
        work_dir = PROJECT_DIR / "build" / "pyinstaller" / platform_dir / name
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            mode,
            "--windowed",
            "--name",
            name,
            "--distpath",
            str(output_dir),
            "--workpath",
            str(work_dir),
            "--specpath",
            str(work_dir),
            "--paths",
            str(SOURCE_DIR / "Server"),
            "--paths",
            str(SOURCE_DIR / "Client"),
            "--paths",
            str(SOURCE_DIR),
            "--add-data",
            f"{SOURCE_DIR / 'assets'}{os.pathsep}assets",
            str(entry_point),
        ]
        subprocess.run(command, cwd=SOURCE_DIR, check=True)
        if system == "Darwin":
            shutil.rmtree(output_dir / name)

    print("Native server and client applications created.")


if __name__ == "__main__":
    build()
