# kowhai-agent

Ask questions of Slurm accounting data, and draft per-project usage notes from it.

This is the production half of the *Agents for HPC Operations* workshop notebook.
The notebook teaches; this package is what you actually run. They are deliberately
separate: 42 of the notebook's 56 code cells are demonstrations, several of them
deliberately wrong, and none of that belongs in a scheduled job.

## Install

```bash
uv sync                      # or: pip install -e ".[dev]"
export OPENROUTER_API_KEY=...
```

You need `data/jobs.parquet` (and optionally `data/sched_15m.parquet`):

```bash
uv run scripts/make_workshop_data.py                  # synthetic, for trying it out
uv run scripts/sacct_to_parquet.py sacct_dump.txt \
    --tz Pacific/Auckland --out data/ --derive-sched  # your real cluster
```

## Use

```bash
kowhai selfcheck                     # every tool, no model calls, no cost
kowhai context                       # what the system prompt is made of
kowhai ask "Which project wasted the most core-hours?" --trace
kowhai advisory --out drafts/        # one usage note per project
```

`advisory` writes markdown drafts and sends nothing. That is the point: it is the
one task in the workshop that passed its own rubric — repetitive, low-stakes per
item, prose output — and the human stays between the draft and the researcher.

## How it is put together

    src/kowhai_agent/
      tooling.py   @tool: generates the OpenAI spec from the signature + docstring
      tools.py     partition_info, run_sql, list_values, as closures over one database
      agent.py     the loop (~40 lines), plus the cost and trace accounting
      context.py   loads the system prompt from markdown files
      data.py      DuckDB over Parquet, materialised, then no filesystem at all
      cli.py       ask / advisory / selfcheck / context
    context/
      00-role.md 10-jobs.md 20-sched-15m.md 30-domain-notes.md partitions.json

Three decisions carried over from the workshop's conclusions:

**The context is content, not code.** Schema cards and domain notes are markdown
files loaded at runtime, in filename order. A colleague who knows the cluster but
not Python can edit them; they get reviewed on their own merits; and you can diff
how your institutional knowledge changed over a year. `context/` is the asset in
this repository. The Python is replaceable.

**Tool specs are generated, never hand-written.** In the notebook each tool carried
~25 lines of JSON that could silently drift from its own function. Here `@tool`
derives it from type hints and the docstring, so they cannot disagree, and an
unsupported parameter type fails at import rather than at 3 a.m.

**Every run is costed.** `Run.summary()` reports wall time, model calls, tool calls,
how many the model had to correct, and estimated prompt tokens; each run appends a
line to `logs/runs.jsonl`. The loop resends the whole history each iteration, so
context is paid for once per tool call, not once per question — that is visible
here rather than a surprise on the invoice.

## Guardrails

`run_sql` hands the model a general SQL tool, so the tool function is the control
point:

- **One statement, and it must be a SELECT.** DuckDB's own parser counts the
  statements and names each type, so `SELECT 1; DROP VIEW jobs` is rejected outright
  rather than half-executed. A prefix check on the string passes it.
- **Every query filters on a timestamp column.** Checked against the query with
  comments and string literals blanked out, so the predicate cannot hide in a `--`
  comment. This enforces a habit, not a scan limit: `submit_ts > TIMESTAMP
  '1970-01-01'` satisfies it and still reads everything.
- **Results are capped** at `KOWHAI_MAX_ROWS` (50), so one query cannot flood the
  context window. It caps what the model sees, not what DuckDB materialises.
- **The connection has no filesystem.** `read_text`, `read_csv`, `glob` and
  `COPY ... TO` are all legal inside a single SELECT, so no statement-level check
  closes them. `Database.open` reads the Parquet into tables, then disables
  `LocalFileSystem` and locks the configuration so a query cannot turn it back on.
  The cost: the whole export is held in memory, and DuckDB can no longer spill a
  large aggregation to disk.

Each rejection is a sentence written for the model to read and correct, not an
exception. Watch one happen with `kowhai ask --trace`.

**None of this is database-level isolation.** It is one process guarding itself,
which is the right shape for a workshop dataset in a `:memory:` database and the
wrong shape for a real `slurmdbd`. There, put the query behind a read-only role on a
separate replica with a statement timeout, and treat everything above as defence in
depth. That matters most for `advisory`: it runs unattended, and the `job_name` and
`project_name` strings it reads into the model's context are chosen by whoever ran
`sbatch`.

## What this is not for

From the workshop's Part 12 rubric, most reporting work should not be an agent:

| Task | Build instead |
|---|---|
| A number someone asks for the same way each month | a saved query on a dashboard |
| "Is anything wrong right now?" | a threshold alert on requested-versus-used cores |
| Anything a user could appeal (quota, allocation) | a deterministic query; agents do not repeat themselves |
| Eighteen tailored notes nobody has time to write | this package |

## Tests

```bash
uv run pytest        # 30 tests, no network, no API key
```

The suite covers spec generation, tool-error recovery inside the loop, iteration
limits, and that the context files still contain the facts the answers depend on —
the UTC/NZST note, `planned_min`, and the warning against averaging a ratio.

It also covers each guardrail twice: with the input it obviously rejects, and with
the input that used to slip past it. A guard tested only against what it visibly
catches proves nothing, which is how the semicolon and the `--` comment survived a
green suite.
