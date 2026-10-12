"""Cross-platform backend built on pynput (Windows, macOS, Linux/X11)."""

from __future__ import annotations

from typing import Optional

from .base import Geometry, InputBackend

# webmouse key name -> attribute name on pynput.keyboard.Key
_NAMED = {
    "enter": "enter",
    "backspace": "backspace",
    "tab": "tab",
    "esc": "esc",
    "space": "space",
    "delete": "delete",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "insert": "insert",
    "capslock": "caps_lock",
    "menu": "menu",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "meta": "cmd",
    "volup": "media_volume_up",
    "voldown": "media_volume_down",
    "mute": "media_volume_mute",
    "playpause": "media_play_pause",
    "next": "media_next",
    "prev": "media_previous",
}
_NAMED.update({f"f{i}": f"f{i}" for i in range(1, 21)})


class PynputBackend(InputBackend):
    name = "pynput"

    def __init__(self) -> None:
        # Importing pynput fails on headless Linux, which is how "auto"
        # knows to try the next backend.
        from pynput import keyboard, mouse

        self._mouse = mouse.Controller()
        self._kb = keyboard.Controller()
        self._buttons = {
            "left": mouse.Button.left,
            "right": mouse.Button.right,
            "middle": mouse.Button.middle,
        }
        self._keys = {}
        for ours, theirs in _NAMED.items():
            key = getattr(keyboard.Key, theirs, None)
            if key is not None:
                self._keys[ours] = key

    # --- pointer -----------------------------------------------------------
    def move_rel(self, dx: int, dy: int) -> None:
        self._mouse.move(dx, dy)

    def move_abs(
        self,
        fx: float,
        fy: float,
        geom: Optional[Geometry],
        virtual: Optional[Geometry],
    ) -> None:
        if geom is None:
            return
        left, top, width, height = geom
        x = left + min(max(fx, 0.0), 1.0) * (width - 1)
        y = top + min(max(fy, 0.0), 1.0) * (height - 1)
        self._mouse.position = (round(x), round(y))

    def button(self, name: str, down: bool) -> None:
        btn = self._buttons[name]
        if down:
            self._mouse.press(btn)
        else:
            self._mouse.release(btn)

    def scroll(self, dx: int, dy: int) -> None:
        self._mouse.scroll(dx, dy)

    def position(self):
        x, y = self._mouse.position
        return int(x), int(y)

    # --- keyboard ----------------------------------------------------------
    def _resolve(self, name: str):
        if name in self._keys:
            return self._keys[name]
        if len(name) == 1:
            return name
        raise ValueError(f"unknown key {name!r}")

    def key(self, name: str, down: bool) -> None:
        key = self._resolve(name)
        if down:
            self._kb.press(key)
        else:
            self._kb.release(key)

    def type_text(self, text: str) -> None:
        self._kb.type(text)
