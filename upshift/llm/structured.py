"""Helpers for schema-constrained model output."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from upshift.core.budget import Budget

from .client import TokenFactoryClient, content_of
from .models import ModelSpec

log = logging.getLogger(__name__)

FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def json_schema_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-style strict JSON Schema response_format (verified on all Nemotron tiers)."""
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def parse_json(text: str) -> Any:
    """Parse model output as JSON, tolerating code fences and leading/trailing prose."""
    cleaned = FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
    ends = [i for i in (cleaned.rfind("}"), cleaned.rfind("]")) if i >= 0]
    if not starts or not ends:
        raise ValueError("no JSON object found in model output")
    return json.loads(cleaned[min(starts) : max(ends) + 1])


async def complete_json(
    llm: TokenFactoryClient,
    model: ModelSpec,
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    schema: dict[str, Any],
    max_tokens: int,
    agent: str,
    run_id: str | None = None,
    budget: Budget | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Schema-constrained completion with one json_object retry when the output is unparsable."""
    response = await llm.complete(
        model, messages, response_format=json_schema_format(schema_name, schema),
        temperature=0.0, max_tokens=max_tokens, agent=agent, run_id=run_id,
        budget=budget, attempt=attempt,
    )
    text = content_of(response)
    try:
        data = parse_json(text)
    except ValueError as exc:
        finish = response.choices[0].finish_reason if response.choices else None
        log.warning(
            "%s: unparsable output (%s); finish_reason=%s chars=%d; retrying with json_object",
            agent, exc, finish, len(text),
        )
        retry = [
            *messages,
            {"role": "assistant", "content": text or "(empty)"},
            {"role": "user", "content": f"That was not valid JSON ({exc}). Return only JSON."},
        ]
        response = await llm.complete(
            model, retry, response_format={"type": "json_object"}, temperature=0.0,
            max_tokens=max_tokens, agent=agent, run_id=run_id, budget=budget, attempt=attempt,
        )
        data = parse_json(content_of(response))
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data
