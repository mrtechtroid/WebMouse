"""A single thread that owns the input backend.

Injection calls can block (X11 round trips, key-by-key typing), so they never
run on the event loop. One thread also keeps events strictly ordered and means
backends need no locking.
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable

log = logging.getLogger("webmouse.input")


class InputWorker:
    def __init__(self) -> None:
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._errors = 0
        self._thread = threading.Thread(target=self._run, name="webmouse-input", daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        """Run ``fn`` on the worker thread, fire and forget."""
        self._queue.put((fn, args, None))

    def call(self, fn: Callable[..., Any], *args: Any, timeout: float = 1.0) -> Any:
        """Run ``fn`` on the worker thread and wait for the result."""
        future: Future = Future()
        self._queue.put((fn, args, future))
        return future.result(timeout)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, args, future = item
            try:
                result = fn(*args)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                self._errors += 1
                if self._errors <= 3 or self._errors % 200 == 0:
                    log.warning("input error (%d so far): %s", self._errors, exc)
                if future is not None:
                    future.set_exception(exc)
            else:
                if future is not None:
                    future.set_result(result)
