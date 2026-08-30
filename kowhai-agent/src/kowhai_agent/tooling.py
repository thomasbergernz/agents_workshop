"""Turn a plain Python function into a tool the model can request.

In the workshop notebook every tool carried about 25 lines of hand-written JSON
next to it. That JSON can drift from the function signature without anything
failing, which is a silent bug of exactly the kind Part 12 warns about. Here the
spec is generated from the signature and the docstring, so the two cannot
disagree.

    @tool
    def partition_info(partition: str) -> str:
        '''Hardware and policy for one Slurm partition.

        partition: Partition name, for example 'large' or 'gpu'.
        '''

The first paragraph of the docstring becomes the tool description the model
reads; indented `name: description` lines become parameter descriptions.
"""
from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin, get_type_hints

_PARAM_LINE = re.compile(r"^(\w+):\s+(.+)$")

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _json_schema_for(annotation: Any) -> dict[str, Any]:
    if get_origin(annotation) is Literal:
        options = list(get_args(annotation))
        kind = _JSON_TYPES.get(type(options[0]), "string")
        return {"type": kind, "enum": options}
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    raise TypeError(
        f"Unsupported tool parameter type {annotation!r}. Tools take simple "
        "scalars so the model can fill them in reliably."
    )


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (description, {parameter: description})."""
    description: list[str] = []
    params: dict[str, str] = {}
    for line in inspect.cleandoc(doc or "").splitlines():
        match = _PARAM_LINE.match(line.strip())
        if match and not line.strip().endswith(":"):
            params[match.group(1)] = match.group(2).strip()
        elif not params:
            description.append(line.strip())
    return " ".join(description).strip(), params


def tool(fn: Callable[..., str]) -> Callable[..., str]:
    """Attach a generated OpenAI tool spec to a function as `.tool_spec`."""
    hints = get_type_hints(fn)
    signature = inspect.signature(fn)
    description, param_docs = _split_docstring(fn.__doc__ or "")
    if not description:
        raise ValueError(f"{fn.__name__} needs a docstring; the model reads it.")

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        schema = _json_schema_for(hints.get(name, str))
        if name in param_docs:
            schema["description"] = param_docs[name]
        properties[name] = schema
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    fn.tool_spec = {  # type: ignore[attr-defined]
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
    return fn


@dataclass
class ToolCall:
    """One complete request/execute/result cycle, for logging and display."""

    name: str
    arguments: dict[str, Any]
    result: str
    seconds: float
    failed: bool = False


@dataclass
class Toolbox:
    """The set of tools an agent may request, and the only place they run."""

    tools: dict[str, Callable[..., str]] = field(default_factory=dict)

    @classmethod
    def of(cls, *functions: Callable[..., str]) -> Toolbox:
        for fn in functions:
            if not hasattr(fn, "tool_spec"):
                raise TypeError(f"{fn.__name__} is missing @tool")
        return cls({fn.__name__: fn for fn in functions})

    def with_(self, *functions: Callable[..., str]) -> Toolbox:
        """A copy with extra tools, or replacements for existing ones."""
        merged = dict(self.tools)
        merged.update({fn.__name__: fn for fn in functions})
        return Toolbox(merged)

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [fn.tool_spec for fn in self.tools.values()]  # type: ignore[attr-defined]

    def __iter__(self) -> Iterator[str]:
        return iter(self.tools)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        """Execute one tool. This is the control point: validation, permissions
        and audit logging belong here, next to the line that calls the function."""
        started = time.perf_counter()
        try:
            function = self.tools[name]
        except KeyError:
            return ToolCall(name, arguments, f"Error: no tool named '{name}'.",
                            time.perf_counter() - started, failed=True)
        try:
            result = str(function(**arguments))
            failed = result.lstrip().lower().startswith("error")
        except Exception as exc:  # returned to the model, which usually recovers
            result, failed = f"Error: {type(exc).__name__}: {exc}", True
        return ToolCall(name, arguments, result, time.perf_counter() - started, failed)


def format_call(call: ToolCall, index: int) -> str:
    """Markdown rendering of one tool call, as the notebook shows it."""
    body = call.result if call.result.lstrip().startswith("|") else f"```text\n{call.result}\n```"
    return (f"#### Tool call {index}: `{call.name}`\n\n"
            f"```json\n{json.dumps(call.arguments, indent=2)}\n```\n\n{body}")
