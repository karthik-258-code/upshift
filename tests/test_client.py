from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from upshift.core.budget import Budget
from upshift.llm.client import LLMUsage, TokenFactoryClient, content_of
from upshift.llm.models import ModelCatalog


def _completion() -> ChatCompletion:
    return ChatCompletion(
        id="cmpl-test",
        object="chat.completion",
        created=0,
        model="test",
        choices=[
            Choice(
                index=0,
                finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content="ok"),
            )
        ],
        usage=CompletionUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
    )


def _rate_limit() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(429, request=request, headers={"retry-after": "0"})
    return openai.RateLimitError("slow down", response=response, body=None)


class _FakeCompletions:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls += 1
        self.last_kwargs = kwargs
        if self.failures:
            raise self.failures.pop(0)
        return _completion()


def _fake_client(failures: list[Exception]) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(failures)))


async def test_retries_then_succeeds_and_accounts_cost(catalog: ModelCatalog) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    seen: list[LLMUsage] = []
    fake = _fake_client([_rate_limit(), _rate_limit()])
    client = TokenFactoryClient(
        "key", "https://example.test/v1/", client=fake, max_retries=3,
        usage_hook=seen.append, sleep=fake_sleep,
    )
    budget = Budget(cap_usd=1.0)

    response = await client.complete(
        catalog.models["super"], [{"role": "user", "content": "hi"}],
        agent="surgeon", run_id="r1", budget=budget,
    )

    assert content_of(response) == "ok"
    assert fake.chat.completions.calls == 3
    assert len(sleeps) == 2
    usage = seen[0]
    expected = 1000 / 1e6 * 0.30 + 500 / 1e6 * 0.90
    assert usage.cost_usd == pytest.approx(expected)
    assert budget.spent_usd == pytest.approx(expected)
    assert usage.retries == 2 and usage.agent == "surgeon" and usage.run_id == "r1"
    assert client.total_cost_usd == pytest.approx(expected)


async def test_gives_up_after_max_retries(catalog: ModelCatalog) -> None:
    async def fake_sleep(_: float) -> None:
        return None

    fake = _fake_client([_rate_limit(), _rate_limit(), _rate_limit()])
    client = TokenFactoryClient("k", "https://example.test/v1/", client=fake, max_retries=2, sleep=fake_sleep)
    with pytest.raises(openai.RateLimitError):
        await client.complete(catalog.models["nano"], [{"role": "user", "content": "hi"}])
    assert fake.chat.completions.calls == 3


async def test_optional_arguments_are_forwarded(catalog: ModelCatalog) -> None:
    fake = _fake_client([])
    client = TokenFactoryClient("k", "https://example.test/v1/", client=fake)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]
    await client.complete(
        catalog.models["nano"], [{"role": "user", "content": "hi"}],
        tools=tools, response_format={"type": "json_object"}, max_tokens=64,
    )
    sent = fake.chat.completions.last_kwargs
    assert sent["model"] == catalog.models["nano"].id
    assert sent["tools"] == tools
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["max_tokens"] == 64
