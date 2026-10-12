"""Process-level tweaks that must happen before any screen or input API is used."""

from __future__ import annotations

import sys


def prepare_process() -> None:
    if sys.platform == "win32":
        # Without this, Windows reports scaled coordinates to some APIs and real
        # pixels to others, which makes absolute positioning drift on HiDPI screens.
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass
