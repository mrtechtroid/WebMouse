"""Linux kernel uinput backend (works on Wayland, X11 and the console).

Needs write access to /dev/uinput. Keyboard typing assumes a US layout because
uinput injects physical key codes, not characters.
"""

from __future__ import annotations

from typing import Optional

from .base import Geometry, InputBackend

_ABS_MAX = 65535

_NAMED = {
    "enter": "KEY_ENTER",
    "backspace": "KEY_BACKSPACE",
    "tab": "KEY_TAB",
    "esc": "KEY_ESC",
    "space": "KEY_SPACE",
    "delete": "KEY_DELETE",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "home": "KEY_HOME",
    "end": "KEY_END",
    "pageup": "KEY_PAGEUP",
    "pagedown": "KEY_PAGEDOWN",
    "insert": "KEY_INSERT",
    "capslock": "KEY_CAPSLOCK",
    "menu": "KEY_MENU",
    "ctrl": "KEY_LEFTCTRL",
    "alt": "KEY_LEFTALT",
    "shift": "KEY_LEFTSHIFT",
    "meta": "KEY_LEFTMETA",
    "volup": "KEY_VOLUMEUP",
    "voldown": "KEY_VOLUMEDOWN",
    "mute": "KEY_MUTE",
    "playpause": "KEY_PLAYPAUSE",
    "next": "KEY_NEXTSONG",
    "prev": "KEY_PREVIOUSSONG",
}
_NAMED.update({f"f{i}": f"KEY_F{i}" for i in range(1, 13)})

# character -> (KEY_ name, needs shift), US layout
_CHARS: dict[str, tuple[str, bool]] = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _CHARS[_c] = (f"KEY_{_c.upper()}", False)
    _CHARS[_c.upper()] = (f"KEY_{_c.upper()}", True)
for _digit, _sym in zip("1234567890", "!@#$%^&*()"):
    _CHARS[_digit] = (f"KEY_{_digit}", False)
    _CHARS[_sym] = (f"KEY_{_digit}", True)
for _ch, _name in {
    " ": "KEY_SPACE", "-": "KEY_MINUS", "=": "KEY_EQUAL", "[": "KEY_LEFTBRACE",
    "]": "KEY_RIGHTBRACE", "\\": "KEY_BACKSLASH", ";": "KEY_SEMICOLON",
    "'": "KEY_APOSTROPHE", "`": "KEY_GRAVE", ",": "KEY_COMMA", ".": "KEY_DOT",
    "/": "KEY_SLASH", "\n": "KEY_ENTER", "\t": "KEY_TAB",
}.items():
    _CHARS[_ch] = (_name, False)
for _ch, _name in {
    "_": "KEY_MINUS", "+": "KEY_EQUAL", "{": "KEY_LEFTBRACE", "}": "KEY_RIGHTBRACE",
    "|": "KEY_BACKSLASH", ":": "KEY_SEMICOLON", '"': "KEY_APOSTROPHE",
    "~": "KEY_GRAVE", "<": "KEY_COMMA", ">": "KEY_DOT", "?": "KEY_SLASH",
}.items():
    _CHARS[_ch] = (_name, True)


class UinputBackend(InputBackend):
    name = "uinput"

    def __init__(self) -> None:
        from evdev import AbsInfo, UInput
        from evdev import ecodes as e

        self._e = e
        keycodes = {getattr(e, n) for n in _NAMED.values() if hasattr(e, n)}
        keycodes |= {getattr(e, n) for n, _ in _CHARS.values()}
        buttons = [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]

        # A relative device for pointer motion, buttons, wheel and keys...
        self._dev = UInput(
            {
                e.EV_KEY: sorted(keycodes | set(buttons)),
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
            },
            name="webmouse virtual input",
        )
        # ...and a separate absolute device, like a graphics tablet.
        info = AbsInfo(value=0, min=0, max=_ABS_MAX, fuzz=0, flat=0, resolution=0)
        self._tablet = UInput(
            {
                e.EV_KEY: [e.BTN_LEFT],
                e.EV_ABS: [(e.ABS_X, info), (e.ABS_Y, info)],
            },
            name="webmouse virtual tablet",
        )
        self._buttons = {
            "left": e.BTN_LEFT,
            "right": e.BTN_RIGHT,
            "middle": e.BTN_MIDDLE,
        }

    # --- pointer -----------------------------------------------------------
    def move_rel(self, dx: int, dy: int) -> None:
        e = self._e
        if dx:
            self._dev.write(e.EV_REL, e.REL_X, dx)
        if dy:
            self._dev.write(e.EV_REL, e.REL_Y, dy)
        self._dev.syn()

    def move_abs(
        self,
        fx: float,
        fy: float,
        geom: Optional[Geometry],
        virtual: Optional[Geometry],
    ) -> None:
        e = self._e
        if geom and virtual and virtual[2] > 0 and virtual[3] > 0:
            gx = (geom[0] + fx * geom[2] - virtual[0]) / virtual[2]
            gy = (geom[1] + fy * geom[3] - virtual[1]) / virtual[3]
        else:
            gx, gy = fx, fy
        x = round(min(max(gx, 0.0), 1.0) * _ABS_MAX)
        y = round(min(max(gy, 0.0), 1.0) * _ABS_MAX)
        self._tablet.write(e.EV_ABS, e.ABS_X, x)
        self._tablet.write(e.EV_ABS, e.ABS_Y, y)
        self._tablet.syn()

    def button(self, name: str, down: bool) -> None:
        self._dev.write(self._e.EV_KEY, self._buttons[name], 1 if down else 0)
        self._dev.syn()

    def scroll(self, dx: int, dy: int) -> None:
        e = self._e
        if dy:
            self._dev.write(e.EV_REL, e.REL_WHEEL, dy)
        if dx:
            self._dev.write(e.EV_REL, e.REL_HWHEEL, dx)
        self._dev.syn()

    # --- keyboard ----------------------------------------------------------
    def _code(self, name: str, *, shifted: bool = False) -> int:
        if name in _NAMED:
            return getattr(self._e, _NAMED[name])
        if len(name) == 1 and name.lower() in _CHARS:
            return getattr(self._e, _CHARS[name.lower()][0])
        raise ValueError(f"unknown key {name!r}")

    def key(self, name: str, down: bool) -> None:
        self._dev.write(self._e.EV_KEY, self._code(name), 1 if down else 0)
        self._dev.syn()

    def type_text(self, text: str) -> None:
        e = self._e
        for ch in text:
            entry = _CHARS.get(ch)
            if entry is None:
                continue  # uinput cannot type non-US characters
            name, shift = entry
            code = getattr(e, name)
            if shift:
                self._dev.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            self._dev.write(e.EV_KEY, code, 1)
            self._dev.write(e.EV_KEY, code, 0)
            if shift:
                self._dev.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            self._dev.syn()

    def close(self) -> None:
        for dev in (self._dev, self._tablet):
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass
