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
            # 在 refresh 之前清空唤醒信号：若 refresh 期间收到 reschedule()
            # （例如设置变更触发立即刷新），保留信号并在刷新后直接进入下一轮，
            # 而不是被清掉后继续休眠整个间隔。
            self._wake.clear()
            try:
                await self.refresher.refresh()
            except Exception as exc:
                self._logger.error("scheduled refresh failed: %s", type(exc).__name__)
            if self._stop.is_set():
                break
            if self._wake.is_set():
                continue
            try:
                delay = self.delay_seconds()
            except Exception as exc:
                self._logger.error("scheduler delay computation failed: %s", type(exc).__name__)
                delay = 3600
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self._stop.set()
        self._wake.set()
