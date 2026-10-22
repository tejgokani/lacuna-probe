"""TUI settings, stored as JSON in the config directory."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from lacuna.config import DEFAULT_PORT, config_dir, default_db_path, default_profile

WINDOWS = ("1h", "6h", "24h", "7d", "all")
WINDOW_SECONDS = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "all": None}


@dataclass
class Settings:
    profile: str = ""  # empty means the hostname
    db_path: str = ""  # empty means the default location
    refresh_s: float = 2.0
    window: str = "24h"
    config_path: str = ""  # empty means ~/.config/lacuna/lacuna.toml if it exists
    server_bind: str = "0.0.0.0"
    server_port: int = DEFAULT_PORT
    output_dir: str = ""  # empty means the current directory
    color: bool = True

    def effective_profile(self) -> str:
        return self.profile or default_profile()

    def effective_db(self) -> Path:
        return Path(self.db_path).expanduser() if self.db_path else default_db_path()

    def effective_output_dir(self) -> Path:
        return Path(self.output_dir).expanduser() if self.output_dir else Path.cwd()


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load(path: Path | None = None) -> Settings:
    """Read settings. Missing or damaged files give defaults, unknown keys are ignored."""
    path = path or settings_path()
    s = Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return s
    if not isinstance(raw, dict):
        return s
    for f in fields(Settings):
        if f.name in raw:
            try:
                apply(s, f.name, raw[f.name], persist=False)
            except ValueError:
                pass
    return s


def save(s: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def apply(s: Settings, key: str, value: object, persist: bool = True) -> None:
    """Validate and set one setting. Raises ValueError with a readable message."""
    text = str(value).strip()
    if key in ("profile", "db_path", "config_path", "output_dir"):
        setattr(s, key, text)
    elif key == "refresh_s":
        num = _number(text, "refresh")
        if not 0.5 <= num <= 60:
            raise ValueError("refresh must be between 0.5 and 60 seconds")
        s.refresh_s = num
    elif key == "window":
        if text not in WINDOWS:
            raise ValueError(f"window must be one of {', '.join(WINDOWS)}")
        s.window = text
    elif key == "server_bind":
        try:
            socket.inet_aton(text)
        except OSError:
            raise ValueError("bind must be an IPv4 address such as 0.0.0.0 or 127.0.0.1") from None
        s.server_bind = text
    elif key == "server_port":
        port = int(_number(text, "port"))
        if not 1 <= port <= 65534:
            raise ValueError("port must be between 1 and 65534 (TCP uses port + 1)")
        s.server_port = port
    elif key == "color":
        s.color = text.lower() in ("1", "true", "on", "yes") if not isinstance(value, bool) else value
    else:
        raise ValueError(f"unknown setting {key}")
    if persist:
        save(s)


def _number(text: str, name: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
