"""Translate client WebSocket messages into input actions.

Wire protocol (JSON objects with a ``t`` field), client -> server:

  m   {x, y}            relative pointer motion in device pixels (fractional)
  a   {x, y}            absolute pointer position, 0..1 inside the selected monitor
  b   {b, d}            button b ("left"/"right"/"middle") down (d=1) or up (d=0)
  c   {b, n}            click button b, n times
  s   {x, y}            scroll; y > 0 scrolls up, x > 0 scrolls right (pixels)
  z   {d}               zoom; d > 0 zooms in (pixels of pinch travel)
  key {k, mods, n}      press key k (see KEY_NAMES) with modifiers, n times
  text {s}              type text
  rename {name}         change this device's display name
  monitor {i}           select the monitor used for absolute mode
  ping {ts}             answered with pong {ts}
  release {}            release anything held down
"""

from __future__ import annotations

import math
import re
import sys
from typing import Any, Optional

from .config import Config
from .input.base import InputBackend
from .screens import Screens
from .security import Session, clean_name
from .worker import InputWorker

KEY_NAMES = {
    "enter", "backspace", "tab", "esc", "space", "delete", "up", "down", "left",
    "right", "home", "end", "pageup", "pagedown", "insert", "capslock", "menu",
    "volup", "voldown", "mute", "playpause", "next", "prev",
} | {f"f{i}" for i in range(1, 21)}
_MODS = {"ctrl", "alt", "shift", "meta"}
_BUTTONS = {"left", "right", "middle"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # everything except \t and \n


def _num(value: Any, limit: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return max(-limit, min(limit, float(value)))


class Controller:
    def __init__(
        self,
        cfg: Config,
        backend: InputBackend,
        worker: InputWorker,
        screens: Screens,
    ) -> None:
        self.cfg = cfg
        self.backend = backend
        self.worker = worker
        self.screens = screens
        self._mod_key = "meta" if sys.platform == "darwin" else "ctrl"

    # --- dispatch ----------------------------------------------------------
    def handle(self, s: Session, msg: dict) -> Optional[dict]:
        kind = msg.get("t")
        fn = getattr(self, f"_on_{kind}", None) if isinstance(kind, str) else None
        if fn is None or kind.startswith("_"):
            return None
        return fn(s, msg)

    # --- pointer -----------------------------------------------------------
    def _on_m(self, s: Session, m: dict) -> None:
        s.acc_x += _num(m.get("x"), 2000)
        s.acc_y += _num(m.get("y"), 2000)
        ix, iy = int(s.acc_x), int(s.acc_y)  # truncate toward zero
        if ix or iy:
            s.acc_x -= ix
            s.acc_y -= iy
            self.worker.submit(self.backend.move_rel, ix, iy)

    def _on_a(self, s: Session, m: dict) -> None:
        fx = min(max(_num(m.get("x"), 2), 0.0), 1.0)
        fy = min(max(_num(m.get("y"), 2), 0.0), 1.0)
        mon = self.screens.get(s.monitor) or self.screens.get(self.screens.default_index())
        virtual = self.screens.virtual()
        self.worker.submit(
            self.backend.move_abs,
            fx,
            fy,
            mon.geometry if mon else None,
            virtual.geometry if virtual else None,
        )

    def _on_b(self, s: Session, m: dict) -> None:
        name = m.get("b")
        if name not in _BUTTONS:
            return
        down = bool(m.get("d"))
        (s.pressed.add if down else s.pressed.discard)(name)
        self.worker.submit(self.backend.button, name, down)

    def _on_c(self, s: Session, m: dict) -> None:
        name = m.get("b")
        if name not in _BUTTONS:
            return
        count = int(min(max(_num(m.get("n", 1), 3), 1), 3))
        self.worker.submit(self.backend.click, name, count)

    def _on_s(self, s: Session, m: dict) -> None:
        per = self.cfg.input.scroll_px_per_tick
        s.scroll_x += _num(m.get("x"), 5000) / per
        s.scroll_y += _num(m.get("y"), 5000) / per
        tx, ty = int(s.scroll_x), int(s.scroll_y)
        if tx or ty:
            s.scroll_x -= tx
            s.scroll_y -= ty
            self.worker.submit(self.backend.scroll, tx, ty)

    def _on_z(self, s: Session, m: dict) -> None:
        s.zoom += _num(m.get("d"), 5000) / self.cfg.input.zoom_px_per_tick
        ticks = int(s.zoom)
        if ticks:
            s.zoom -= ticks
            self.worker.submit(self.backend.zoom, ticks)

    # --- keyboard ----------------------------------------------------------
    def _on_key(self, s: Session, m: dict) -> None:
        key = m.get("k")
        if not isinstance(key, str):
            return
        if key not in KEY_NAMES and not (len(key) == 1 and key.isprintable()):
            return
        mods: list[str] = []
        for mod in m.get("mods") or []:
            mod = self._mod_key if mod == "mod" else mod
            if mod in _MODS and mod not in mods:
                mods.append(mod)
        count = int(min(max(_num(m.get("n", 1), 100), 1), 100))
        self.worker.submit(self.backend.tap_key, key, tuple(mods), count)

    def _on_text(self, s: Session, m: dict) -> None:
        text = m.get("s")
        if not isinstance(text, str):
            return
        text = _CONTROL.sub("", text)[: self.cfg.input.max_text_length]
        if text:
            self.worker.submit(self.backend.type_text, text)

    # --- session -----------------------------------------------------------
    def _on_rename(self, s: Session, m: dict) -> dict:
        s.name = clean_name(m.get("name"), s.name)
        return {"t": "renamed", "name": s.name}

    def _on_monitor(self, s: Session, m: dict) -> Optional[dict]:
        index = int(_num(m.get("i"), 64))
        mon = self.screens.get(index)
        if mon is None or (index == 0 and self.screens.physical()):
            return None
        s.monitor = index
        return {"t": "screen", "monitor": index, "w": mon.width, "h": mon.height}

    def _on_ping(self, s: Session, m: dict) -> dict:
        return {"t": "pong", "ts": m.get("ts")}

    def _on_release(self, s: Session, m: dict) -> None:
        self.release_all(s)

    def release_all(self, s: Session) -> None:
        for name in list(s.pressed):
            self.worker.submit(self.backend.button, name, False)
        s.pressed.clear()
