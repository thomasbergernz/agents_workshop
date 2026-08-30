"""Command line entry points.

`advisory` is the job that passed the workshop's own rubric in Part 12:
repetitive, low-stakes per item, and the deliverable is prose. It writes drafts
for a human to read and send. It does not send anything.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from .agent import Agent
from .config import settings
from .context import context_files, load_context
from .data import Database
from .tools import build_toolbox, load_inventory

app = typer.Typer(add_completion=False, help="Ask questions of Slurm accounting data.")

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


def _build(verbose: bool = False) -> tuple[Agent, Database]:
    from openai import OpenAI

    db = Database.open(settings.data_dir)
    inventory = load_inventory(settings.context_dir / "partitions.json")
    toolbox = build_toolbox(db, inventory, settings.max_rows)
    client = OpenAI(base_url=settings.base_url, api_key=settings.require_api_key())
    agent = Agent(
        client=client,
        model=settings.model,
        system_prompt=load_context(settings.context_dir),
        toolbox=toolbox,
        log_path=settings.log_path,
    )
    return agent, db


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
    typer.echo(run.answer or "No answer: the agent ran out of iterations.")
    typer.secho(f"\n{run.summary()}", fg="bright_black")


@app.command()
def advisory(
    accounts: str | None = typer.Option(
        None, help="Comma-separated account codes. Default: every account in the data."),
    out: Path = typer.Option(Path("drafts"), help="Directory to write drafts into."),
    limit: int = typer.Option(0, help="Stop after N accounts (0 means no limit)."),
) -> None:
    """Draft a usage note per project. Writes files; sends nothing."""
    agent, db = _build()
    if accounts:
        codes = [a.strip() for a in accounts.split(",") if a.strip()]
    else:
        codes = [r[0] for r in db.sql(
            "SELECT DISTINCT account FROM jobs ORDER BY 1").fetchall()]
    if limit:
        codes = codes[:limit]

    out.mkdir(parents=True, exist_ok=True)
    total_seconds = total_tokens = 0
    for code in codes:
        run = agent.ask(ADVISORY.format(account=code))
        total_seconds += run.seconds
        total_tokens += run.prompt_tokens_estimate
        path = out / f"{code}.md"
        path.write_text(
            f"# Usage note: {code}\n\n"
            f"<!-- draft, unreviewed. {run.summary()} -->\n\n"
            f"{run.answer or '(no answer produced)'}\n", encoding="utf-8")
        flag = "!" if run.failed_calls or not run.answer else " "
        typer.echo(f"{flag} {path}  ({run.seconds:.1f}s, {len(run.calls)} tool calls)")

    typer.secho(
        f"\n{len(codes)} drafts in {out}/ — read them before anyone sends one.\n"
        f"{total_seconds:.0f}s, ~{total_tokens:,} prompt tokens total.",
        fg="green")


@app.command()
def selfcheck() -> None:
    """Exercise every tool without calling a model."""
    db = Database.open(settings.data_dir)
    inventory = load_inventory(settings.context_dir / "partitions.json")
    toolbox = build_toolbox(db, inventory, settings.max_rows)
    window = f"{db.window_start:%Y-%m-%d %H:%M}"

    checks = [
        ("tables present", lambda: ", ".join(db.tables)),
        ("partition_info", lambda: toolbox.call("partition_info", {"partition": "large"}).result),
        ("partition_info rejects unknown",
         lambda: "ok" if toolbox.call("partition_info", {"partition": "nope"})
                 .result.startswith("Unknown") else "Error"),
        ("run_sql accepts a time filter",
         lambda: toolbox.call("run_sql", {"sql": f"SELECT COUNT(*) AS n FROM jobs "
                                                 f"WHERE submit_ts >= TIMESTAMP '{window}'"}).result),
        ("run_sql rejects a missing time filter",
         lambda: "ok" if toolbox.call("run_sql", {"sql": "SELECT COUNT(*) FROM jobs"})
                 .result.startswith("Error: every query") else "Error"),
        ("run_sql rejects writes",
         lambda: "ok" if toolbox.call("run_sql", {"sql": "DROP VIEW jobs"})
                 .result.startswith("Error: only SELECT") else "Error"),
        ("list_values", lambda: toolbox.call("list_values", {"column": "partition"}).result),
        ("context loads",
         lambda: f"{len(load_context(settings.context_dir).split())} words"),
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
