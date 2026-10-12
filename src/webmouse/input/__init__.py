"""Input injection backends."""

from __future__ import annotations

import logging
import os
import sys

from .base import BackendError, DummyBackend, InputBackend

log = logging.getLogger("webmouse.input")

__all__ = ["BackendError", "InputBackend", "DummyBackend", "create_backend"]


def _make(name: str) -> InputBackend:
    if name == "pynput":
        from .pynput_backend import PynputBackend

        return PynputBackend()
    if name == "uinput":
        from .uinput_backend import UinputBackend

        return UinputBackend()
    if name == "dummy":
        return DummyBackend()
    raise BackendError(f"unknown backend {name!r}")


def create_backend(name: str = "auto") -> InputBackend:
    """Create an input backend.

    ``auto`` picks ``pynput`` everywhere, except on Wayland where the kernel
    ``uinput`` interface is the only reliable option.
    """
    name = name.lower()
    if name != "auto":
        return _make(name)

    order = ["pynput"]
    if sys.platform.startswith("linux"):
        wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        order = ["uinput", "pynput"] if wayland else ["pynput", "uinput"]

    errors: list[str] = []
    for candidate in order:
        try:
            backend = _make(candidate)
            log.debug("using input backend %s", candidate)
            return backend
        except Exception as exc:  # noqa: BLE001 - any failure means "try next"
            errors.append(f"  - {candidate}: {exc}")
    raise BackendError(
        "no usable input backend found:\n" + "\n".join(errors) + _hint()
    )


def _hint() -> str:
    if sys.platform == "darwin":
        return (
            "\nOn macOS, grant your terminal Accessibility access under "
            "System Settings > Privacy & Security."
        )
    if sys.platform.startswith("linux"):
        return (
            "\nOn Linux, run inside an X11 session (DISPLAY must be set), or on "
            "Wayland give your user write access to /dev/uinput (see README)."
        )
    return ""
