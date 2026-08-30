"""The tools, as closures over a database rather than module globals.

Same three tools as the workshop notebook, same guardrails, but each spec is
generated from the signature by @tool instead of being maintained by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, get_args

import duckdb
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

from .data import Database
from .tooling import Toolbox, ToolFailure, tool

TIME_COLUMNS = ("submit_ts", "eligible_ts", "start_ts", "end_ts", "ts")

# One definition. The runtime guard and the enum the model is shown were written
# out separately, so adding a column to one would either expose a column the
# guard rejects or hide a column that is allowed -- silently, either way.
LookupColumn = Literal["account", "project_name", "institution", "partition",
                       "state", "job_name", "user", "last_reason"]
LOOKUP_COLUMNS = get_args(LookupColumn)

_TIME_FILTER = re.compile(r"\b(" + "|".join(TIME_COLUMNS) + r")\s*(>=|>|<=|<|between)", re.IGNORECASE)

# Comments, string literals and quoted identifiers are all blanked before the
# time-filter search, so a predicate cannot hide inside one. DuckDB spells a
# literal four ways and each was a way past this guard: `-- submit_ts > 1`,
# 'submit_ts > x', $$submit_ts > x$$, and `AS "submit_ts > x"` — the last being
# an alias, which needs no predicate at all. Alternatives cannot collide because
# each starts with a different character, so their order here does not matter.
_COMMENT_OR_LITERAL = re.compile(
    r"""'(?:[^']|'')*'          # 'single-quoted string'
      | "(?:[^"]|"")*"          # "quoted identifier", including a column alias
      | \$(\w*)\$.*?\$\1\$      # $$dollar-quoted$$ and $tag$tagged$tag$
      | --[^\n]*                # -- line comment
      | /\*.*?\*/               # /* block comment */
    """,
    re.DOTALL | re.VERBOSE,
)

# One row can flood the context window without ever reaching the row cap, and
# run_sql's docstring tells the model to aggregate — which is how you get there.
MAX_RESULT_CHARS = 20_000


# job_name, project_name and user are chosen by whoever ran sbatch. A newline in
# one forges a whole extra table row and a pipe opens a column, both
# indistinguishable from real data where the model reads them. What counts as
# structure depends on the rendering, and escaping more than that has a cost:
# list_values exists to hand the model an exact value to filter on, so escaping
# a pipe there returns something that matches nothing.
_LINE = str.maketrans({"\n": "\\n", "\r": "\\r"})
_CELL = str.maketrans({"\n": "\\n", "\r": "\\r", "\t": "\\t", "|": "\\|"})


def _as_line(value: object) -> str:
    """For a newline-separated list: only a line break is structure."""
    return str(value).translate(_LINE)


def _as_cell(value: object) -> str:
    """For a markdown table cell: line breaks, tabs and pipes are structure.

    str() first, deliberately. A LIST, STRUCT or MAP column arrives from DuckDB
    as an ndarray or dict, so an isinstance(str) test skipped it and the hostile
    string nested inside went through unescaped.
    """
    return str(value).translate(_CELL)


def _render(frame) -> str:
    """A markdown table whose values cannot be mistaken for its structure."""
    frame = frame.copy()
    frame.columns = [_as_cell(c) for c in frame.columns]
    for column in frame.columns:
        # Escape anything that is not a number or a timestamp, rather than
        # naming the dtypes that hold text: what counts as a string dtype has
        # changed across pandas versions, and a guard that quietly stops
        # matching is worse than no guard.
        values = frame[column]
        if not (is_numeric_dtype(values) or is_datetime64_any_dtype(values)):
            frame[column] = values.map(_as_cell)
    return frame.to_markdown(index=False)


def _fits(rendered: str) -> str:
    if len(rendered) <= MAX_RESULT_CHARS:
        return rendered
    return (rendered[:MAX_RESULT_CHARS] +
            f"\n\n[cut at {MAX_RESULT_CHARS:,} characters. Return fewer or shorter "
            "columns; aggregate to a number rather than concatenating text]")


def _escape_like(fragment: str) -> str:
    """Make a user fragment literal: `_` and `%` are ILIKE wildcards otherwise."""
    for char in ("\\", "%", "_"):
        fragment = fragment.replace(char, "\\" + char)
    return fragment


def _single_select(sql: str) -> str | None:
    """An error message unless `sql` is exactly one SELECT statement, else None.

    A prefix check only inspects the start of the string, so `SELECT 1; DROP
    VIEW jobs` passed it. DuckDB's own parser counts the statements and names
    their type, which is the same question asked of the thing that will run them.
    """
    try:
        statements = duckdb.extract_statements(sql)
    except Exception as exc:
        return f"Error: {exc}"
    if len(statements) != 1:
        return ("Error: send exactly one statement. Anything after a semicolon "
                "is rejected, including a second SELECT.")
    # `!=`, not `is not`: duckdb's StatementType is a pybind11 enum and hands
    # back a fresh object on each access, so identity never holds.
    if statements[0].type != duckdb.StatementType.SELECT:
        return "Error: only SELECT queries are allowed. This tool cannot modify data."
    return None


def load_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Partition hardware and charging policy: `scontrol show partition` plus finance."""
    return json.loads(path.read_text(encoding="utf-8"))


def build_toolbox(db: Database, inventory: dict[str, dict[str, Any]],
                  max_rows: int = 50) -> Toolbox:
    """Wire the tools to one database and one cluster inventory."""
    window = (f"{db.window_start:%Y-%m-%d %H:%M}", f"{db.window_end:%Y-%m-%d %H:%M}")

    @tool
    def partition_info(partition: str) -> str:
        """Hardware and policy for one Slurm partition: node count, cores and memory
        per node, GPU model, maximum walltime, and the charging rate. Use it to judge
        whether a job's request was reasonable or what it cost.

        partition: Partition name, for example 'large' or 'gpu'.
        """
        details = inventory.get(partition)
        if details is None:
            return ToolFailure(f"Unknown partition '{partition}'. "
                               f"Known partitions: {', '.join(inventory)}.")
        lines = [f"partition: {partition}"]
        lines += [f"{k}: {v}" for k, v in details.items()]
        cores = details["nodes"] * details["cpus_per_node"]
        lines.append(f"total cores: {cores}")
        lines.append(f"core-hours available per day: {cores * 24:,}")
        return "\n".join(lines)

    @tool
    def run_sql(sql: str) -> str:
        """Run one read-only DuckDB SELECT query against the Slurm accounting data.
        Every query must filter on a timestamp column. Results are capped, so
        aggregate rather than selecting raw rows.

        sql: A single SELECT statement.
        """
        rejection = _single_select(sql)
        if rejection is not None:
            return rejection
        if not _TIME_FILTER.search(_COMMENT_OR_LITERAL.sub(" ", sql)):
            return (
                "Error: every query must filter on a timestamp column "
                f"({', '.join(TIME_COLUMNS)}).\n"
                "Add a predicate such as:\n"
                f"  WHERE submit_ts >= TIMESTAMP '{window[0]}'\n"
                f"    AND submit_ts <  TIMESTAMP '{window[1]}'\n"
                f"The data covers {window[0]} to {window[1]} UTC."
            )
        try:
            # The cap belongs in the query. Calling .df() first builds the whole
            # result in pandas -- outside DuckDB's memory accounting -- so a
            # cross join is materialised in full just to show fifty rows of it.
            # One extra row is enough to know there was more.
            frame = db.sql(sql).limit(max_rows + 1).df()
        except Exception as exc:
            # DuckDB quotes the offending value back verbatim, so this path can
            # carry a hostile job name too -- and it returned before both the
            # escaping and the size cap.
            return _fits(_as_line(f"Error: {exc}"))
        if frame.empty:
            return ("0 rows. The query is valid but nothing matched it. Check your "
                    "filter values with list_values before concluding the answer is zero.")
        if len(frame) > max_rows:
            return _fits(_render(frame.head(max_rows)) +
                         f"\n\n[truncated at {max_rows} rows. Aggregate, or add ORDER BY "
                         "and LIMIT]")
        return _fits(_render(frame))

    @tool
    def list_values(column: LookupColumn, contains: str = "") -> str:
        """List the real distinct values of a dimension column before filtering on it.
        Use this whenever the user names a project, institution, partition or job in
        prose, because the stored spelling and punctuation will not match what they typed.

        column: Which dimension column to list.
        contains: Case-insensitive fragment to filter by.
        """
        if column not in LOOKUP_COLUMNS:
            return f"Error: column must be one of {list(LOOKUP_COLUMNS)}."
        frame = db.connection.execute(
            f"SELECT DISTINCT {column} AS value FROM jobs "
            f"WHERE {column} ILIKE ? ESCAPE '\\' ORDER BY 1 LIMIT 100",
            [f"%{_escape_like(contains)}%"]
        ).df()
        if frame.empty:
            return ToolFailure(
                f"No {column} value contains '{contains}'. Try a shorter fragment.")
        suffix = "\n[first 100 only]" if len(frame) == 100 else ""
        return _fits("\n".join(frame["value"].map(_as_line)) + suffix)

    return Toolbox.of(partition_info, run_sql, list_values)
