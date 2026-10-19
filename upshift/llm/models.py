"""Model catalog loaded from config/models.yaml, optionally overlaid with probe results."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

CAPABILITIES = ("chat", "tool_calling", "parallel_tool_calls", "json_object", "json_schema")
# How to switch a model's hidden reasoning off for structured-output calls. Discovered by
# scripts/probe_models.py and stored under models.<id>.thinking.control in capabilities.json.
THINKING_CONTROLS = ("chat_template_kwargs", "no_think", "detailed_thinking_off")


@dataclass(frozen=True)
class ModelSpec:
    key: str
    id: str
    tier: int
    context_tokens: int
    input_price_per_m: float
    output_price_per_m: float
    documented_capabilities: frozenset[str]
    optional: bool = False
    notes: str = ""
    thinking_control: str = ""  # one of THINKING_CONTROLS, or "" when not needed/unknown

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens / 1e6 * self.input_price_per_m
            + completion_tokens / 1e6 * self.output_price_per_m
        )


@dataclass
class ModelCatalog:
    base_url: str
    models: dict[str, ModelSpec]
    # model id -> capability -> verified flag. None means the probe has not run yet.
    verified: dict[str, dict[str, bool]] | None = None

    @classmethod
    def load(
        cls, models_file: str | Path, capabilities_file: str | Path | None = None
    ) -> ModelCatalog:
        raw = yaml.safe_load(Path(models_file).read_text(encoding="utf-8"))
        models: dict[str, ModelSpec] = {}
        for key, m in raw["models"].items():
            models[key] = ModelSpec(
                key=key,
                id=m["id"],
                tier=int(m.get("tier", 0)),
                context_tokens=int(m["context_tokens"]),
                input_price_per_m=float(m["input_price_per_m"]),
                output_price_per_m=float(m["output_price_per_m"]),
                documented_capabilities=frozenset(m.get("documented_capabilities", ["chat"])),
                optional=bool(m.get("optional", False)),
                notes=str(m.get("notes", "")),
                thinking_control=str(m.get("thinking_control", "")),
            )
        verified = None
        if capabilities_file is not None and Path(capabilities_file).exists():
            data = json.loads(Path(capabilities_file).read_text(encoding="utf-8"))
            probed = data.get("models", {})
            verified = {
                model_id: {c: bool(v) for c, v in caps.items() if c in CAPABILITIES}
                for model_id, caps in probed.items()
            }
            for key, spec in list(models.items()):
                control = str((probed.get(spec.id, {}).get("thinking") or {}).get("control", ""))
                if not spec.thinking_control and control in THINKING_CONTROLS:
                    models[key] = replace(spec, thinking_control=control)
        return cls(base_url=raw.get("base_url", ""), models=models, verified=verified)

    @property
    def probed(self) -> bool:
        return self.verified is not None

    def capabilities(self, key: str) -> set[str]:
        """Verified capabilities if the probe ran, otherwise the documented ones."""
        spec = self.models[key]
        if self.verified is None:
            return set(spec.documented_capabilities)
        flags = self.verified.get(spec.id)
        if flags is None:
            return set()
        return {c for c, ok in flags.items() if ok}

    def is_available(self, key: str) -> bool:
        return "chat" in self.capabilities(key)
