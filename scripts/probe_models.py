#!/usr/bin/env python
"""Probe each catalog model on Token Factory for the capabilities Upshift relies on.

Writes config/capabilities.json, which the router then treats as the truth. Each probe
uses tiny prompts; a full run costs a few cents at most. Requires NEBIUS_API_KEY.

    python scripts/probe_models.py               # all models
    python scripts/probe_models.py --only nano    # one model, merged into the existing file
    python scripts/probe_models.py --list         # list model IDs visible to this account
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upshift.config import get_settings  # noqa: E402
from upshift.llm.models import CAPABILITIES, ModelCatalog  # noqa: E402

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

JSON_SCHEMA = {
    "name": "probe_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"status": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["status", "count"],
        "additionalProperties": False,
    },
}


def _chat(client: OpenAI, model: str, **kwargs: Any) -> Any:
    return client.chat.completions.create(model=model, temperature=0, max_tokens=512, **kwargs)


def probe_chat(client: OpenAI, model: str) -> bool:
    r = _chat(client, model, messages=[{"role": "user", "content": "Reply with the word: ready"}])
    return bool(r.choices)


def probe_tool_calling(client: OpenAI, model: str) -> bool:
    r = _chat(
        client,
        model,
        messages=[{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
        tools=[WEATHER_TOOL],
        tool_choice="auto",
    )
    calls = r.choices[0].message.tool_calls or []
    return bool(calls) and "city" in json.loads(calls[0].function.arguments)


def probe_parallel_tool_calls(client: OpenAI, model: str) -> bool:
    messages = [
        {
            "role": "user",
            "content": "Call get_weather once for Paris and once for Tokyo in the same response.",
        }
    ]
    try:
        r = _chat(client, model, messages=messages, tools=[WEATHER_TOOL], parallel_tool_calls=True)
    except Exception:  # noqa: BLE001 - some servers reject the flag; retry without it
        r = _chat(client, model, messages=messages, tools=[WEATHER_TOOL])
    return len(r.choices[0].message.tool_calls or []) >= 2


def probe_json_object(client: OpenAI, model: str) -> bool:
    r = _chat(
        client,
        model,
        messages=[
            {
                "role": "user",
                "content": (
                    'Return a JSON object with keys "status" ("ok") and "count" (3). '
                    "Output JSON only."
                ),
            }
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(r.choices[0].message.content or "")
    return isinstance(data, dict) and {"status", "count"} <= set(data)


def probe_json_schema(client: OpenAI, model: str) -> bool:
    r = _chat(
        client,
        model,
        messages=[{"role": "user", "content": "Report status ok and count 3."}],
        response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
    )
    data = json.loads(r.choices[0].message.content or "")
    return isinstance(data.get("status"), str) and isinstance(data.get("count"), int)


def _thinks(client: OpenAI, model: str, messages: list[dict[str, Any]], **extra: Any) -> bool:
    """True when the model spends tokens on hidden reasoning before answering."""
    r = client.chat.completions.create(
        model=model, messages=messages, max_tokens=24, temperature=0, **extra
    )
    message = r.choices[0].message
    extras = getattr(message, "model_extra", None) or {}
    reasoning = extras.get("reasoning_content") or extras.get("reasoning") or ""
    content = (message.content or "").strip()
    return bool(reasoning) or (r.choices[0].finish_reason == "length" and not content)


def probe_thinking(client: OpenAI, model: str) -> dict[str, Any]:
    """Detect hidden reasoning and find a switch that turns it off."""
    task = [{"role": "user", "content": "What is 17 * 23? Reply with just the number."}]
    detected = _thinks(client, model, task)
    control = "none"
    notes = ""
    if detected:
        candidates: list[tuple[str, dict[str, Any]]] = [
            ("chat_template_kwargs", {
                "messages": task,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }),
            ("no_think", {"messages": [{"role": "system", "content": "/no_think"}, *task]}),
            ("detailed_thinking_off", {
                "messages": [{"role": "system", "content": "detailed thinking off"}, *task],
            }),
        ]
        control = "unknown"
        for name, kwargs in candidates:
            try:
                if not _thinks(client, model, **kwargs):
                    control = name
                    break
            except Exception as exc:  # noqa: BLE001
                notes += f"{name}: {type(exc).__name__}; "
    return {"detected": detected, "control": control, "notes": notes.strip()}


PROBES: dict[str, Callable[[OpenAI, str], bool]] = {
    "chat": probe_chat,
    "tool_calling": probe_tool_calling,
    "parallel_tool_calls": probe_parallel_tool_calls,
    "json_object": probe_json_object,
    "json_schema": probe_json_schema,
}
assert tuple(PROBES) == CAPABILITIES


def list_models(client: OpenAI, needle: str = "nemotron") -> int:
    print(f"Model IDs visible to this account containing '{needle}':")
    found = 0
    for model in client.models.list():
        if needle in model.id.lower():
            print(f"  {model.id}")
            found += 1
    if not found:
        print("  (none; try --needle with another substring)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated model keys (default: all)")
    parser.add_argument(
        "--out", type=Path, help="output path (default: settings.capabilities_file)"
    )
    parser.add_argument("--list", action="store_true", help="list available model IDs and exit")
    parser.add_argument("--needle", default="nemotron", help="substring filter for --list")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.nebius_api_key:
        print("NEBIUS_API_KEY is not set (put it in .env).", file=sys.stderr)
        return 2
    client = OpenAI(
        api_key=settings.nebius_api_key,
        base_url=settings.nebius_base_url,
        timeout=90,
        max_retries=1,
    )
    if args.list:
        return list_models(client, args.needle.lower())

    catalog = ModelCatalog.load(settings.models_file)
    keys = args.only.split(",") if args.only else list(catalog.models)

    results: dict[str, Any] = {}
    for key in keys:
        spec = catalog.models[key]
        flags: dict[str, Any] = {}
        notes: dict[str, str] = {}
        latency: dict[str, int] = {}
        print(f"\n== {key}: {spec.id}")
        for cap, probe in PROBES.items():
            if cap != "chat" and not flags.get("chat"):
                flags[cap] = False
                notes[cap] = "skipped: chat failed"
                continue
            started = time.perf_counter()
            try:
                ok = bool(probe(client, spec.id))
            except Exception as exc:  # noqa: BLE001
                ok = False
                notes[cap] = f"{type(exc).__name__}: {exc}"[:300]
            latency[cap] = int((time.perf_counter() - started) * 1000)
            flags[cap] = ok
            verdict = "PASS" if ok else "FAIL"
            print(f"  {cap:<20} {verdict:<4} {latency[cap]:>6} ms  {notes.get(cap, '')}")
        thinking: dict[str, Any] = {"detected": False, "control": "unknown", "notes": ""}
        if flags.get("chat"):
            try:
                thinking = probe_thinking(client, spec.id)
            except Exception as exc:  # noqa: BLE001
                thinking["notes"] = f"{type(exc).__name__}: {exc}"[:300]
        state = "ON" if thinking["detected"] else "off"
        print(
            f"  {'reasoning':<20} {state:<4}        switch: {thinking['control']}  "
            f"{thinking['notes']}"
        )
        results[spec.id] = {**flags, "latency_ms": latency, "notes": notes, "thinking": thinking}

    out = args.out or settings.capabilities_file
    existing: dict[str, Any] = {}
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8")).get("models", {})
    existing.update(results)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "probed_at": datetime.now(UTC).isoformat(),
                "base_url": settings.nebius_base_url,
                "models": existing,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
