from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest

from gateway.run import GatewayRunner


class _BlockingSessionDB:
    def __init__(self) -> None:
        self.started = threading.Event()

    def list_pending_handoffs(self):
        self.started.set()
        time.sleep(0.25)
        return []


@pytest.mark.asyncio
async def test_handoff_watcher_does_not_block_gateway_loop():
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._session_db = _BlockingSessionDB()

    task = asyncio.create_task(
        runner._handoff_watcher(interval=0.01, initial_delay=0)
    )
    try:
        assert await asyncio.to_thread(runner._session_db.started.wait, 1.0)

        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(0.01)

        assert loop.time() - start < 0.1
    finally:
        runner._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
