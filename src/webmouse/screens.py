"""Monitor geometry, cached so input handling never touches the screen API."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("webmouse.screens")


@dataclass(frozen=True)
class Monitor:
    index: int  # 0 = the whole virtual desktop, 1.. = individual monitors
    left: int
    top: int
    width: int
    height: int

    @property
    def geometry(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


class Screens:
    def __init__(self) -> None:
        self._monitors: list[Monitor] = []
        self._loaded_at = 0.0
        self._warned = False

    @staticmethod
    def _load() -> list[Monitor]:
        import mss

        with mss.mss() as sct:
            return [
                Monitor(i, m["left"], m["top"], m["width"], m["height"])
                for i, m in enumerate(sct.monitors)
            ]

    async def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._loaded_at < 5:
            return
        try:
            self._monitors = await asyncio.to_thread(self._load)
        except Exception as exc:  # noqa: BLE001
            self._monitors = []
            if not self._warned:
                log.warning("cannot read monitor layout (absolute mode and streaming "
                            "will be limited): %s", exc)
                self._warned = True
        self._loaded_at = time.monotonic()

    def physical(self) -> list[Monitor]:
        return [m for m in self._monitors if m.index >= 1]

    def get(self, index: int) -> Optional[Monitor]:
        for m in self._monitors:
            if m.index == index:
                return m
        return None

    def virtual(self) -> Optional[Monitor]:
        return self.get(0)

    def default_index(self) -> int:
        return 1 if self.get(1) else 0
