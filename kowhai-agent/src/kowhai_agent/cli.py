"""Command line entry points.

`advisory` is the job that passed the workshop's own rubric in Part 12:
repetitive, low-stakes per item, and the deliverable is prose. It writes drafts
for a human to read and send. It does not send anything.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import typer

from .agent import Agent
from .config import settings
from .context import context_files, load_context
from .data import Database
from .tools import build_toolbox, load_inventory

app = typer.Typer(add_completion=False, help="Ask questions of Slurm accounting data.")

# Account codes come out of the data, not from an operator typing them, so they
# are whatever a converted sacct export happened to contain.
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class DraftSummary:
    written: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0
    tokens: int = 0


def _draft_path(out: Path, code: str) -> Path:
    """Where a draft for `code` may be written, or a refusal."""
    if not _ACCOUNT.match(code or ""):
        raise typer.BadParameter(
            f"account code {code!r} is not a plain name; refusing to build a path from it")
    return out / f"{code}.md"


def _draft_each(agent_for, codes: list[str], out: Path, prompt: str) -> DraftSummary:
    """One draft per account. A failure costs that account, not the batch.

    Every account before the failure has already been paid for in model calls,
    so losing them to an exception in the seventeenth is the expensive mistake.

    `agent_for` returns the agent for one account. It is a callable rather than a
    single shared agent because each account gets its own database holding only
    that account's rows -- the scope has to be in the data, not in the prompt.
    """
    summary = DraftSummary()
    for code in codes:
        path = _draft_path(out, code)
        try:
            agent, rows = agent_for(code)
            run = agent.ask(prompt.format(account=code))
        except Exception as exc:
            summary.failed.append(code)
            path.write_text(f"# Usage note: {code}\n\n"
                            f"<!-- FAILED, nothing to send: {type(exc).__name__}: {exc} -->\n",
                            encoding="utf-8")
            typer.secho(f"! {path}  ({type(exc).__name__}: {exc})", fg="red")
            continue
        summary.seconds += run.seconds
        summary.tokens += run.prompt_tokens_estimate
        path.write_text(
            f"# Usage note: {code}\n\n"
            f"<!-- draft, unreviewed. {run.summary()} -->\n"
            f"<!-- derived from {rows:,} job rows, all of account {code}. The agent "
            f"could not see any other account's data. -->\n\n"
            f"{run.answer or '(no answer produced: ' + run.no_answer_reason + ')'}\n",
            encoding="utf-8")
        summary.written.append(path)
        flag = "!" if run.failed_calls or not run.answer else " "
        typer.echo(f"{flag} {path}  ({run.seconds:.1f}s, {len(run.calls)} tool calls)")
    return summary

ADVISORY = """Write a short usage note for the research group behind project {account},
covering the period in this dataset.

1. Get their totals: core-hours charged, CPU time actually used, and overall CPU
   efficiency. Remember to filter on a timestamp column.
2. Find their single worst job name by wasted core-hours, and how it was configured:
   cores requested per task, how many tasks, and how long each ran.
3. Look up what a node in that partition actually provides, so you can say what
   fraction of one they were holding.

