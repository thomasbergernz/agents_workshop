"""Convert a real sacct export into the two Parquet files the Kōwhai notebook reads.

Step 1 — export from your cluster (one line, no -X so we get the steps):

    SLURM_TIME_FORMAT='%Y-%m-%dT%H:%M:%S' \
    sacct -a -S 2026-07-06 -E 2026-08-03 --parsable2 --noconvert \
      --format=JobID,JobIDRaw,JobName,User,Account,Partition,QOS,State,ExitCode,\
Submit,Eligible,Start,End,Timelimit,Elapsed,Planned,NNodes,NCPUS,ReqMem,ReqTRES,\
TotalCPU,MaxRSS,Reason > sacct_dump.txt

    (Slurm older than 23.02: replace Planned with Reserved in --format.)

Step 2 — convert. sacct prints times in the cluster's local zone, so tell the
converter which zone that is; the notebook expects UTC in the files:

    python sacct_to_parquet.py sacct_dump.txt --tz Pacific/Auckland \
        --out data/ --derive-sched --anonymise

Step 3 — put the resulting data/jobs.parquet and data/sched_15m.parquet next to the
notebook before running its dataset cell. build_dataset() sees them and skips the
synthetic generator.

What you will NOT have from sacct alone:
  est_start_ts  Slurm does not retain its predictions. Starts as NULL; to fill it
                going forward, sample `squeue --start` (see squeue_start_sampler
                at the bottom) and merge on job_id.
  gpu_util_pct  comes from profiling (DCGM, jobstats), not accounting. NULL here.
  project_name / institution
                sacct only knows the account code. Pass --accounts accounts.csv
                with columns account,project_name,institution to enrich.
  sched_15m     --derive-sched rebuilds it from the jobs themselves. It is faithful
                for load caused by these jobs but blind to anything outside the
                export (other partitions, down nodes, reservations). For the real
                thing, sample sinfo/squeue on a timer or use your Prometheus
                slurm exporter's series.

--anonymise replaces usernames with stable pseudonyms (u0001, u0002, ...) so the
notebook can be demonstrated without showing who ran what. Accounts are kept.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TARGET_COLUMNS = [
    "job_id", "array_job_id", "array_task_id", "job_name", "user", "account",
    "project_name", "institution", "partition", "qos", "state", "exit_code",
    "submit_ts", "eligible_ts", "est_start_ts", "start_ts", "end_ts",
    "timelimit_min", "elapsed_min", "planned_min",
    "req_nodes", "req_cpus", "req_mem_mb", "req_gpus",
    "total_cpu_min", "max_rss_mb", "gpu_util_pct", "last_reason",
]

_DUR = re.compile(r"^(?:(\d+)-)?(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?$")
_TWO = re.compile(r"^(?:(\d+)-)?(\d{1,2}):(\d{2})(?:\.(\d+))?$")
_MEM = re.compile(r"^([\d.]+)([KMGT]?)([nc]?)$", re.IGNORECASE)

# sacct's two-part duration is ambiguous and the columns disagree about it.
# Timelimit, Elapsed and Planned are walltimes, written [DD-]HH:MM. TotalCPU is
# consumed CPU time, written [DD-][HH:]MM:SS[.mmm]. Reading a TotalCPU of
# "11:20.500" as eleven hours instead of eleven minutes inflates it 60x, and
# CPU efficiency is the number every advisory note is built on -- so the caller
# says which grammar it means rather than the parser guessing.
HOURS_MINUTES, MINUTES_SECONDS = "hm", "ms"


def duration_minutes(text: str, style: str = HOURS_MINUTES) -> float | None:
    """A sacct duration in minutes.

    style=HOURS_MINUTES: [DD-]HH:MM:SS[.fff] or [DD-]HH:MM   (walltime columns)
    style=MINUTES_SECONDS: same three-part form, but a two-part
    value reads as MM:SS[.mmm]                               (TotalCPU)
    """
    t = (text or "").strip()
    if t in ("", "UNLIMITED", "Partition_Limit", "INVALID", "NOT_SET"):
        return None
    m = _DUR.match(t)
    if m:
        d, h, mi, sec, frac = m.groups()
        seconds = int(sec) + float(f"0.{frac}") if frac else int(sec)
        return int(d or 0) * 1440 + int(h) * 60 + int(mi) + seconds / 60
    m = _TWO.match(t)
    if m:
        d, first, second, frac = m.groups()
        if style == MINUTES_SECONDS:
            seconds = int(second) + float(f"0.{frac}") if frac else int(second)
            return int(d or 0) * 1440 + int(first) + seconds / 60
        return int(d or 0) * 1440 + int(first) * 60 + int(second)
    return None


def mem_mb(reqmem: str, nnodes: int, ncpus: int) -> float | None:
    """ReqMem -> total MB for the job. Handles 4Gn / 4Gc (per node / per cpu),
    plain 300G / 512000M (per job on newer Slurm), and bare bytes (--noconvert)."""
    t = (reqmem or "").strip()
    if not t or t == "0":
        return None
    if t.isdigit():
        # Bare digits are ambiguous across Slurm versions: bytes on some
        # (--noconvert), MB on others. Nobody requests under 10 MB, so:
        v = int(t)
        return v / 1048576 if v >= 1e7 else float(v)
    m = _MEM.match(t)
    if not m:
        return None
    value = float(m.group(1))
    # Slurm's K/M/G/T are KiB/MiB/GiB/TiB. Reading them as decimal made 4G into
    # 4000 MB rather than 4096, and the ReqMem and MaxRSS errors ran in opposite
    # directions -- compounding on the memory-efficiency ratio rather than
    # cancelling.
    scale = {"": 1 / 1048576, "K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1048576.0}[
        m.group(2).upper()]
    total = value * scale
    per = m.group(3).lower()
    if per == "n":
        total *= max(nnodes, 1)
    elif per == "c":
        total *= max(ncpus, 1)
    return total


def gpus_from_reqtres(reqtres: str) -> int:
    """ReqTRES like 'cpu=128,mem=480G,node=1,billing=128,gres/gpu=2,gres/gpu:a100=2'."""
    total = 0
    for part in (reqtres or "").split(","):
        if part.startswith("gres/gpu=") or re.match(r"^gres/gpu:[^=]+=", part):
            try:
                total = max(total, int(part.split("=", 1)[1]))
            except ValueError:
                pass
    return total


def parse_ids(jobid: str, jobidraw: str) -> tuple[int, int, int, bool]:
    """'1234' / '1234_7' / '1234.batch' -> (job_id, array_job_id, array_task_id, is_step)."""
    base, dot, _step = jobid.partition(".")
    is_step = bool(dot)
    m = re.match(r"^(\d+)_(\d+)$", base)
    if m:
        array_job_id, task = int(m.group(1)), int(m.group(2))
        raw_base = (jobidraw or "").partition(".")[0]
        job_id = int(raw_base) if raw_base.isdigit() else array_job_id
        return job_id, array_job_id, task, is_step
    if re.match(r"^\d+_\[", base):        # pending array container, e.g. 1234_[5-99]
        return int(base.split("_")[0]), int(base.split("_")[0]), -1, is_step
    # Heterogeneous job components are written 1234+0. int() raised on the '+',
    # and because the parse runs inside a pandas combine, one such row killed
    # the whole conversion with a traceback naming neither row nor column.
    base = base.split("+")[0]
    if not base.isdigit():
        return -1, -1, -1, is_step
    return int(base), -1, -1, is_step


def clean_state(state: str) -> str:
    return (state or "").split(" ")[0]     # 'CANCELLED by 12345' -> 'CANCELLED'


SLURM_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def to_utc(series: pd.Series, tz: str) -> pd.Series:
    """Local sacct timestamps to naive UTC.

    ambiguous/nonexistent were 'NaT', which silently deleted an hour of the year
    at each daylight-saving transition -- per column and independently, so one
    job could end up with a null start and a valid end. A transition now costs
    at most an hour of accuracy instead of the whole value.
    """
    local = pd.to_datetime(series, format=SLURM_TIME_FORMAT, errors="coerce")
    return (local.dt.tz_localize(ZoneInfo(tz), ambiguous=False,
                                 nonexistent="shift_forward")
                 .dt.tz_convert("UTC").dt.tz_localize(None))


def convert(dump_path: str, tz: str, accounts_csv: str | None,
            anonymise: bool) -> pd.DataFrame:
    raw = pd.read_csv(dump_path, sep="|", dtype=str, keep_default_na=False,
                      quoting=csv.QUOTE_NONE)
    need = {"JobID", "State", "Submit", "NNodes", "NCPUS"}
    missing = need - set(raw.columns)
    if missing:
        sys.exit(f"Export is missing columns {sorted(missing)}. "
                 "Re-run sacct with the --format line from this file's docstring.")
    if "Planned" not in raw.columns and "Reserved" in raw.columns:
        raw = raw.rename(columns={"Reserved": "Planned"})

    # `.get(col, "")` returns a bare string when the column is absent, and the
    # string then reached .map()/.dt and raised. The guard above says only five
    # columns are required, so the rest have to actually degrade.
    for column in ("JobIDRaw", "JobName", "User", "Account", "Partition", "QOS",
                   "ExitCode", "Eligible", "Start", "End", "Timelimit", "Elapsed",
                   "Planned", "ReqMem", "ReqTRES", "TotalCPU", "MaxRSS", "Reason"):
        if column not in raw.columns:
            raw[column] = ""

    ids = raw["JobID"].combine(raw["JobIDRaw"].where(raw["JobIDRaw"] != "", raw["JobID"]),
                               parse_ids)
    raw[["job_id", "array_job_id", "array_task_id", "_is_step"]] = \
        pd.DataFrame(ids.tolist(), index=raw.index)

    # MaxRSS lives on steps; everything else on the allocation row.
    steps = raw[raw["_is_step"]]
    rss = (steps.assign(_rss=steps.get("MaxRSS", "").map(
               lambda v: mem_mb(v, 1, 1) if str(v).strip() else None))
                .groupby("job_id")["_rss"].max())

    a = raw[~raw["_is_step"]].copy()
    if a.empty:
        sys.exit("No allocation rows found - is this a steps-only export?")

    nnodes = pd.to_numeric(a["NNodes"], errors="coerce").fillna(1).astype(int)
    ncpus = pd.to_numeric(a["NCPUS"], errors="coerce").fillna(1).astype(int)

    out = pd.DataFrame({
        "job_id": a["job_id"],
        "array_job_id": a["array_job_id"],
        "array_task_id": a["array_task_id"],
        "job_name": a.get("JobName", ""),
        "user": a.get("User", ""),
        "account": a.get("Account", ""),
        "partition": a.get("Partition", ""),
        "qos": a.get("QOS", ""),
        "state": a["State"].map(clean_state),
        "exit_code": a.get("ExitCode", ""),
        "submit_ts": to_utc(a["Submit"], tz),
        "eligible_ts": to_utc(a.get("Eligible", ""), tz),
        "start_ts": to_utc(a.get("Start", ""), tz),
        "end_ts": to_utc(a.get("End", ""), tz),
        "timelimit_min": a.get("Timelimit", "").map(duration_minutes),
        "elapsed_min": a.get("Elapsed", "").map(duration_minutes),
        "planned_min": a.get("Planned", "").map(duration_minutes),
        "req_nodes": nnodes,
        "req_cpus": ncpus,
        "req_mem_mb": [mem_mb(m, n, c) for m, n, c
                       in zip(a.get("ReqMem", ""), nnodes, ncpus)],
        "req_gpus": a.get("ReqTRES", "").map(gpus_from_reqtres),
        "total_cpu_min": a["TotalCPU"].map(
            lambda v: duration_minutes(v, style=MINUTES_SECONDS)),
        "last_reason": a.get("Reason", "").replace({"None": None, "": None}),
    })
    alloc_rss = a["MaxRSS"].map(lambda v: mem_mb(v, 1, 1) if str(v).strip() else None)
    alloc_rss.index = a["job_id"].values
    # An array's task 0 carries the container's JobIDRaw, so both rows derive the
    # same job_id and .map() refused the duplicated index -- aborting any export
    # taken while an array was mid-flight, which is the common case.
    alloc_rss = alloc_rss[~alloc_rss.index.duplicated()]
    rss = rss[~rss.index.duplicated()]
    out["max_rss_mb"] = out["job_id"].map(rss).fillna(out["job_id"].map(alloc_rss))
    out["est_start_ts"] = pd.NaT          # not retained by Slurm; see docstring
    out["gpu_util_pct"] = np.nan          # profiling data, not accounting data

    # Planned missing entirely (very old Slurm): derive it.
    if out["planned_min"].isna().all():
        out["planned_min"] = (
            (out["start_ts"] - out["eligible_ts"]).dt.total_seconds() / 60)

    out["project_name"], out["institution"] = out["account"], ""
    if accounts_csv:
        m = pd.read_csv(accounts_csv, dtype=str).set_index("account")
        out["project_name"] = out["account"].map(m["project_name"]).fillna(out["account"])
        out["institution"] = out["account"].map(m.get("institution", pd.Series(dtype=str))).fillna("")

    if anonymise:
        # Documented as "stable pseudonyms" in the docstring and in --help, but
        # a fresh random salt per run relabelled everyone: u0001 in this month's
        # file was a different person from u0001 in last month's. Keep the salt
        # somewhere safe and pass it in; the default is stable but public, so it
        # only shuffles the names, it does not hide them.
        salt = os.environ.get("KOWHAI_ANON_SALT", "kowhai")
        order = {u: i + 1 for i, u in enumerate(sorted(
            out["user"].unique(),
            key=lambda u: hashlib.sha256((salt + u).encode()).hexdigest()))}
        out["user"] = out["user"].map(lambda u: f"u{order[u]:04d}")

    # A job cancelled while pending reports Elapsed=00:00:00 and TotalCPU=00:00:00,
    # which parsed to 0. The domain notes promise the agent these rows are NULL
    # and "dropped by every average" -- with 0 they are not dropped: they drag
    # the average down and make total_cpu_min / (req_cpus * elapsed_min) a
    # division by zero. make_workshop_data.py already nulls them.
    never_ran = out["start_ts"].isna()
    out.loc[never_ran, ["elapsed_min", "total_cpu_min", "max_rss_mb"]] = None

    for c in ["elapsed_min", "timelimit_min", "max_rss_mb", "req_mem_mb"]:
        # .round() on an object-dtype column calls round() per element, and a
        # column that is entirely None stays object -- so a blank MaxRSS, which
        # every real export has on its allocation rows, aborted the whole run.
        out[c] = out[c].astype(float).round().astype("Int64")
    out["planned_min"] = out["planned_min"].astype(float).round(1)
    out["total_cpu_min"] = out["total_cpu_min"].astype(float).round(1)
    return out[TARGET_COLUMNS].sort_values("submit_ts").reset_index(drop=True)


def reconcile(raw_rows: int, out: pd.DataFrame) -> str:
    """What the conversion lost, so a silent failure is not a silent success.

    Everything above fails softly by design -- an unparseable duration or a
    coerced timestamp becomes NULL rather than an error. That is only safe if
    somebody is told how often it happened.
    """
    lines = [f"{raw_rows:,} rows in, {len(out):,} allocations out."]
    for column in ("submit_ts", "start_ts", "elapsed_min", "total_cpu_min",
                   "req_mem_mb", "max_rss_mb", "planned_min"):
        missing = int(out[column].isna().sum())
        if missing:
            lines.append(f"  {column}: {missing:,} null ({missing / max(len(out), 1):.0%})")
    started = out[out["start_ts"].notna()]
    if len(started):
        impossible = int((started["total_cpu_min"] >
                          started["req_cpus"] * started["elapsed_min"] * 1.05).sum())
        if impossible:
            lines.append(f"  WARNING: {impossible:,} jobs report more CPU time than "
                         "their allocation could supply — check the TotalCPU format")
        out_of_order = int((started["start_ts"] < started["submit_ts"]).sum())
        if out_of_order:
            lines.append(f"  WARNING: {out_of_order:,} jobs start before they were submitted")
    return "\n".join(lines)


def derive_sched(jobs: pd.DataFrame, out_path: str) -> None:
    """Rebuild sched_15m from the jobs themselves (see docstring caveat)."""
    import duckdb
    con = duckdb.connect()
    con.register("j", jobs)
    lo = jobs["submit_ts"].min().floor("15min")
    hi = max(jobs["end_ts"].max(), jobs["start_ts"].max()).ceil("15min")
    s = con.sql(f"""
        WITH b AS (SELECT UNNEST(generate_series(TIMESTAMP '{lo}', TIMESTAMP '{hi}',
                                                 INTERVAL 15 MINUTE)) AS ts),
        grid AS (SELECT b.ts, p.partition FROM b
                 CROSS JOIN (SELECT DISTINCT partition FROM j) p),
        run AS (SELECT g.ts, g.partition, COUNT(*) AS jobs_running,
                       SUM(j.req_nodes) AS nodes_alloc, SUM(j.req_cpus) AS cpus_alloc
                FROM grid g JOIN j ON j.partition = g.partition
                  AND j.start_ts <= g.ts AND j.end_ts > g.ts GROUP BY 1, 2),
        pend AS (SELECT g.ts, g.partition, COUNT(*) AS jobs_pending,
                        SUM(j.req_cpus) AS cpus_pending_requested,
                        MAX(date_diff('minute', j.eligible_ts, g.ts)) AS oldest_pending_min
                 FROM grid g JOIN j ON j.partition = g.partition
                   AND j.eligible_ts <= g.ts
                   -- start_ts IS NULL means the job never started. The old
                   -- predicate compared NULL > ts, which is NULL, not true, so
                   -- every job that gave up waiting was missing from the
                   -- backlog for the entire time it was actually queued.
                   AND (j.start_ts > g.ts
                        OR (j.start_ts IS NULL
                            AND (j.end_ts IS NULL OR j.end_ts > g.ts)))
                 GROUP BY 1, 2)
        SELECT g.ts, g.partition,
               COALESCE(r.jobs_running, 0) AS jobs_running,
               COALESCE(r.nodes_alloc, 0) AS nodes_alloc,
               COALESCE(r.cpus_alloc, 0) AS cpus_alloc,
               COALESCE(p2.jobs_pending, 0) AS jobs_pending,
               COALESCE(p2.cpus_pending_requested, 0) AS cpus_pending_requested,
               COALESCE(p2.oldest_pending_min, 0) AS oldest_pending_min
        FROM grid g LEFT JOIN run r USING (ts, partition)
                    LEFT JOIN pend p2 USING (ts, partition) ORDER BY 1, 2
    """).df()
    peak = s.groupby("partition")["nodes_alloc"].max().rename("nodes_total")
    s = s.merge(peak, on="partition")
    s["nodes_total"] = s["nodes_total"].clip(lower=1).astype(int)
    busy = s[s["nodes_alloc"] > 0]
    cores_per_node = (busy["cpus_alloc"] / busy["nodes_alloc"]).groupby(
        busy["partition"]).max().round()
    s["cpus_total"] = (s["nodes_total"] *
                       s["partition"].map(cores_per_node).fillna(128)).astype(int)
    # Accounting data cannot see a drained node or a reservation. Writing 0
    # told the agent "none were drained" with full confidence; NULL tells it the
    # truth, which is that this table cannot answer that question.
    s["nodes_down_drain"] = pd.NA
    s["reservation_nodes"] = pd.NA
    # Documented as nodes_total - nodes_alloc - nodes_down_drain. With an
    # unknown drain count the remainder is unknown too, so it stays NULL rather
    # than reporting every partition as 100% busy at its own observed peak.
    s["nodes_idle"] = pd.NA
    s.to_parquet(out_path, index=False)
    print(f"wrote {out_path}: {len(s):,} rows.\n"
          "  nodes_total and cpus_total are inferred from observed peaks, not from\n"
          "  sinfo, so they are a lower bound. nodes_down_drain, reservation_nodes\n"
          "  and nodes_idle are NULL: accounting data cannot see them.")


SQUEUE_START_SAMPLER = r"""
# Optional: collect est_start_ts going forward. Run every 15 minutes from cron:
#   */15 * * * *  /path/to/sample_squeue_start.sh >> /var/log/squeue_start.tsv
# sample_squeue_start.sh:
#   squeue --start --noheader -o "%A|%S" | awk -v now="$(date -uIs)" -F'|' \
#     '$2 != "N/A" {print now"|"$1"|"$2}'
# Keep each job's FIRST estimate, convert to UTC, and merge into jobs.parquet
# on job_id as est_start_ts.
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="output of the sacct command in the docstring")
    ap.add_argument("--tz", required=True,
                    help="time zone sacct printed in, e.g. Pacific/Auckland")
    ap.add_argument("--out", default="data")
    ap.add_argument("--accounts", help="CSV: account,project_name,institution")
    ap.add_argument("--anonymise", action="store_true",
                    help="pseudonymise usernames (NOT anonymisation: account, "
                         "project_name, job_name, job_id and exact timestamps are "
                         "kept, which is enough to re-identify). Set KOWHAI_ANON_SALT "
                         "to a secret you reuse, or pseudonyms are only shuffled, "
                         "not hidden.")
    ap.add_argument("--derive-sched", action="store_true",
                    help="also rebuild sched_15m.parquet from these jobs")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    with open(args.dump, encoding="utf-8") as fh:
        raw_rows = sum(1 for _ in fh) - 1
    jobs = convert(args.dump, args.tz, args.accounts, args.anonymise)
    jp = os.path.join(args.out, "jobs.parquet")
    jobs.to_parquet(jp, index=False)
    print(f"wrote {jp}: {len(jobs):,} allocations, "
          f"{jobs['submit_ts'].min()} to {jobs['submit_ts'].max()} UTC")
    print(reconcile(raw_rows, jobs))
    if args.derive_sched:
        derive_sched(jobs, os.path.join(args.out, "sched_15m.parquet"))
    print(SQUEUE_START_SAMPLER)
