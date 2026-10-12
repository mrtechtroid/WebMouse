"""Screen capture and MJPEG fan-out.

One capture task runs per distinct (monitor, quality, scale, fps) combination,
only while at least one viewer is connected. Frames are produced on a dedicated
thread because mss handles are thread-affine.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional, Tuple

from .config import StreamConfig
from .screens import Screens

log = logging.getLogger("webmouse.stream")

PositionFn = Callable[[], Optional[Tuple[int, int]]]


KEEPALIVE_SECONDS = 3.0


class StreamError(Exception):
    pass


@dataclass(frozen=True)
class StreamKey:
    monitor: int
    quality: int
    scale: float
    fps: int


class _Channel:
    def __init__(self) -> None:
        self.frame = b""
        self.seq = 0
        self.error: Optional[str] = None
        self.viewers = 0
        self.task: Optional[asyncio.Task] = None
        self.cond = asyncio.Condition()


class StreamHub:
    def __init__(self, cfg: StreamConfig, screens: Screens, position: PositionFn) -> None:
        self.cfg = cfg
        self._screens = screens
        self._position = position
        self._executor = ThreadPoolExecutor(1, thread_name_prefix="webmouse-capture")
        self._local = threading.local()
        self._channels: dict[StreamKey, _Channel] = {}
        self.available = False
        self.reason = "not probed yet"

    # --- capability probe --------------------------------------------------
    async def probe(self) -> None:
        if not self.cfg.enabled:
            self.reason = "disabled in config"
            return
        try:
            key = StreamKey(self._screens.default_index(), 40, 0.1, 1)
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._grab, key, None
            )
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.reason = str(exc) or exc.__class__.__name__
            log.warning("screen streaming unavailable: %s", self.reason)
        else:
            self.available = True
            self.reason = ""

    # --- viewer API --------------------------------------------------------
    async def frames(self, key: StreamKey) -> AsyncIterator[bytes]:
        channel = self._channels.get(key)
        if channel is None:
            channel = self._channels[key] = _Channel()
        channel.viewers += 1
        if channel.task is None or channel.task.done():
            channel.error = None
            channel.task = asyncio.create_task(self._run(key, channel))
        last = 0
        try:
            while True:
                try:
                    async with channel.cond:
                        await asyncio.wait_for(
                            channel.cond.wait_for(
                                lambda: channel.seq != last or channel.error is not None
                            ),
                            KEEPALIVE_SECONDS,
                        )
                        if channel.error is not None and channel.seq == last:
                            raise StreamError(channel.error)
                        last = channel.seq
                        frame = channel.frame
                except asyncio.TimeoutError:
                    # A static screen produces no new frames. Re-send the last one
                    # so a disconnected viewer is noticed (the write fails).
                    frame = channel.frame
                    if not frame:
                        continue
                yield frame
        finally:
            channel.viewers -= 1
            if channel.viewers <= 0:
                if channel.task is not None:
                    channel.task.cancel()
                self._channels.pop(key, None)

    async def _run(self, key: StreamKey, channel: _Channel) -> None:
        loop = asyncio.get_running_loop()
        period = 1.0 / key.fps
        signature = None
        while True:
            started = loop.time()
            try:
                frame, new_sig = await loop.run_in_executor(
                    self._executor, self._grab, key, signature
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("capture failed: %s", exc)
                async with channel.cond:
                    channel.error = str(exc) or exc.__class__.__name__
                    channel.cond.notify_all()
                return
            if frame is not None:
                signature = new_sig
                async with channel.cond:
                    channel.frame = frame
                    channel.seq += 1
                    channel.cond.notify_all()
            await asyncio.sleep(max(0.0, period - (loop.time() - started)))

    # --- capture thread ----------------------------------------------------
    def _grab(self, key: StreamKey, previous) -> tuple[Optional[bytes], object]:
        import mss
        from PIL import Image, ImageDraw

        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = mss.mss()
        monitor = sct.monitors[key.monitor]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if key.scale < 0.999:
            size = (max(1, round(img.width * key.scale)), max(1, round(img.height * key.scale)))
            img = img.resize(size, Image.BILINEAR)

        cursor = None
        if self.cfg.draw_cursor:
            try:
                pos = self._position()
            except Exception:  # noqa: BLE001
                pos = None
            if pos is not None and monitor["width"] and monitor["height"]:
                fx = (pos[0] - monitor["left"]) / monitor["width"]
                fy = (pos[1] - monitor["top"]) / monitor["height"]
                if 0 <= fx <= 1 and 0 <= fy <= 1:
                    cursor = (round(fx * img.width), round(fy * img.height))

        signature = (zlib.adler32(img.tobytes()), cursor)
        if signature == previous:
            return None, previous  # nothing changed, skip encoding

        if cursor is not None:
            s = max(1.0, img.width / 900)
            pts = [(0, 0), (0, 16), (4, 12), (7, 19), (10, 18), (7, 11), (12, 11)]
            pts = [(cursor[0] + x * s, cursor[1] + y * s) for x, y in pts]
            ImageDraw.Draw(img).polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))

        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=key.quality)
        return buf.getvalue(), signature

    def close(self) -> None:
        for channel in list(self._channels.values()):
            if channel.task is not None:
                channel.task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
