"""
In-memory SSE event bus for workflow runs.

Extracted from the engine (roadmap §3 target structure: ``app/workflow/bus.py``)
so node families that must publish run-lifecycle events — the human-in-the-loop
waits in ``nodes/hitl.py`` flip a run to ``waiting``/``running`` — can do so
without importing the engine and creating an import cycle.

This is the DEFAULT single-process implementation only. The pluggable
``EventBus`` interface with a distributed (Redis pub/sub) backend for
multi-instance deployments is a separate roadmap item (§4.3) and lands later;
behaviour here is byte-for-byte the engine's former bus, so nothing observable
changes in single-node.
"""

from __future__ import annotations

import asyncio

# Live SSE subscribers, keyed by run id.
_subscribers: dict[str, list[asyncio.Queue]] = {}


def subscribe(run_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(queue)
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue) -> None:
    queues = _subscribers.get(run_id)
    if not queues:
        return
    if queue in queues:
        queues.remove(queue)
    if not queues:
        _subscribers.pop(run_id, None)


def publish(run_id: str, event: dict) -> None:
    for queue in _subscribers.get(run_id, []):
        queue.put_nowait(event)
