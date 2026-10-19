"""Helpers for schema-constrained model output."""

from __future__ import annotations

import json
import re
from typing import Any

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
