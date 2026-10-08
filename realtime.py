"""In-process pub/sub for pushing live case events to connected dashboards.

Subscribers are Server-Sent Event streams. Events are not persisted here; the
database is the source of truth, and clients re-fetch it whenever their stream
(re)connects. This broker lives in one process, so run the server with a single
worker (the default for `python3 main.py`).
"""
import asyncio
import itertools
import json
from typing import Callable, Optional, Set

# A slow client that falls this far behind is disconnected and re-syncs on reconnect.
MAX_QUEUED_EVENTS = 500


class Subscription:
    def __init__(self, accepts: Callable[[Optional[int], Optional[int]], bool]):
        self.accepts = accepts
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)


class EventBroker:
    def __init__(self):
        self._subscribers: Set[Subscription] = set()
        self._ids = itertools.count(1)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, accepts: Callable[[Optional[int], Optional[int]], bool]) -> Subscription:
        """
        accepts(owner_id, hospital_id) decides whether this subscriber may see an event for a case
        owned by that EMT and routed to that hospital.
        """
        sub = Subscription(accepts)
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)

    def publish(self, event_type: str, data: dict, owner_id: Optional[int] = None,
                hospital_id: Optional[int] = None) -> None:
        """Queue an event for every subscriber allowed to see it. Must be called on the event loop."""
        message = format_sse(event_type, data, event_id=next(self._ids))
        for sub in list(self._subscribers):
            if not sub.accepts(owner_id, hospital_id):
                continue
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                # None tells the stream to close; the client reconnects and re-fetches.
                self._subscribers.discard(sub)
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                sub.queue.put_nowait(None)


def format_sse(event_type: str, data: dict, event_id: Optional[int] = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


broker = EventBroker()
