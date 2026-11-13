"""Server configuration loaded from CLI, environment or defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from shared.constants import DEFAULT_DB_PATH, DEFAULT_HOST, DEFAULT_PORT

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    """Writable data directory for source runs and frozen executables."""
    override = os.getenv("SYNCANVAS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("win"):
            base = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
            return base / "SynCanvas"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "SynCanvas"
        base = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        return base / "syncanvas"
    return PROJECT_ROOT


DATA_DIR = default_data_dir()
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"
DEFAULT_SERVER_DB = DATA_DIR / "database" / "syncanvas.db"


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_path: Path = field(default_factory=lambda: DEFAULT_SERVER_DB)
    log_dir: Path = field(default_factory=lambda: LOG_DIR)
    autosave_seconds: float = 2.0
    snapshot_interval_seconds: float = 300.0
    max_sessions: int = 50
    max_clients_per_session: int = 40
    motd: str = "Welcome to SynCanvas"
    log_level: str = "INFO"
    require_password: bool = False
    bind_all: bool = True

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path).expanduser().resolve()
        self.log_dir = Path(self.log_dir).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if not (0 <= int(self.port) <= 65535):
            raise ValueError("Port must be between 0 and 65535 (0 chooses an ephemeral port)")
        if not self.host:
            self.host = DEFAULT_HOST

    @classmethod
    def from_env(cls) -> "ServerConfig":
        return cls(
            host=os.getenv("SYNCANVAS_HOST", DEFAULT_HOST),
            port=int(os.getenv("SYNCANVAS_PORT", str(DEFAULT_PORT))),
            db_path=Path(os.getenv("SYNCANVAS_DB", str(DEFAULT_SERVER_DB))),
            autosave_seconds=float(os.getenv("SYNCANVAS_AUTOSAVE", "2.0")),
            snapshot_interval_seconds=float(os.getenv("SYNCANVAS_SNAPSHOT_INTERVAL", "300")),
            max_sessions=int(os.getenv("SYNCANVAS_MAX_SESSIONS", "50")),
            max_clients_per_session=int(os.getenv("SYNCANVAS_MAX_CLIENTS", "40")),
            motd=os.getenv("SYNCANVAS_MOTD", "Welcome to SynCanvas"),
            log_level=os.getenv("SYNCANVAS_LOG_LEVEL", "INFO"),
            require_password=_bool_env("SYNCANVAS_REQUIRE_PASSWORD", False),
        )

    @property
    def display_host(self) -> str:
        return "0.0.0.0 (all interfaces)" if self.host in ("0.0.0.0", "") else self.host
