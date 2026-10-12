"""LAN-only network policy, PIN pairing, sessions and brute-force lockout."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import string
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .config import SecurityConfig

# Loopback, RFC1918 private, link-local, and IPv6 unique-local / link-local.
_LAN_NETWORKS = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)


class NetworkPolicy:
    """Decides whether a client address belongs to the local network."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        self.networks = [ipaddress.ip_network(n) for n in _LAN_NETWORKS]
        self.networks += [ipaddress.ip_network(n, strict=False) for n in extra]

    def allows(self, address: Optional[str]) -> bool:
        if not address:
            return False
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return any(ip.version == n.version and ip in n for n in self.networks)


def generate_pin(length: int = 6) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


def resolve_pin(cfg: SecurityConfig) -> str:
    return generate_pin(cfg.pin_length) if cfg.pin.lower() == "auto" else cfg.pin


def clean_name(raw: object, default: str = "Unnamed device") -> str:
    text = "".join(ch for ch in str(raw or "") if unicodedata.category(ch)[0] != "C")
    return text.strip()[:32] or default


class LoginError(Exception):
    def __init__(self, message: str, status: int = 401, retry_after: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass
class Session:
    token: str
    id: str
    name: str
    ip: str
    created: float
    last_seen: float
    monitor: int = 1
    # live connections and input state, owned by the server / controller
    sockets: set = field(default_factory=set, repr=False)
    pressed: set = field(default_factory=set, repr=False)
    acc_x: float = 0.0
    acc_y: float = 0.0
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    zoom: float = 0.0


@dataclass
class _Failures:
    count: int = 0
    level: int = 0
    locked_until: float = 0.0


class AuthManager:
    def __init__(
        self,
        cfg: SecurityConfig,
        pin: str,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.pin = pin
        self._now = now
        self._sessions: dict[str, Session] = {}
        self._failures: dict[str, _Failures] = {}

    # --- login -------------------------------------------------------------
    def login(self, ip: str, pin: str, name: str) -> Session:
        now = self._now()
        fail = self._failures.setdefault(ip, _Failures())
        if fail.locked_until > now:
            wait = int(fail.locked_until - now) + 1
            raise LoginError(f"Too many wrong PINs. Try again in {wait}s.", 429, wait)

        if not hmac.compare_digest(pin.strip().encode(), self.pin.encode()):
            fail.count += 1
            if fail.count >= self.cfg.max_failed_attempts:
                fail.level += 1
                fail.count = 0
                wait = min(self.cfg.lockout_seconds * 2 ** (fail.level - 1), 3600)
                fail.locked_until = now + wait
                raise LoginError(f"Too many wrong PINs. Try again in {wait}s.", 429, wait)
            raise LoginError("Wrong PIN.", 401)

        self._failures.pop(ip, None)
        while len(self._sessions) >= self.cfg.max_devices:
            oldest = min(self._sessions.values(), key=lambda s: s.last_seen)
            self.revoke(oldest.id)

        token = secrets.token_urlsafe(32)
        session = Session(
            token=token,
            id=hashlib.sha256(token.encode()).hexdigest()[:10],
            name=clean_name(name),
            ip=ip,
            created=now,
            last_seen=now,
        )
        self._sessions[token] = session
        return session

    # --- lookup ------------------------------------------------------------
    def get(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        now = self._now()
        if now - session.created > self.cfg.session_hours * 3600:
            self._sessions.pop(token, None)
            return None
        session.last_seen = now
        return session

    def sessions(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.created)

    def logout(self, token: Optional[str]) -> Optional[Session]:
        return self._sessions.pop(token, None) if token else None

    def revoke(self, session_id: str) -> Optional[Session]:
        for token, session in list(self._sessions.items()):
            if session.id == session_id:
                return self._sessions.pop(token)
        return None

    def reap(self) -> list[Session]:
        """Drop expired sessions and stale failure records."""
        now = self._now()
        expired = [
            s for s in self._sessions.values()
            if now - s.created > self.cfg.session_hours * 3600
        ]
        for s in expired:
            self._sessions.pop(s.token, None)
        for ip, fail in list(self._failures.items()):
            if fail.count == 0 and fail.locked_until < now - 3600:
                self._failures.pop(ip, None)
        return expired