Then write at most 150 words addressed to the group. No SQL, no column names, no
jargon: they know their science, not Slurm. Say what they were charged, what they
used, name the job, say concretely what to change in their sbatch script, and estimate
what the change would save. Be direct and not preachy; these are colleagues, not
offenders."""


def _client():
    from openai import OpenAI

    return OpenAI(base_url=settings.base_url, api_key=settings.require_api_key())


def _agent_over(db: Database, client) -> Agent:
    inventory = load_inventory(settings.context_dir / "partitions.json")
    return Agent(
        client=client,
        model=settings.model,
        system_prompt=load_context(settings.context_dir, tables=db.tables),
        toolbox=build_toolbox(db, inventory, settings.max_rows),
        log_path=settings.log_path,
    )


def _build() -> tuple[Agent, Database]:
    db = Database.open(settings.data_dir)
    return _agent_over(db, _client()), db


@app.command()
def ask(
    question: str,
    trace: bool = typer.Option(False, help="Print every tool call as it happens."),
) -> None:
    """Ask one question."""
    agent, _ = _build()
    on_call = None
    if trace:
        def on_call(call, index):
            typer.secho(f"[{index}] {call.name} {call.arguments}",
                        fg="red" if call.failed else "cyan")
    run = agent.ask(question, on_call=on_call)
    typer.echo("")
    typer.echo(run.answer or f"No answer: {run.no_answer_reason}.")
    typer.secho(f"\n{run.summary()}", fg="bright_black")


@app.command()
def advisory(
    accounts: str | None = typer.Option(
        None, help="Comma-separated account codes. Default: every account in the data."),
    out: Path = typer.Option(Path("drafts"), help="Directory to write drafts into."),
    limit: int = typer.Option(0, help="Stop after N accounts (0 means no limit)."),
) -> None:
    """Draft a usage note per project. Writes files; sends nothing."""
    db = Database.open(settings.data_dir)
    client = _client()
    if accounts:
        codes = [a.strip() for a in accounts.split(",") if a.strip()]
    else:
        codes = [r[0] for r in db.sql(
            "SELECT DISTINCT account FROM jobs ORDER BY 1").fetchall()]
    if limit:
        codes = codes[:limit]

    def agent_for(code: str):
        # One database per account, holding that account only. Asking the model
        # to write about account A does not stop it reading account B; not
        # loading B does. This matters because the prompt tells the model to go
        # looking at job names, and job names are chosen by cluster users.
        scoped = db.scoped(code)
        rows = scoped.sql("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return _agent_over(scoped, client), rows

    out.mkdir(parents=True, exist_ok=True)
    summary = _draft_each(agent_for, codes, out, ADVISORY)

    typer.secho(
        f"\n{len(summary.written)} drafts in {out}/ — read them before anyone sends one.\n"
        f"{summary.seconds:.0f}s, ~{summary.tokens:,} prompt tokens total.",
        fg="green")
    if summary.failed:
        typer.secho(f"{len(summary.failed)} failed: {', '.join(summary.failed)}", fg="red")
        raise typer.Exit(code=1)


@app.command()
def selfcheck() -> None:
    """Exercise every tool without calling a model."""
    db = Database.open(settings.data_dir)
    inventory = load_inventory(settings.context_dir / "partitions.json")
    toolbox = build_toolbox(db, inventory, settings.max_rows)
    window = f"{db.window_start:%Y-%m-%d %H:%M}"

    checks = [
        ("tables present", lambda: ", ".join(db.tables)),
        ("partition_info", lambda: toolbox.call(
            "partition_info", {"partition": next(iter(inventory))}).result),
        ("partition_info rejects unknown",
         lambda: "ok" if toolbox.call("partition_info", {"partition": "nope"}).failed
                 else "Error: an unknown partition was not reported as a failure"),
        ("run_sql accepts a time filter",
         lambda: toolbox.call("run_sql", {"sql": f"SELECT COUNT(*) AS n FROM jobs "
                                                 f"WHERE submit_ts >= TIMESTAMP '{window}'"}).result),
        ("run_sql rejects a missing time filter",
         lambda: "ok" if toolbox.call("run_sql", {"sql": "SELECT COUNT(*) FROM jobs"})
                 .result.startswith("Error: every query") else "Error"),
        ("run_sql rejects writes",
         lambda: "ok" if toolbox.call("run_sql", {"sql": "DROP TABLE jobs"})
                 .result.startswith("Error: only SELECT") else "Error"),
        ("list_values", lambda: toolbox.call("list_values", {"column": "partition"}).result),
        ("context loads",
         lambda: f"{len(load_context(settings.context_dir, tables=db.tables).split())} words"),
        ("specs generated", lambda: json.dumps([s["function"]["name"] for s in toolbox.specs])),
    ]
    width = max(len(name) for name, _ in checks)
    failures = 0
    for name, run_check in checks:
        try:
            detail = str(run_check()).replace("\n", " ")[:70]
            ok = not detail.startswith("Error")
        except Exception as exc:
            detail, ok = f"{type(exc).__name__}: {exc}", False
        failures += not ok
        typer.secho(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}",
                    fg="green" if ok else "red")
    raise typer.Exit(code=1 if failures else 0)


@app.command()
def context() -> None:
    """Show the context files that make up the system prompt."""
    for name, words in context_files(settings.context_dir):
        typer.echo(f"{words:6,} words  {name}")
    total = len(load_context(settings.context_dir).split())
    typer.secho(f"{total:6,} words  total, resent on every model call",
                fg="bright_black")


if __name__ == "__main__":
    app()
