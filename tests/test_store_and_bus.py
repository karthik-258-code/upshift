from __future__ import annotations

import asyncio

from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType, Run, RunStatus
from upshift.core.store import EventStore


def test_append_assigns_sequence_per_run() -> None:
    store = EventStore()
    run = store.create_run(Run(repo_url="https://example.test/a"))
    other = store.create_run(Run(repo_url="https://example.test/b"))
    e1 = store.append(Event(run_id=run.id, type=EventType.RUN_STARTED))
    e2 = store.append(Event(run_id=run.id, type=EventType.AGENT_MESSAGE, payload={"m": 1}))
    o1 = store.append(Event(run_id=other.id, type=EventType.RUN_STARTED))
    assert (e1.seq, e2.seq, o1.seq) == (1, 2, 1)
    assert [e.seq for e in store.events(run.id)] == [1, 2]
    assert store.events(run.id, after_seq=1)[0].payload == {"m": 1}
    assert store.get_run("missing") is None
    assert {r.id for r in store.list_runs()} == {run.id, other.id}


def test_set_status_merges_summary() -> None:
    store = EventStore()
    run = store.create_run(Run(repo_url="x"))
    store.set_status(run.id, RunStatus.RUNNING)
    updated = store.set_status(run.id, RunStatus.SUCCEEDED, mode="noop")
    assert updated is not None
    assert updated.status is RunStatus.SUCCEEDED and updated.summary == {"mode": "noop"}


async def test_stream_replays_then_live_then_terminates() -> None:
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    await bus.publish(Event(run_id=run.id, type=EventType.RUN_STARTED))

    received: list[EventType] = []

    async def consume() -> None:
        async for event in bus.stream(run.id, keepalive_seconds=0.05):
            if event is not None:
                received.append(event.type)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.15)  # replay done, at least one keepalive tick elapsed
    await bus.publish(Event(run_id=run.id, type=EventType.AGENT_MESSAGE))
    await bus.publish(Event(run_id=run.id, type=EventType.RUN_FINISHED))
    await asyncio.wait_for(task, timeout=2)

    assert received == [EventType.RUN_STARTED, EventType.AGENT_MESSAGE, EventType.RUN_FINISHED]
    assert not bus._subscribers


async def test_stream_of_finished_run_does_not_hang() -> None:
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    await bus.publish(Event(run_id=run.id, type=EventType.RUN_STARTED))
    await bus.publish(Event(run_id=run.id, type=EventType.RUN_FINISHED))
    store.set_status(run.id, RunStatus.SUCCEEDED)

    events = [e async for e in bus.stream(run.id)]
    assert [e.type for e in events if e] == [EventType.RUN_STARTED, EventType.RUN_FINISHED]

    resumed = [e async for e in bus.stream(run.id, after_seq=2)]
    assert resumed == []
