"""Persist events through the store and fan them out to live SSE subscribers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

from .events import TERMINAL_EVENTS, TERMINAL_STATUSES, Event
from .store import EventStore


class EventBus:
    def __init__(self, store: EventStore) -> None:
        self.store = store
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    async def publish(self, event: Event) -> Event:
        stored = self.store.append(event)
        for queue in list(self._subscribers.get(stored.run_id, ())):
            queue.put_nowait(stored)
        return stored

    def _subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        return queue

    def _unsubscribe(self, run_id: str, queue: asyncio.Queue[Event]) -> None:
        subs = self._subscribers.get(run_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(run_id, None)

    async def stream(
        self, run_id: str, *, after_seq: int = 0, keepalive_seconds: float = 15.0
    ) -> AsyncIterator[Event | None]:
        """Replay stored events, then yield live ones.

        Yields None as a keepalive tick. Ends after a terminal event, or immediately if
        the run is already finished and nothing new is pending.
        """
        queue = self._subscribe(run_id)
        try:
            last_seq = after_seq
            for event in self.store.events(run_id, after_seq=after_seq):
                last_seq = event.seq
                yield event
                if event.type in TERMINAL_EVENTS:
                    return
            run = self.store.get_run(run_id)
            if run is not None and run.status in TERMINAL_STATUSES and queue.empty():
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                except TimeoutError:
                    yield None
                    continue
                if event.seq <= last_seq:
                    continue
                last_seq = event.seq
                yield event
                if event.type in TERMINAL_EVENTS:
                    return
        finally:
            self._unsubscribe(run_id, queue)
