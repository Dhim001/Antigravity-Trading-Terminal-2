"""Shared typed tool registry for agent clients (Copilot, HTTP, future agents).

Tools are registered once (see catalog.py) and executed through a uniform
``{ok, result|error}`` envelope. Tools whose ``side_effect`` is ``trade`` or
``control`` (or that set ``hitl_required``) are human-in-the-loop gated:
``execute`` refuses to run them unless ``confirmed=True``, returning a
pending-style payload the caller can route into the Copilot confirm flow.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

SIDE_EFFECTS = ("read", "trade", "control")
_GATED_SIDE_EFFECTS = ("trade", "control")

Handler = Callable[[dict[str, Any], "ToolContext"], Any]
PlanFn = Callable[[dict[str, Any], "ToolContext"], Any]


@dataclass
class ToolContext:
    """Host state handed to every handler (extra fields are host-specific)."""

    state: Any = None
    session_id: str | None = None
    message: str = ""
    active_symbol: str | None = None

    @property
    def bot_manager(self) -> Any:
        return getattr(self.state, "bot_manager", None)

    @property
    def oms(self) -> Any:
        return getattr(self.state, "oms", None)


@dataclass
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: str = "read"
    hitl_required: bool = False
    handler: Handler | None = None
    # Optional planner for HITL tools: resolves/enriches raw args into the
    # canonical {"type", "params"} pending action (or {"error": ...}).
    plan: PlanFn | None = None

    @property
    def gated(self) -> bool:
        return self.hitl_required or self.side_effect in _GATED_SIDE_EFFECTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "side_effect": self.side_effect,
            "hitl_required": self.hitl_required,
        }


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def _validate_args(schema: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Pragmatic JSON-Schema check: required fields, types, enums.

    Numeric strings are coerced for integer/number fields (LLM planners often
    quote numbers). Returns (possibly-coerced args, error message or None).
    """
    if not isinstance(schema, dict):
        return dict(args), None
    out = dict(args)
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key in schema.get("required") or []:
        if out.get(key) is None:
            return out, f"Missing required argument: {key}"
    for key, spec in props.items():
        if key not in out or out[key] is None or not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        val = out[key]
        if expected in ("integer", "number") and isinstance(val, str):
            try:
                val = int(val) if expected == "integer" else float(val)
                out[key] = val
            except (TypeError, ValueError):
                val = out[key]
        check = _TYPE_CHECKS.get(expected)
        if check is not None and not check(val):
            return out, f"Argument '{key}' must be of type {expected}"
        enum = spec.get("enum")
        if enum and val not in enum:
            return out, f"Argument '{key}' must be one of {list(enum)}"
    return out, None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> AgentTool:
        if not tool.name or not str(tool.name).strip():
            raise ValueError("Tool name is required")
        if tool.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"Invalid side_effect {tool.side_effect!r} for tool {tool.name!r}")
        self._tools[str(tool.name).strip()] = tool
        return tool

    def get(self, name: str | None) -> AgentTool | None:
        return self._tools.get(str(name or "").strip())

    def list(self) -> list[AgentTool]:
        return [self._tools[name] for name in sorted(self._tools)]

    async def execute(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        confirmed: bool = False,
        context: ToolContext | Any = None,
    ) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}

        if isinstance(context, ToolContext):
            ctx = context
        elif context is not None:
            ctx = ToolContext(state=context)
        else:
            ctx = ToolContext()
        raw_args = args if isinstance(args, dict) else {}

        if tool.gated and not confirmed:
            # HITL gate: never execute; return a pending-style preview instead.
            pending: dict[str, Any] = {"type": tool.name, "params": dict(raw_args)}
            if tool.plan is not None:
                try:
                    planned = tool.plan(raw_args, ctx)
                    if inspect.isawaitable(planned):
                        planned = await planned
                except Exception as exc:
                    logger.exception("tool plan failed: %s", tool.name)
                    return {"ok": False, "error": str(exc)}
                if isinstance(planned, dict) and planned.get("error"):
                    return {"ok": False, "error": str(planned["error"])}
                if isinstance(planned, dict):
                    pending = {
                        "type": planned.get("type") or tool.name,
                        "params": planned.get("params") if isinstance(planned.get("params"), dict) else {},
                    }
            return {
                "ok": False,
                "error": f"Tool '{tool.name}' requires confirmation",
                "requires_confirmation": True,
                "pending": pending,
            }

        clean_args, err = _validate_args(tool.input_schema, raw_args)
        if err:
            return {"ok": False, "error": err}

        if tool.handler is None:
            return {"ok": False, "error": f"Tool '{tool.name}' has no handler"}
        try:
            result = tool.handler(clean_args, ctx)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.exception("tool execute failed: %s", tool.name)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "result": result}
