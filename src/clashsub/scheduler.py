import asyncio
import logging


class RefreshScheduler:
    def __init__(self, refresher, delay_seconds):
        self.refresher = refresher
        self.delay_seconds = delay_seconds
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._logger = logging.getLogger("clashsub.scheduler")

    def reschedule(self):
        self._wake.set()

    async def run(self):
        while not self._stop.is_set():
            try:
                await self.refresher.refresh()
            except Exception as exc:
                self._logger.error("scheduled refresh failed: %s", type(exc).__name__)
            if self._stop.is_set():
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.delay_seconds())
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self._stop.set()
        self._wake.set()
