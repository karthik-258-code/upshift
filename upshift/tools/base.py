"""Tool contract and registry. Tool results are always JSON strings for the model."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

ToolHandler = Callable[..., Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai() for t in self._tools.values()]

    async def call(self, name: str, arguments_json: str | None) -> str:
        """Invoke a tool by name with the model-provided JSON arguments. Never raises."""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool: {name}"})
        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
            result = await tool.handler(**arguments)
            return json.dumps(result, default=str)
        except Exception as exc:  # noqa: BLE001 - tool failures are data for the model
            log.warning("tool %s failed: %s", name, exc)
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
