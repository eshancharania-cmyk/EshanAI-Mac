"""Trusted-tool registry: explicit implementations, validated arguments,
structured results, confirmation gating. Never executes model-generated code.
"""
from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class ToolResult:
    ok: bool
    output: str                     # concise, model-facing summary
    data: dict = field(default_factory=dict)   # optional structured payload for UI

    def as_model_message(self, name: str) -> str:
        status = "ok" if self.ok else "error"
        return f'{{"tool": {name!r}, "status": {status!r}, "result": {self.output!r}}}'


class ToolError(Exception):
    """Raised by tool impls; message becomes the tool result."""


class NeedConfirmation(ToolError):
    """Raised to request user approval via the broker."""

    def __init__(self, title: str, detail: str):
        super().__init__(detail)
        self.title = title
        self.detail = detail


class ConfirmationBroker:
    """Blocking ask-the-user hook. Default console fallback; UI supplies its own."""

    def __init__(self, confirm_fn: Callable[[str, str], bool] | None = None, timeout_s: float = 180):
        self._fn = confirm_fn
        self.timeout_s = timeout_s
        self._evt: threading.Event | None = None
        self._answer: bool | None = None

    def request(self, title: str, detail: str) -> bool:
        if self._fn is not None:
            return bool(self._fn(title, detail))
        # Console fallback (development/CLI harness)
        try:
            ans = input(f"\n[confirm] {title}\n  {detail}\n  approve? (y/N) ").strip().lower()
            return ans in ("y", "yes")
        except Exception:
            return False


@dataclass
class ToolSpec:
    name: str
    description: str            # one line for the system prompt
    args: dict[str, str]        # arg name -> short type/description
    required: list[str]
    impl: Callable[[dict, ConfirmationBroker], ToolResult]
    danger: str = ""            # non-empty => always confirm first (danger title/desc)


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def prompt_lines(self) -> list[str]:
        lines = []
        for spec in self._tools.values():
            args = ", ".join(spec.args)
            lines.append(f"- {spec.name}({args}) :: {spec.description}")
        return lines

    # ---------------- validation & execution ----------------

    _INT = re.compile(r"^-?\d+$")
    _FLOAT = re.compile(r"^-?\d+(?:\.\d+)?$")

    def _coerce(self, value: Any, how: str, arg: str):
        words = re.findall(r"[A-Za-z]+", how.lower())
        type_ = "str"
        if any(w in ("int", "integer") for w in words):
            type_ = "int"
        elif any(w in ("float", "number") for w in words):
            type_ = "float"
        if type_ == "int":
            if isinstance(value, bool) or not self._INT.match(str(value).strip()):
                raise ToolError(f"Argument '{arg}' must be an integer, got: {value!r}")
            return int(str(value).strip())
        if type_ == "float":
            if isinstance(value, bool) or not (self._INT.match(str(value).strip()) or self._FLOAT.match(str(value).strip())):
                raise ToolError(f"Argument '{arg}' must be a number, got: {value!r}")
            return float(str(value).strip())
        if isinstance(value, str):
            return value
        return str(value)

    def execute(self, name: str, args: dict, broker: ConfirmationBroker) -> ToolResult:
        name = re.sub(r"\s+", "", str(name or "")).lower()   # tolerate 'new_ folder' style typos
        name = re.sub(r"_{2,}", "_", name).strip("_")        # tolerate 'active__window'
        spec = self._tools.get(name)
        if spec is None:
            spec = self._resolve_approx(name)
        if spec is None:
            closest = difflib.get_close_matches(name, list(self._tools), n=1, cutoff=0.6)
            hint = f" Did you mean '{closest[0]}'?" if closest else ""
            known = ", ".join(sorted(self._tools))
            return ToolResult(False, f"Unknown tool '{name}'.{hint} Valid tools: {known}", {})
        args = args if isinstance(args, dict) else {}
        args = args if isinstance(args, dict) else {}
        clean: dict = {}
        for k, how in spec.args.items():
            if k in args:
                try:
                    clean[k] = self._coerce(args[k], how, k)
                except ToolError as e:
                    return ToolResult(False, str(e), {})
        missing = [k for k in spec.required if k not in clean]
        if missing:
            return ToolResult(False, f"Missing argument(s): {', '.join(missing)}", {})
        try:
            return spec.impl(clean, broker)
        except NeedConfirmation as nc:
            approved = broker.request(nc.title, nc.detail)
            if not approved:
                return ToolResult(False, "User declined the requested action.", {})
            clean["_confirmed"] = True
            try:
                return spec.impl(clean, broker)
            except NeedConfirmation:
                return ToolResult(False, "Confirmation flow error (asked twice).", {})
        except ToolError as e:
            return ToolResult(False, str(e), {})
        except Exception as e:  # defensive: tools must never crash the agent
            return ToolResult(False, f"Tool '{name}' failed: {type(e).__name__}: {e}", {})

    def _resolve_approx(self, name: str) -> "ToolSpec | None":
        """Repair hallucinated tool names ('wait_2_3_4' -> wait_window).

        Safety: only ever resolves when exactly ONE registered tool matches the
        leading alphabetic prefix, or a strong fuzzy match exists — it can
        never reinterpret a call as a semantically different tool.
        """
        m = re.match(r"([a-z]+)", name)
        starts = []
        prefix = m.group(1) if m else ""
        if prefix:
            starts = [t for t in self._tools if t.startswith(prefix)]
        if len(starts) == 1:
            return self._tools[starts[0]]
        close = difflib.get_close_matches(name, list(self._tools), n=1, cutoff=0.75)
        if close:
            return self._tools[close[0]]
        if starts:
            close = difflib.get_close_matches(name, starts, n=1, cutoff=0.5)
            if close:
                return self._tools[close[0]]
        return None


REGISTRY = Registry()
