"""Configuration: dataclasses, TOML loading, environment and CLI overrides."""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

log = logging.getLogger("webmouse.config")

APP_NAME = "webmouse"


class ConfigError(Exception):
    """Raised for unreadable or invalid configuration."""


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    public_url: str = ""
    allowed_hosts: list = field(default_factory=list)


@dataclass
class SecurityConfig:
    pin: str = "auto"
    pin_length: int = 6
    allowed_networks: list = field(default_factory=list)
    max_failed_attempts: int = 5
    lockout_seconds: int = 60
    session_hours: float = 12.0
    max_devices: int = 5
    qr_includes_pin: bool = True


@dataclass
class TlsConfig:
    enabled: bool = False
    cert_file: str = ""
    key_file: str = ""


@dataclass
class InputConfig:
    backend: str = "auto"
    scroll_px_per_tick: float = 28.0
    zoom_px_per_tick: float = 30.0
    max_text_length: int = 2000


@dataclass
class StreamConfig:
    enabled: bool = True
    fps: int = 20
    quality: int = 70
    scale: float = 0.75
    draw_cursor: bool = True


@dataclass
class UiConfig:
    theme: str = "system"
    mode: str = "relative"
    sensitivity: float = 1.0
    acceleration: float = 0.5
    scroll_speed: float = 1.0
    natural_scroll: bool = True
    tap_to_click: bool = True
    pen_mode: str = "direct"
    show_buttons: bool = True
    haptics: bool = True
    keep_awake: bool = True


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    tls: TlsConfig = field(default_factory=TlsConfig)
    input: InputConfig = field(default_factory=InputConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    ui: UiConfig = field(default_factory=UiConfig)


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def default_config_path() -> Path:
    return user_config_dir() / "config.toml"


def default_config_text() -> str:
    return resources.files("webmouse").joinpath("default_config.toml").read_text("utf-8")


def find_config(explicit: Optional[str] = None) -> Optional[Path]:
    candidate = explicit or os.environ.get("PADLINK_CONFIG")
    if candidate:
        path = Path(candidate).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    for path in (Path.cwd() / "webmouse.toml", default_config_path()):
        if path.is_file():
            return path
    return None


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def _coerce(section: str, name: str, current: Any, value: Any) -> Any:
    label = f"[{section}] {name}"
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{label} must be true or false")
        return value
    if isinstance(current, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{label} must be a whole number")
        return value
    if isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{label} must be a number")
        return float(value)
    if isinstance(current, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)  # e.g. pin = 123456
        if not isinstance(value, str):
            raise ConfigError(f"{label} must be text")
        return value
    if isinstance(current, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{label} must be a list of strings")
        return list(value)
    return value


def _apply(cfg: Config, data: Mapping[str, Any], source: str) -> None:
    for section_name, section_data in data.items():
        section = getattr(cfg, section_name, None)
        if section is None or not dataclasses.is_dataclass(section):
            log.warning("%s: unknown section [%s] ignored", source, section_name)
            continue
        if not isinstance(section_data, dict):
            raise ConfigError(f"{source}: [{section_name}] must be a table")
        names = {f.name for f in dataclasses.fields(section)}
        for key, value in section_data.items():
            if key not in names:
                log.warning("%s: unknown option %s.%s ignored", source, section_name, key)
                continue
            setattr(section, key, _coerce(section_name, key, getattr(section, key), value))


_ENV = {
    "PADLINK_HOST": ("server", "host", str),
    "PADLINK_PORT": ("server", "port", int),
    "PADLINK_PUBLIC_URL": ("server", "public_url", str),
    "PADLINK_PIN": ("security", "pin", str),
    "PADLINK_BACKEND": ("input", "backend", str),
    "PADLINK_TLS": ("tls", "enabled", "bool"),
    "PADLINK_STREAM": ("stream", "enabled", "bool"),
}


def _env_overrides(env: Mapping[str, str]) -> dict:
    out: dict = {}
    for var, (section, key, kind) in _ENV.items():
        raw = env.get(var)
        if raw is None or raw == "":
            continue
        try:
            if kind == "bool":
                value: Any = raw.strip().lower() in ("1", "true", "yes", "on")
            else:
                value = kind(raw)
        except ValueError:
            raise ConfigError(f"{var} has an invalid value: {raw!r}") from None
        out.setdefault(section, {})[key] = value
    return out


def validate(cfg: Config) -> None:
    if not 1 <= cfg.server.port <= 65535:
        raise ConfigError("[server] port must be between 1 and 65535")
    sec = cfg.security
    if not 4 <= sec.pin_length <= 12:
        raise ConfigError("[security] pin_length must be between 4 and 12")
    if sec.pin != "auto" and len(sec.pin) < 4:
        raise ConfigError("[security] pin must be at least 4 characters (or \"auto\")")
    if sec.max_failed_attempts < 1 or sec.lockout_seconds < 1:
        raise ConfigError("[security] max_failed_attempts and lockout_seconds must be >= 1")
    if sec.session_hours <= 0 or sec.max_devices < 1:
        raise ConfigError("[security] session_hours and max_devices must be positive")
    import ipaddress

    for net in sec.allowed_networks:
        try:
            ipaddress.ip_network(net, strict=False)
        except ValueError:
            raise ConfigError(f"[security] allowed_networks: invalid CIDR {net!r}") from None
    if cfg.input.backend.lower() not in ("auto", "pynput", "uinput", "dummy"):
        raise ConfigError("[input] backend must be auto, pynput, uinput or dummy")
    if cfg.input.scroll_px_per_tick <= 0 or cfg.input.zoom_px_per_tick <= 0:
        raise ConfigError("[input] *_px_per_tick must be positive")
    st = cfg.stream
    if not 1 <= st.fps <= 60:
        raise ConfigError("[stream] fps must be between 1 and 60")
    if not 10 <= st.quality <= 95:
        raise ConfigError("[stream] quality must be between 10 and 95")
    if not 0.1 <= st.scale <= 1.0:
        raise ConfigError("[stream] scale must be between 0.1 and 1.0")
    ui = cfg.ui
    if ui.theme not in ("system", "light", "dark"):
        raise ConfigError("[ui] theme must be system, light or dark")
    if ui.mode not in ("relative", "absolute"):
        raise ConfigError("[ui] mode must be relative or absolute")
    if ui.pen_mode not in ("direct", "touchpad"):
        raise ConfigError("[ui] pen_mode must be direct or touchpad")


def load_config(
    path: Optional[str] = None,
    cli: Optional[Mapping[str, Mapping[str, Any]]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> tuple[Config, Optional[Path]]:
    """Build the effective configuration.

    Returns the config and the file it was read from (or ``None``).
    """
    cfg = Config()
    found = find_config(path)
    if found is not None:
        try:
            with open(found, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{found}: {exc}") from None
        _apply(cfg, data, str(found))
    _apply(cfg, _env_overrides(os.environ if env is None else env), "environment")
    if cli:
        _apply(cfg, {k: dict(v) for k, v in cli.items()}, "command line")
    validate(cfg)
    return cfg, found
