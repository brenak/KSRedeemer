import asyncio


class AddQueue:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._processing = False

    def start(self):
        asyncio.create_task(self._worker())

    def position(self) -> int:
        """Items ahead of a new request (queue depth + 1 if currently processing)."""
        return self._queue.qsize() + (1 if self._processing else 0)

    async def enqueue(self, coro) -> None:
        await self._queue.put(coro)

    async def _worker(self):
        while True:
            coro = await self._queue.get()
            self._processing = True
            try:
                await coro
            except Exception as e:
                print(f"❌ AddQueue worker error: {e}")
            finally:
                self._processing = False
                self._queue.task_done()
