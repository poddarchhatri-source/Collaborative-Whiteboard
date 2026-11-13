"""Cross-platform client configuration and persisted preferences."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from shared.constants import DEFAULT_CLIENT_HOST, DEFAULT_PORT, DEFAULT_STROKE

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def user_config_dir() -> Path:
    override = os.getenv("SYNCANVAS_CLIENT_CONFIG")
    if override:
        path = Path(override).expanduser()
        return path.parent if path.suffix else path
    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "SynCanvas"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SynCanvas"
    base = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "syncanvas"


def default_log_dir() -> Path:
    if getattr(sys, "frozen", False):
        return user_config_dir() / "logs"
    return PROJECT_ROOT / "logs"


def default_export_dir() -> Path:
    override = os.getenv("SYNCANVAS_EXPORT_DIR")
    if override:
        return Path(override).expanduser()
    documents = Path.home() / "Documents"
    base = documents if documents.exists() else Path.home()
    return base / "SynCanvas exports"


def config_file_path() -> Path:
    override = os.getenv("SYNCANVAS_CLIENT_CONFIG")
    if override:
        return Path(override).expanduser()
    return user_config_dir() / "settings.json"


@dataclass
class ClientSettings:
    host: str = DEFAULT_CLIENT_HOST
    port: int = DEFAULT_PORT
    username: str = ""
    last_code: str = ""
    last_session_name: str = ""
    tokens: dict[str, str] = field(default_factory=dict)
    recent_sessions: list[dict[str, Any]] = field(default_factory=list)
    theme: str = "midnight"
    show_grid: bool = True
    snap_to_grid: bool = False
    show_cursors: bool = True
    stroke_color: str = DEFAULT_STROKE
    stroke_width: int = 3
    fill_color: str = "#27272a"
    sticky_color: str = "#fef9c3"
    font_size: int = 18
    window_geometry: str = ""
    auto_snapshot_minutes: int = 5
    reduce_motion: bool = False
    presentation_follow: bool = True
    export_directory: str = str(default_export_dir())
    developer_save_state: bool = False

    def token_for(self, code: str) -> str:
        return self.tokens.get(code.upper(), "")

    def remember_session(self, code: str, name: str, token: str, host: str, port: int) -> None:
        code = code.upper()
        if token:
            self.tokens[code] = token
            # Keep the token store bounded.
            if len(self.tokens) > 30:
                self.tokens = dict(list(self.tokens.items())[-30:])
        self.last_code = code
        self.last_session_name = name
        entry = {
            "code": code,
            "name": name,
            "host": host,
            "port": port,
        }
        self.recent_sessions = [s for s in self.recent_sessions if s.get("code") != code]
        self.recent_sessions.insert(0, entry)
        self.recent_sessions = self.recent_sessions[:12]


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_file_path()
        self.settings = ClientSettings()
        self.load()

    def load(self) -> ClientSettings:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    allowed = set(ClientSettings.__dataclass_fields__)
                    clean = {k: v for k, v in data.items() if k in allowed}
                    self.settings = ClientSettings(**clean)
        except (OSError, json.JSONDecodeError, TypeError):
            self.settings = ClientSettings()
        return self.settings

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(self.settings), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError:
            pass

    def update(self, **values: Any) -> None:
        for key, value in values.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save()
