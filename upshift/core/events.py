"""Event model. Everything Upshift does is an append-only event that the UI can replay."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    RUN_FAILED = "run.failed"
    DEP_DISCOVERED = "dep.discovered"
    DEP_SKIPPED = "dep.skipped"
    EVIDENCE_FETCHED = "evidence.fetched"
    DOSSIER_READY = "dossier.ready"
    PATCH_PROPOSED = "patch.proposed"
    PATCH_REJECTED = "patch.rejected"
    SANDBOX_STARTED = "sandbox.started"
    SANDBOX_FINISHED = "sandbox.finished"
    TEST_RESULT = "test.result"
    ESCALATION = "escalation"
    REVIEW_VERDICT = "review.verdict"
    MR_OPENED = "mr.opened"
    LLM_CALL = "llm.call"
    TOOL_CALL = "tool.call"
    BUDGET_WARNING = "budget.warning"
    AGENT_MESSAGE = "agent.message"


TERMINAL_EVENTS = frozenset({EventType.RUN_FINISHED, EventType.RUN_FAILED})


def utcnow() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    run_id: str
    seq: int = 0
    ts: datetime = Field(default_factory=utcnow)
    type: EventType
    agent: str = "system"
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        return f"id: {self.seq}\nevent: {self.type.value}\ndata: {self.model_dump_json()}\n\n"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


class Run(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    repo_url: str
    policy: str = "balanced"
    budget_cap_usd: float = 2.0
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    spent_usd: float = 0.0
    summary: dict[str, Any] = Field(default_factory=dict)
