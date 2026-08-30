"""The loop. Still about forty lines; everything else is accounting.

Part 12 of the workshop measures variance and cost by wrapping the client by
hand. Here that measurement is part of the object, because an agent you cannot
cost is one you cannot defend in a review.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .tooling import Toolbox, ToolCall


@dataclass
class Run:
    """Everything one question consumed and produced."""

    question: str
    answer: str | None
    model: str
    calls: list[ToolCall] = field(default_factory=list)
    model_calls: int = 0
    prompt_tokens_estimate: int = 0
    seconds: float = 0.0
    stopped_early: bool = False

    @property
    def failed_calls(self) -> int:
        return sum(1 for c in self.calls if c.failed)

    def summary(self) -> str:
        return (f"{self.seconds:.1f}s, {self.model_calls} model calls, "
                f"{len(self.calls)} tool calls ({self.failed_calls} corrected), "
                f"~{self.prompt_tokens_estimate:,} prompt tokens")


class Agent:
    """Ask a question, let the model request tools, return the answer and the bill."""

    def __init__(
        self,
        client: Any,
        model: str,
        system_prompt: str,
        toolbox: Toolbox,
        max_iters: int = 10,
        max_tokens: int = 2000,
        log_path: Path | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.toolbox = toolbox
        self.max_iters = max_iters
        self.max_tokens = max_tokens
        self.log_path = log_path

    def ask(self, question: str, on_call: Callable[[ToolCall, int], None] | None = None) -> Run:
        messages: list[Any] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        run = Run(question=question, answer=None, model=self.model)
        started = time.perf_counter()

        for _ in range(self.max_iters):
            run.model_calls += 1
            # The loop resends the whole history every iteration, so the context
            # is paid for once per tool call rather than once per question.
            run.prompt_tokens_estimate += sum(len(str(m)) for m in messages) // 4

            message = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.toolbox.specs or None,
                max_tokens=self.max_tokens,
            ).choices[0].message

            if not message.tool_calls:
                run.answer = message.content
                break

            messages.append(message)
            for request in message.tool_calls:
                arguments = json.loads(request.function.arguments or "{}")
                call = self.toolbox.call(request.function.name, arguments)
                run.calls.append(call)
                if on_call is not None:
                    on_call(call, len(run.calls))
                messages.append({"role": "tool", "tool_call_id": request.id,
                                 "content": call.result})
        else:
            run.stopped_early = True

        run.seconds = time.perf_counter() - started
        self._log(run)
        return run

    def _log(self, run: Run) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            **{k: v for k, v in asdict(run).items() if k != "calls"},
            "answer_chars": len(run.answer or ""),
            "calls": [{"name": c.name, "arguments": c.arguments,
                       "seconds": round(c.seconds, 3), "failed": c.failed}
                      for c in run.calls],
        }
        record.pop("answer", None)  # keep the log small; drafts are written separately
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
