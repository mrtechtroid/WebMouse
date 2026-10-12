"""Backend interface shared by every input implementation."""

from __future__ import annotations

import logging
from typing import Optional, Sequence, Tuple

log = logging.getLogger("webmouse.input")

# (left, top, width, height) in the backend's coordinate space.
Geometry = Tuple[int, int, int, int]

MODIFIERS = ("ctrl", "alt", "shift", "meta")


class BackendError(RuntimeError):
    """Raised when no input backend can be created."""


class InputBackend:
    """Everything the server needs to drive the host's mouse and keyboard.

    Implementations are only ever called from one worker thread, so they do
    not need to be thread safe.
    """

    name = "base"

    # --- pointer -----------------------------------------------------------
    def move_rel(self, dx: int, dy: int) -> None:
        raise NotImplementedError

    def move_abs(
        self,
        fx: float,
        fy: float,
        geom: Optional[Geometry],
        virtual: Optional[Geometry],
    ) -> None:
        """Move to a fractional position (0..1) inside ``geom``.

        ``virtual`` is the bounding box of the whole desktop.
        """
        raise NotImplementedError

    def button(self, name: str, down: bool) -> None:
        """``name`` is ``left``, ``right`` or ``middle``."""
        raise NotImplementedError

    def scroll(self, dx: int, dy: int) -> None:
        """Positive ``dy`` scrolls up, positive ``dx`` scrolls right."""
        raise NotImplementedError

    def position(self) -> Optional[Tuple[int, int]]:
        """Current cursor position, if the backend can tell."""
        return None

    # --- keyboard ----------------------------------------------------------
    def key(self, name: str, down: bool) -> None:
        raise NotImplementedError

    def type_text(self, text: str) -> None:
        raise NotImplementedError

    # --- helpers built on the primitives above ------------------------------
    def click(self, name: str, count: int = 1) -> None:
        for _ in range(count):
            self.button(name, True)
            self.button(name, False)

    def tap_key(self, name: str, mods: Sequence[str] = (), count: int = 1) -> None:
        for _ in range(count):
            for m in mods:
                self.key(m, True)
            try:
                self.key(name, True)
                self.key(name, False)
            finally:
                for m in reversed(mods):
                    self.key(m, False)

    def zoom(self, ticks: int) -> None:
        """Ctrl + wheel, which most applications treat as zoom."""
        self.key("ctrl", True)
        try:
            self.scroll(0, ticks)
        finally:
            self.key("ctrl", False)

    def close(self) -> None:
        pass


class DummyBackend(InputBackend):
    """Records events instead of injecting them. Used for tests and --backend dummy."""

    name = "dummy"

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.pos: Tuple[int, int] = (0, 0)

    def _rec(self, *event) -> None:
        self.events.append(event)
        log.debug("dummy: %s", event)

    def move_rel(self, dx, dy):
        self.pos = (self.pos[0] + dx, self.pos[1] + dy)
        self._rec("move_rel", dx, dy)

    def move_abs(self, fx, fy, geom, virtual):
        if geom:
            self.pos = (round(geom[0] + fx * geom[2]), round(geom[1] + fy * geom[3]))
        self._rec("move_abs", round(fx, 4), round(fy, 4))

    def button(self, name, down):
        self._rec("button", name, down)

    def scroll(self, dx, dy):
        self._rec("scroll", dx, dy)

    def position(self):
        return self.pos

    def key(self, name, down):
        self._rec("key", name, down)

    def type_text(self, text):
        self._rec("type", text)
