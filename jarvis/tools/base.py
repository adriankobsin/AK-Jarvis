"""Tool registry: plain Python functions exposed to Claude as client tools."""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

# A confirmation callback receives a human-readable description of the action
# and returns True if the user approves it.
ConfirmFn = Callable[[str], bool]


@dataclass
class ToolContext:
    config: Any
    store: Any
    confirm: ConfirmFn = lambda _msg: False
    notify: Callable[[str, str], None] = lambda _title, _msg: None
    kb: Any = None
    extras: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    func: Callable[..., Any]

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def tool(self, name: str, description: str, properties: dict | None = None,
             required: list[str] | None = None):
        """Decorator: register func(ctx, **kwargs) as a tool."""

        def wrap(func):
            self.add(Tool(
                name=name,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties or {},
                    "required": required or [],
                },
                func=func,
            ))
            return func

        return wrap

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, args: dict, ctx: ToolContext) -> tuple[Any, bool]:
        """Run a tool. Returns (content_string, is_error)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}", True
        try:
            result = tool.func(ctx, **(args or {}))
        except TypeError as e:
            return f"Bad arguments for {name}: {e}", True
        except Exception as e:  # tools should never crash the assistant
            return f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}", True
        if isinstance(result, str):
            return result, False
        if isinstance(result, list) and result and all(
                isinstance(b, dict) and b.get("type") in ("text", "image") for b in result):
            return result, False  # rich content (e.g. screenshots) passed straight to Claude
        return json.dumps(result, default=str, ensure_ascii=False), False


def build_registry(config) -> ToolRegistry:
    """Assemble every tool module into one registry."""
    from . import (browser, comms, devices, documents, expert, integrations, kids, knowledge, media,
                   memory, programs, system, tasks, weather)

    reg = ToolRegistry()
    for mod in (system, memory, knowledge, expert, tasks, weather, devices, kids, comms,
                browser, documents, media, integrations, programs):
        mod.register(reg, config)
    from .openai_pair import openai_ready
    if openai_ready(config):   # Claude + GPT working together (needs OPENAI_API_KEY)
        from . import openai_pair
        openai_pair.register(reg, config)
    role = (config.get("profile") or {}).get("role", "owner")
    if role == "child":
        from ..profiles import CHILD_TOOLS

        for name in list(reg._tools):
            if name not in CHILD_TOOLS:
                reg._tools.pop(name)
    else:
        reg._tools.pop("flag_for_parent", None)
    return reg
