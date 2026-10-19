"""Async Token Factory client (OpenAI-compatible) with retries, Retry-After, cost accounting."""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from upshift.core.budget import Budget

from .models import ModelSpec

log = logging.getLogger(__name__)

RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.APIConnectionError,  # includes APITimeoutError
    openai.InternalServerError,  # any 5xx
)


@dataclass
class LLMUsage:
    model_key: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    agent: str
    run_id: str | None
    attempt: int
    retries: int
    budget_thresholds_crossed: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


UsageHook = Callable[[LLMUsage], Awaitable[None] | None]
SleepFn = Callable[[float], Awaitable[None]]


class TokenFactoryClient:
    """SDK retries are disabled so backoff, Retry-After and accounting live here and are testable."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 4,
        usage_hook: UsageHook | None = None,
        client: Any | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
        )
        self._sleep: SleepFn = sleep or asyncio.sleep
        self.max_retries = max_retries
        self.usage_hook = usage_hook
        self.ledger: list[LLMUsage] = []

    @property
    def total_cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.ledger)

    async def complete(
        self,
        spec: ModelSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        agent: str = "system",
        run_id: str | None = None,
        attempt: int = 1,
        budget: Budget | None = None,
        **extra: Any,
    ) -> ChatCompletion:
        kwargs: dict[str, Any] = {"model": spec.id, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs.update(extra)

        retries = 0
        started = time.perf_counter()
        while True:
            try:
                response: ChatCompletion = await self._client.chat.completions.create(**kwargs)
                break
            except RETRYABLE_ERRORS as exc:
                if retries >= self.max_retries:
                    raise
                delay = self._backoff(retries, exc)
                retries += 1
                log.warning(
                    "token factory %s on %s; retry %d/%d in %.2fs",
                    type(exc).__name__, spec.id, retries, self.max_retries, delay,
                )
                await self._sleep(delay)
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        cost = spec.cost(prompt_tokens, completion_tokens)
        crossed = budget.spend(cost) if budget is not None else []

        record = LLMUsage(
            model_key=spec.key,
            model_id=spec.id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            agent=agent,
            run_id=run_id,
            attempt=attempt,
            retries=retries,
            budget_thresholds_crossed=crossed,
        )
        self.ledger.append(record)
        if self.usage_hook is not None:
            maybe = self.usage_hook(record)
            if inspect.isawaitable(maybe):
                await maybe
        return response

    @staticmethod
    def _backoff(retry_index: int, exc: Exception) -> float:
        base = min(0.5 * (2**retry_index), 20.0)
        delay = base + random.uniform(0, base * 0.5)
        response = getattr(exc, "response", None)
        header = response.headers.get("retry-after") if response is not None else None
        if header:
            try:
                delay = max(delay, float(header))
            except ValueError:
                pass
        return delay


def content_of(response: ChatCompletion) -> str:
    """Text content of the first choice, or empty string."""
    if not response.choices:
        return ""
    return response.choices[0].message.content or ""
