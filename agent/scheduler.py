import asyncio
import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


class Scheduler:
    """Runs a blocking task_fn on a fixed interval without blocking the
    asyncio event loop. Each cycle runs in a worker thread (via
    asyncio.to_thread) since the monitoring cycle uses synchronous httpx/
    Kubernetes client calls and can sleep for a while during verification.
    """

    def __init__(self, interval_seconds: float, task_fn: Callable[[], None]) -> None:
        self._interval = interval_seconds
        self._task_fn = task_fn
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            # Waits out any in-flight cycle (bounded by VERIFY_TIMEOUT_SECONDS)
            # rather than abandoning it mid-remediation.
            await self._task

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            start = time.monotonic()
            try:
                await asyncio.to_thread(self._task_fn)
            except Exception:
                logger.exception("Monitoring cycle failed")

            elapsed = time.monotonic() - start
            remaining = max(0.0, self._interval - elapsed)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
