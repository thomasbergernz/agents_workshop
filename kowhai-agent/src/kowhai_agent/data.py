"""DuckDB over the Parquet exports.

The Parquet files are read once into tables rather than left behind lazy views,
because the connection then has no further use for the filesystem and can give
it up: `disabled_filesystems` closes `read_text`, `glob` and `COPY ... TO`, which
a SELECT-only guard does not, and `lock_configuration` stops a query turning
either setting back on. The cost is that the whole export is held in memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass
class Database:
    connection: duckdb.DuckDBPyConnection
    window_start: datetime
    window_end: datetime

    @classmethod
    def open(cls, data_dir: Path) -> Database:
        jobs = data_dir / "jobs.parquet"
        sched = data_dir / "sched_15m.parquet"
        if not jobs.exists():
            raise SystemExit(
                f"No jobs.parquet in {data_dir}. Generate the workshop dataset, or "
                "convert a real sacct export with sacct_to_parquet.py."
            )
        con = duckdb.connect(":memory:")
        con.execute("SET enable_progress_bar = false")
        con.execute(f"CREATE TABLE jobs AS SELECT * FROM '{jobs}'")
        if sched.exists():
            con.execute(f"CREATE TABLE sched_15m AS SELECT * FROM '{sched}'")
        lo, hi = con.sql("SELECT MIN(submit_ts), MAX(end_ts) FROM jobs").fetchone()
        con.execute("SET disabled_filesystems = 'LocalFileSystem'")
        con.execute("SET lock_configuration = true")
        return cls(con, lo, hi)

    def sql(self, query: str):
        return self.connection.sql(query)

    @property
    def tables(self) -> list[str]:
        return [r[0] for r in self.connection.sql("SHOW TABLES").fetchall()]
