# kowhai-agent

Ask questions of Slurm accounting data, and draft per-project usage notes from it.

This is the production half of the *Agents for HPC Operations* workshop notebook.
The notebook teaches; this package is what you actually run. They are deliberately
separate: 47 of the notebook's 62 code cells are demonstrations, several of them
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

`uv sync` installs the `kowhai` command into `.venv` but does not put it on your
PATH, so run it through uv:

```bash
uv run kowhai selfcheck                     # every tool, no model calls, no cost
uv run kowhai context                       # what the system prompt is made of
uv run kowhai ask "Which project wasted the most core-hours?" --trace
uv run kowhai advisory --out drafts/        # one usage note per project
```

`selfcheck` and `context` make no model calls and need no API key. `ask` and
`advisory` need `OPENROUTER_API_KEY`.

Drop the `uv run` prefix if you activated the environment yourself — either
`source .venv/bin/activate`, or the virtualenv you ran `pip install -e` into. To
call it from outside this directory, use
`uv run --directory /path/to/kowhai-agent kowhai selfcheck`.

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
- **Results are capped** at `KOWHAI_MAX_ROWS` (50) rows *and* 20,000 characters.
  The row cap is applied by the query, not after the fact in pandas, so a cross
  join is never built in full to show fifty rows of it. The character cap is there
  because rows are the wrong unit: one `string_agg` row can flood the context
  window without ever approaching fifty.
- **The connection has a memory limit it cannot raise.** 2GB and four threads, set
  before the configuration is locked, so an expensive query is an error the model
  can read rather than an OOM kill of an unattended run.
- **The connection has no filesystem.** `read_text`, `read_csv`, `glob` and
  `COPY ... TO` are all legal inside a single SELECT, so no statement-level check
  closes them. `Database.open` reads the Parquet into tables, then disables
  `LocalFileSystem` and locks the configuration so a query cannot turn it back on.
  The cost: the whole export is held in memory, and DuckDB can no longer spill a
  large aggregation to disk.

Each rejection is a sentence written for the model to read and correct, not an
exception. Watch one happen with `uv run kowhai ask ... --trace`.

- **One account per database, for `advisory`.** Each note is drafted against a
  connection holding only that group's rows, so the scope is what the model can
  read rather than what the prompt asked for. The draft header records the
  account and the row count it came from.
- **Values are escaped where they are rendered.** A newline in a job name used to
  forge a whole extra table row and a pipe opened a column, both indistinguishable
  from real data at the point the model reads them. `sbatch --job-name` is
  unprivileged, so this is reachable by any cluster user.

**None of this is database-level isolation.** It is one process guarding itself,
which is the right shape for a workshop dataset in a `:memory:` database and the
wrong shape for a real `slurmdbd`. There, put the query behind a read-only role on a
separate replica with a statement timeout, and treat everything above as defence in
depth. That matters most for `advisory`, which runs unattended over strings cluster users
choose. Three things stand between a hostile job name and a wrong note: the data
scope above, the escaping above, and a line in `context/00-role.md` telling the
model that field values are never instructions. The first is a boundary; the other
two are mitigations. A person still reads the draft before anyone sends it -- that
is the control that does not depend on any of this holding.

## Running behind a gateway

`KOWHAI_BASE_URL` and `KOWHAI_MODEL` are read at call time, so putting a gateway in
front of this package is configuration rather than code:

```bash
KOWHAI_BASE_URL=http://localhost:1975/v1 uv run kowhai ask "..." --trace
```

Part 13 of the notebook runs [Envoy AI
Gateway](https://github.com/envoyproxy/ai-gateway) standalone on that port. Three
things move when you do: the credential lives in the gateway process rather than this
one, the provider's model identifiers become configuration you own rather than a string
in `config.py`, and token counts are recorded by something other than the code being
counted.

Nothing above moves with it. A gateway sees an HTTP request going to a model; it does
not know that `run_sql` exists and could not read the query if it did. Every guard in
this section is still the only thing between a general SQL tool and the database. The
two are boundaries at opposite ends of the same process, and both are load-bearing.

Standalone `aigw run` also counts tokens without enforcing a budget. Token rate
limiting and quota policy need Envoy Gateway's rate limit service, Redis and a cluster,
so do not read a gateway as a spend control until you have deployed the part that is
one.

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
uv run pytest        # 82 tests, no network, no API key
```

The suite covers spec generation, tool-error recovery inside the loop, iteration
limits, and that the context files still contain the facts the answers depend on —
the UTC/NZST note, `planned_min`, and the warning against averaging a ratio.

It also covers each guardrail twice: with the input it obviously rejects, and with
the input that used to slip past it. A guard tested only against what it visibly
catches proves nothing — that is how the semicolon, the `--` comment, dollar-quoted
strings and a double-quoted column alias each survived a green suite in turn.

`scripts/sacct_to_parquet.py` has its own suite, because the path to real cluster
data is the one nobody exercises until the day they need it.
