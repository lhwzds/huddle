"""In-process fan-out of committed events."""
import asyncio
import copy
from collections import defaultdict

class Bus:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, conv_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._subscribers[conv_id].add(queue)
        return queue

    def unsubscribe(self, conv_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(conv_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(conv_id, None)

    def publish(self, conv_id: str, event: dict) -> None:
        for queue in tuple(self._subscribers.get(conv_id, ())):
            queue.put_nowait(copy.deepcopy(event))
