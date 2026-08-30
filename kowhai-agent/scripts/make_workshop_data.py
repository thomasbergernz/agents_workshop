"""Synthetic Slurm accounting data for the Kowhai workshop.

Stands in for `sacct` and `sinfo` on a real cluster. You do not need to read this
to do the workshop; skip to Part 1 once it has run.
"""
import os

import duckdb
import numpy as np
import pandas as pd

SEED = 62
UTC_START = np.datetime64("2026-07-05T12:00")   # Mon 6 Jul 00:00 NZST
DAYS = 28
MINUTES = DAYS * 24 * 60
TZ = 12 * 60  # NZST minutes ahead of UTC

PARTITIONS = {
    "debug":   {"nodes": 4,   "cpn": 128, "mem": 480_000,   "gpus": 0, "max_min": 15},
    "large":   {"nodes": 240, "cpn": 128, "mem": 480_000,   "gpus": 0, "max_min": 4320},
    "long":    {"nodes": 40,  "cpn": 128, "mem": 480_000,   "gpus": 0, "max_min": 20160},
    "hugemem": {"nodes": 6,   "cpn": 128, "mem": 4_030_000, "gpus": 0, "max_min": 10080},
    "gpu":     {"nodes": 24,  "cpn": 64,  "mem": 480_000,   "gpus": 4, "max_min": 1440},
}

PROJECTS = [
    ("uoa03521", "Te Whare Wānanga o Tāmaki Makaurau — Molecular Dynamics", "University of Auckland", "Chemistry"),
    ("uoa04412", "Te Whare Wānanga o Tāmaki Makaurau — Kauri Dieback Metagenomics", "University of Auckland", "Biology"),
    ("uow02918", "Te Whare Wānanga o Waikato — Coastal Ocean Modelling", "University of Waikato", "Earth Sciences"),
    ("vuw03102", "Te Herenga Waka — Seismic Wave Propagation", "Victoria University of Wellington", "Earth Sciences"),
    ("uoo02755", "Te Whare Wānanga o Ōtākou — Genomic Epidemiology", "University of Otago", "Biology"),
    ("cawt01130", "Cawthron Institute — Harmful Algal Bloom Forecasting", "Cawthron Institute", "Biology"),
    ("niwa02901", "NIWA — Regional Climate Downscaling", "NIWA", "Earth Sciences"),
    ("niwa03340", "NIWA — Antarctic Sea Ice Reanalysis", "NIWA", "Earth Sciences"),
    ("mwlr01862", "Manaaki Whenua — Land Cover Classification", "Manaaki Whenua Landcare Research", "Earth Sciences"),
    ("plnt02211", "Plant & Food Research — Kiwifruit Pangenome", "Plant and Food Research", "Biology"),
    ("agr01204",  "AgResearch — Ruminant Methane Modelling", "AgResearch", "Biology"),
    ("uoc02640", "Te Whare Wānanga o Waitaha — Computational Fluid Dynamics", "University of Canterbury", "Engineering"),
    ("uoc03881", "Te Whare Wānanga o Waitaha — Gravitational Wave Search", "University of Canterbury", "Physics"),
    ("mas02409", "Te Kunenga ki Pūrehuroa — Protein Structure Prediction", "Massey University", "Biology"),
    ("aut01527", "Te Wānanga Aronui o Tāmaki Makau Rau — Neural Speech Models", "Auckland University of Technology", "Computer Science"),
    ("gns02066", "GNS Science — Geothermal Reservoir Simulation", "GNS Science", "Earth Sciences"),
    ("lcr01998", "Te Pūnaha Matatini — Epidemic Network Models", "Te Pūnaha Matatini", "Mathematics"),
    ("mfe00812", "Ministry for the Environment — Freshwater Quality Modelling", "Ministry for the Environment", "Earth Sciences"),
]

FIRSTS = ["hana", "rewi", "mere", "tane", "aroha", "kiri", "manaia", "ngaio", "rangi", "tui",
          "james", "sarah", "wei", "priya", "chen", "aditi", "olivia", "liam", "noah", "emma",
          "hemi", "ana", "raj", "yuki", "sofia", "ben", "grace", "leo", "maia", "finn"]
LASTS = ["kerekere", "ngata", "clifton", "harrison", "zhang", "patel", "wilson", "obrien",
         "tahana", "murray", "singh", "nakamura", "lopez", "brown", "walker", "cheng",
         "waititi", "reid", "fraser", "kumar"]


def local_hour(ts_utc):
    """UTC_START is exactly Monday 00:00 NZST, so offsets are measured from it."""
    m = (ts_utc - UTC_START) / np.timedelta64(1, "m")
    return (m / 60.0) % 24.0


def local_dow(ts_utc):
    # 0 = Monday local
    m = (ts_utc - UTC_START) / np.timedelta64(1, "m")
    return ((m // (24 * 60)).astype(int)) % 7


# Submissions per local hour, relative to the 11:00 peak. A working day, not a curve.
HOURLY = np.array([
    0.11, 0.08, 0.05, 0.04, 0.04, 0.06, 0.11, 0.22,   # 00-07
    0.46, 0.80, 0.95, 1.00, 0.84, 0.90, 0.95, 0.90,   # 08-15
    0.84, 0.68, 0.50, 0.40, 0.34, 0.28, 0.21, 0.15,   # 16-23
])


def submit_intensity(ts):
    h = local_hour(ts).astype(int) % 24
    d = local_dow(ts)
    week = np.where(d >= 5, 0.45, 1.0)
    return np.clip(HOURLY[h] * week, 0.02, 1.0)


def sample_submits(n, rng, spread=1.0):
    """Diurnal + weekly submission pattern in local time, returned as UTC."""
    out = []
    while sum(len(o) for o in out) < n:
        cand = UTC_START + (rng.random(n * 3) * MINUTES).astype("timedelta64[m]")
        keep = cand[rng.random(cand.size) < submit_intensity(cand) ** spread]
        out.append(keep)
    return np.sort(np.concatenate(out)[:n])


def cluster_load(ts, partition, rng):
    """0-1 pressure on the queue at this moment."""
    h = local_hour(ts)
    d = local_dow(ts)
    base = 0.55 + 0.32 * np.exp(-0.5 * ((h - 13.0) / 5.0) ** 2)
    base = np.where(d >= 5, base - 0.16, base)
    # the incident: Sat 25 - Mon 27 July NZST, `large` saturated
    inc0 = np.datetime64("2026-07-24T12:00")   # Sat 25 Jul 00:00 NZST
    inc1 = np.datetime64("2026-07-27T06:00")   # Mon 27 Jul 18:00 NZST
    if partition in ("large", "debug"):
        base = np.where((ts >= inc0) & (ts < inc1), np.minimum(base + 0.42, 1.10), base)
    # maintenance reservation: Tue 21 Jul 08:00-14:00 NZST drains 40 large nodes
    m0 = np.datetime64("2026-07-20T20:00")
    m1 = np.datetime64("2026-07-21T02:00")
    if partition == "large":
        base = np.where((ts >= m0) & (ts < m1), base + 0.18, base)
    return np.clip(base + rng.normal(0, 0.06, ts.size), 0.05, 1.15)


def wait_minutes(ts, partition, req_nodes, qos, fairshare, rng):
    load = cluster_load(ts, partition, rng)
    size = 1.0 + np.log1p(req_nodes) * 1.35
    pressure = np.exp(4.6 * (load - 0.55))
    base = {"debug": 0.6, "large": 4.0, "long": 18.0, "hugemem": 26.0, "gpu": 14.0}[partition]
    mu = base * size * pressure * fairshare
    w = rng.lognormal(np.log(np.maximum(mu, 0.3)), 0.85)
    w = np.where(qos == "debug", w * 0.08, w)
    return np.clip(w, 0.05, 60 * 30)


def make_block(rng, n, partition, name_pool, spread, req_nodes, cpus_per_node_used,
               mem_frac, timelimit, walltime_use, cpu_eff, mem_eff, gpus=0,
               gpu_util=None, dep_mean=0.0, proj_idx=None, user_idx=None):
    p = PARTITIONS[partition]
    submit = sample_submits(n, rng, spread)
    req_nodes = np.asarray(req_nodes)
    req_cpus = req_nodes * cpus_per_node_used
    req_mem = np.round(p["mem"] * mem_frac * req_nodes / 1000.0) * 1000.0

    tl = np.clip(np.round(timelimit), 5, p["max_min"])
    elapsed = np.clip(np.round(tl * walltime_use), 1, tl)

    state = rng.choice(
        ["COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "OUT_OF_MEMORY", "NODE_FAIL"],
        size=n, p=[0.855, 0.070, 0.032, 0.030, 0.010, 0.003])
    elapsed = np.where(state == "TIMEOUT", tl, elapsed)
    elapsed = np.where(state == "FAILED", np.maximum(1, np.round(elapsed * rng.beta(1.2, 3.0, n))), elapsed)
    elapsed = np.where(state == "OUT_OF_MEMORY", np.maximum(1, np.round(elapsed * rng.beta(1.5, 3.0, n))), elapsed)
    elapsed = np.where(state == "NODE_FAIL", np.maximum(1, np.round(elapsed * rng.beta(1.2, 2.0, n))), elapsed)
    cancelled_pending = (state == "CANCELLED") & (rng.random(n) < 0.35)
    elapsed = np.where((state == "CANCELLED") & ~cancelled_pending,
                       np.maximum(1, np.round(elapsed * rng.beta(1.0, 4.0, n))), elapsed)

    eff = np.clip(cpu_eff, 0.002, 1.0)
    total_cpu = np.round(req_cpus * elapsed * eff, 1)
    rss = np.round(req_mem * np.clip(mem_eff, 0.01, 1.35))
    rss = np.where(state == "OUT_OF_MEMORY", np.round(req_mem * rng.uniform(0.97, 1.0, n)), rss)
    rss = np.minimum(rss, req_mem * 1.02)

    fairshare = rng.uniform(0.6, 1.9, n)
    qos = np.where(np.full(n, partition == "debug"), "debug", "normal")
    dep = np.where(rng.random(n) < (0.85 if dep_mean > 0 else 0.0),
                   rng.exponential(max(dep_mean, 1e-6), n), 0.0)
    dep = np.where(rng.random(n) < 0.03, dep + rng.uniform(600, 4300, n), dep)
    eligible = submit + np.round(dep).astype("timedelta64[m]")

    planned = wait_minutes(eligible, partition, req_nodes, qos, fairshare, rng)
    start = eligible + np.round(planned).astype("timedelta64[m]")
    end = start + elapsed.astype("timedelta64[m]")

    df = pd.DataFrame({
        "job_name": rng.choice(name_pool, n),
        "partition": partition, "qos": qos,
        "submit_ts": submit, "eligible_ts": eligible, "start_ts": start, "end_ts": end,
        "state": state, "req_nodes": req_nodes, "req_cpus": req_cpus,
        "req_mem_mb": req_mem.astype(np.int64), "req_gpus": req_nodes * gpus,
        "timelimit_min": tl.astype(np.int64), "elapsed_min": elapsed.astype(np.int64),
        "planned_min": np.round(planned, 1), "total_cpu_min": total_cpu,
        "max_rss_mb": rss.astype(np.int64),
        "gpu_util_pct": (np.round(gpu_util, 1) if gpu_util is not None else np.nan),
        "_cancelled_pending": cancelled_pending,
        "_proj": proj_idx if proj_idx is not None else rng.integers(0, len(PROJECTS), n),
        "_user": user_idx if user_idx is not None else rng.integers(0, 340, n),
    })
    return df


def build(rng):
    blocks = []

    # 1. Nextflow / Snakemake pipeline tasks - dependency held, small, efficient
    n = 46000
    blocks.append(make_block(
        rng, n, "large",
        ["nf-FASTQC", "nf-BWAMEM", "nf-MARKDUP", "nf-HAPLOTYPECALLER", "snakemake-align",
         "nf-MULTIQC", "snakemake-count"],
        spread=0.7,
        req_nodes=np.ones(n, int), cpus_per_node_used=rng.choice([2, 4, 8, 16], n, p=[.3, .35, .25, .1]),
        mem_frac=rng.uniform(0.03, 0.18, n),
        timelimit=rng.choice([60, 120, 240, 480], n, p=[.35, .35, .2, .1]),
        walltime_use=rng.beta(1.6, 3.2, n),
        cpu_eff=rng.beta(7, 2.4, n),
        mem_eff=rng.beta(2.2, 3.0, n),
        dep_mean=95.0))

    # 2. MPI simulation - multi node, well tuned
    n = 2200
    nodes = rng.choice([1, 2, 4, 8, 16, 32], n, p=[.34, .28, .19, .11, .06, .02])
    blocks.append(make_block(
        rng, n, "large",
        ["gromacs_prod", "wrf_nested", "openfoam_les", "specfem3d", "nemo_ocean", "vasp_relax"],
        spread=1.0,
        req_nodes=nodes, cpus_per_node_used=128,
        mem_frac=rng.uniform(0.25, 0.75, n),
        timelimit=rng.choice([360, 720, 1440, 2880], n, p=[.3, .35, .25, .1]),
        walltime_use=rng.beta(2.6, 2.0, n),
        cpu_eff=rng.beta(14, 1.9, n),
        mem_eff=rng.beta(3.0, 2.4, n)))

    # 3. Serial R / Python asking for a whole node - the waste
    n = 7200
    cpus = rng.choice([32, 64, 128], n, p=[.4, .3, .3])
    blocks.append(make_block(
        rng, n, "large",
        ["run_model.R", "analysis.R", "bootstrap.R", "process.py", "fit_glmm.R"],
        spread=0.9,
        req_nodes=np.ones(n, int), cpus_per_node_used=cpus,
        mem_frac=rng.uniform(0.3, 0.95, n),
        timelimit=rng.choice([240, 480, 1440, 2880], n, p=[.25, .3, .3, .15]),
        walltime_use=rng.beta(1.3, 4.0, n),
        cpu_eff=rng.uniform(0.9, 1.7, n) / cpus,   # single threaded on a whole node
        mem_eff=rng.beta(1.3, 6.0, n)))

    # 4. GPU training - low CPU efficiency by design
    n = 8600
    gu = np.clip(rng.beta(4.5, 2.0, n) * 100, 3, 99)
    blocks.append(make_block(
        rng, n, "gpu",
        ["train_asr", "finetune_llm", "alphafold_gpu", "torch_ddp", "cryosparc_gpu"],
        spread=0.9,
        req_nodes=np.ones(n, int), cpus_per_node_used=rng.choice([4, 8, 16], n, p=[.3, .5, .2]),
        mem_frac=rng.uniform(0.1, 0.5, n),
        timelimit=rng.choice([240, 480, 720, 1440], n, p=[.2, .3, .3, .2]),
        walltime_use=rng.beta(2.0, 2.4, n),
        cpu_eff=rng.beta(1.6, 9.0, n),
        mem_eff=rng.beta(2.0, 3.0, n),
        gpus=rng.choice([1, 2, 4], n, p=[.6, .25, .15]), gpu_util=gu))

    # 5. Interactive / MATLAB - huge walltime over-request
    n = 6400
    blocks.append(make_block(
        rng, n, "large",
        ["matlab_session", "jupyter", "sinteractive", "ondemand_rstudio"],
        spread=1.3,
        req_nodes=np.ones(n, int), cpus_per_node_used=rng.choice([4, 8, 16, 32], n, p=[.3, .3, .25, .15]),
        mem_frac=rng.uniform(0.05, 0.4, n),
        timelimit=rng.choice([480, 1440, 2880], n, p=[.4, .4, .2]),
        walltime_use=rng.beta(1.0, 9.0, n),
        cpu_eff=rng.beta(1.4, 7.0, n),
        mem_eff=rng.beta(1.5, 5.0, n)))

    # 6. hugemem assembly
    n = 140
    blocks.append(make_block(
        rng, n, "hugemem",
        ["hifiasm", "spades_meta", "trinity_asm", "canu_correct"],
        spread=1.0,
        req_nodes=np.ones(n, int), cpus_per_node_used=rng.choice([64, 128], n, p=[.4, .6]),
        mem_frac=rng.uniform(0.35, 0.95, n),
        timelimit=rng.choice([1440, 2880, 5760, 10080], n, p=[.3, .3, .3, .1]),
        walltime_use=rng.beta(2.0, 2.5, n),
        cpu_eff=rng.beta(4.0, 3.0, n),
        mem_eff=rng.beta(3.0, 2.2, n)))

    # 7. long partition - climate runs
    n = 180
    nodes = rng.choice([1, 2, 4, 8], n, p=[.45, .3, .18, .07])
    blocks.append(make_block(
        rng, n, "long",
        ["cesm_hist", "roms_hindcast", "mom6_spinup", "chem_transport"],
        spread=1.0,
        req_nodes=nodes, cpus_per_node_used=128,
        mem_frac=rng.uniform(0.2, 0.6, n),
        timelimit=rng.choice([4320, 10080, 20160], n, p=[.4, .4, .2]),
        walltime_use=rng.beta(3.0, 2.0, n),
        cpu_eff=rng.beta(9.0, 2.2, n),
        mem_eff=rng.beta(2.6, 2.6, n)))

    # 8. debug
    n = 11000
    blocks.append(make_block(
        rng, n, "debug",
        ["test", "hello_mpi", "check_env", "quicktest", "debug_run"],
        spread=1.1,
        req_nodes=np.ones(n, int), cpus_per_node_used=rng.choice([1, 2, 4, 8], n, p=[.4, .3, .2, .1]),
        mem_frac=rng.uniform(0.02, 0.2, n),
        timelimit=np.full(n, 15),
        walltime_use=rng.beta(1.1, 5.0, n),
        cpu_eff=rng.beta(2.2, 2.6, n),
        mem_eff=rng.beta(1.5, 5.0, n)))

    # --- planted incident: 3,200 array tasks, 128 cpus each, single threaded ---
    n = 6000
    inc_submit = (np.datetime64("2026-07-24T12:00")
                  + np.round(rng.exponential(95, n)).astype("timedelta64[m]"))
    elapsed = np.round(rng.normal(64, 11, n)).clip(18, 140)
    inc = pd.DataFrame({
        "job_name": "kauri_bin_annotate",
        "partition": "large", "qos": "normal",
        "submit_ts": inc_submit, "eligible_ts": inc_submit,
        "state": "COMPLETED", "req_nodes": 1, "req_cpus": 128, "req_mem_mb": 480000, "req_gpus": 0,
        "timelimit_min": 1440, "elapsed_min": elapsed.astype(np.int64),
        "total_cpu_min": np.round(elapsed * rng.uniform(0.95, 1.25, n), 1),
        "max_rss_mb": np.round(rng.uniform(9000, 26000, n)).astype(np.int64),
        "gpu_util_pct": np.nan,
        "_cancelled_pending": False,
        "_proj": 1,          # uoa04412 Kauri Dieback Metagenomics
        "_user": 7,
    })
    # they trickle through the queue over ~2.5 days
    order = np.argsort(rng.random(n))
    slot = np.zeros(n)
    slot[order] = np.linspace(20, 3600, n) + rng.normal(0, 90, n)
    inc["planned_min"] = np.round(np.clip(slot, 5, None), 1)
    inc["start_ts"] = inc["eligible_ts"] + pd.to_timedelta(inc["planned_min"].round(), unit="m")
    inc["end_ts"] = inc["start_ts"] + pd.to_timedelta(inc["elapsed_min"], unit="m")
    blocks.append(inc)

    # --- second, quieter anomaly: same job resubmitted, always TIMEOUT ---
    n = 41
    sub = (np.datetime64("2026-07-06T00:00")
           + np.round(np.linspace(0, 26 * 1440, n) + rng.normal(0, 120, n)).astype("timedelta64[m]"))
    tmo = pd.DataFrame({
        "job_name": "seismic_inv_full", "partition": "large", "qos": "normal",
        "submit_ts": sub, "eligible_ts": sub, "state": "TIMEOUT",
        "req_nodes": 2, "req_cpus": 256, "req_mem_mb": 960000, "req_gpus": 0,
        "timelimit_min": 4320, "elapsed_min": 4320,
        "total_cpu_min": np.round(256 * 4320 * rng.uniform(0.80, 0.93, n), 1),
        "max_rss_mb": np.round(rng.uniform(180000, 320000, n)).astype(np.int64),
        "gpu_util_pct": np.nan, "_cancelled_pending": False, "_proj": 3, "_user": 2,
        "planned_min": np.round(rng.lognormal(np.log(140), 0.8, n), 1),
    })
    tmo["start_ts"] = tmo["eligible_ts"] + pd.to_timedelta(tmo["planned_min"].round(), unit="m")
    tmo["end_ts"] = tmo["start_ts"] + pd.to_timedelta(tmo["elapsed_min"], unit="m")
    blocks.append(tmo)

    df = pd.concat(blocks, ignore_index=True)
    df = df[df["submit_ts"] < np.datetime64("2026-08-02T12:00")].copy()
    df = df.sort_values("submit_ts").reset_index(drop=True)

    # identity columns
    proj = np.array(PROJECTS, dtype=object)
    df["account"] = proj[df["_proj"].values, 0]
    df["project_name"] = proj[df["_proj"].values, 1]
    df["institution"] = proj[df["_proj"].values, 2]
    users = np.array([f"{FIRSTS[i % len(FIRSTS)][0]}{LASTS[(i * 7) % len(LASTS)]}{'' if i < 20 else i % 90}"
                      for i in range(340)])
    df["user"] = users[df["_user"].values % 340]
    df.loc[df["_proj"] == 1, "user"] = np.where(
        rng.random((df["_proj"] == 1).sum()) < 0.55, "hkerekere",
        users[rng.integers(0, 340, (df["_proj"] == 1).sum())])
    df.loc[(df["_proj"] == 1) & (df["job_name"] == "kauri_bin_annotate"), "user"] = "hkerekere"
    df.loc[df["job_name"] == "seismic_inv_full", "user"] = "rclifton"

    df["job_id"] = np.arange(4_180_000, 4_180_000 + len(df))
    is_array = df["job_name"].str.startswith(("nf-", "snakemake")).values
    seq = np.cumsum(is_array) - 1
    df["array_task_id"] = np.where(is_array, seq % 250, -1)
    df["array_job_id"] = np.where(is_array, 5_000_000 + seq // 250, -1)
    kauri = (df["job_name"] == "kauri_bin_annotate").values
    df.loc[kauri, "array_job_id"] = 5_900_017
    df.loc[kauri, "array_task_id"] = np.arange(kauri.sum())

    # cancelled-while-pending jobs never start
    cp = df["_cancelled_pending"].fillna(False).values.astype(bool)
    for c in ["start_ts", "end_ts"]:
        df[c] = df[c].astype("datetime64[ns]")
        df.loc[cp, c] = pd.NaT
    df.loc[cp, ["elapsed_min", "total_cpu_min", "max_rss_mb", "planned_min"]] = np.nan

    # Slurm's estimated start time, produced by the backfill scheduler.
    # Pessimistic, because it assumes every running job uses its full time limit.
    nn = len(df)
    factor = rng.lognormal(np.log(3.1), 0.62, nn)
    factor = np.where(rng.random(nn) < 0.09, rng.uniform(0.35, 0.9, nn), factor)
    est = df["eligible_ts"].values + pd.to_timedelta(
        np.round(df["planned_min"].fillna(0).values * factor), unit="m")
    has_est = (df["planned_min"].fillna(0) > 4) & (rng.random(nn) < 0.72) & ~cp
    df["est_start_ts"] = np.where(has_est, est, np.datetime64("NaT"))

    reason = np.where(df["planned_min"].fillna(0) > 4, "Priority", None)
    big = df["req_nodes"] >= 8
    reason = np.where(big & (df["planned_min"].fillna(0) > 4), "Resources", reason)
    dep_held = (df["eligible_ts"] - df["submit_ts"]) > pd.Timedelta(minutes=2)
    reason = np.where(dep_held, "Dependency", reason)
    maint = (df["start_ts"] >= np.datetime64("2026-07-20T20:00")) & \
            (df["start_ts"] < np.datetime64("2026-07-21T04:00")) & (df["partition"] == "large")
    reason = np.where(maint & (df["planned_min"].fillna(0) > 30),
                      "ReqNodeNotAvail, Reserved for maintenance", reason)
    df["last_reason"] = reason
    df.loc[dep_held, "est_start_ts"] = pd.NaT  # no estimate while a dependency is unmet

    df["exit_code"] = np.where(df["state"] == "COMPLETED", "0:0",
                               np.where(df["state"] == "OUT_OF_MEMORY", "0:125",
                                        np.where(df["state"] == "TIMEOUT", "0:15", "1:0")))
    # sacct export taken at the end of the window: jobs still running are not in it
    df = df[df["end_ts"].notna() & (df["end_ts"] < np.datetime64("2026-08-02T12:00"))
            | df["_cancelled_pending"].fillna(False)].copy()

    for c in ["submit_ts", "eligible_ts", "est_start_ts", "start_ts", "end_ts"]:
        df[c] = df[c].astype("datetime64[us]")
    for c in ["elapsed_min", "max_rss_mb", "timelimit_min", "req_cpus", "req_nodes"]:
        df[c] = df[c].astype("Int64")

    cols = ["job_id", "array_job_id", "array_task_id", "job_name", "user", "account", "project_name",
            "institution", "partition", "qos", "state", "exit_code",
            "submit_ts", "eligible_ts", "est_start_ts", "start_ts", "end_ts",
            "timelimit_min", "elapsed_min", "planned_min",
            "req_nodes", "req_cpus", "req_mem_mb", "req_gpus",
            "total_cpu_min", "max_rss_mb", "gpu_util_pct", "last_reason"]
    return df[cols]


def build_sched(jobs, rng):
    con = duckdb.connect()
    con.register("j", jobs)
    parts = pd.DataFrame([{"partition": k, "nodes_total": v["nodes"],
                               "cpus_total": v["nodes"] * v["cpn"]} for k, v in PARTITIONS.items()])
    con.register("p", parts)
    sql = """
    WITH b AS (
      SELECT UNNEST(generate_series(TIMESTAMP '2026-07-05 12:00',
                                    TIMESTAMP '2026-08-02 11:45',
                                    INTERVAL 15 MINUTE)) AS ts
    ),
    grid AS (SELECT b.ts, p.partition, p.nodes_total, p.cpus_total FROM b CROSS JOIN p),
    run AS (
      SELECT g.ts, g.partition,
             COUNT(*) AS jobs_running,
             SUM(j.req_nodes) AS nodes_alloc,
             SUM(j.req_cpus) AS cpus_alloc
      FROM grid g JOIN j ON j.partition = g.partition
        AND j.start_ts <= g.ts AND j.end_ts > g.ts
      GROUP BY 1, 2
    ),
    pend AS (
      SELECT g.ts, g.partition,
             COUNT(*) AS jobs_pending,
             SUM(j.req_cpus) AS cpus_pending_requested,
             MAX(date_diff('minute', j.eligible_ts, g.ts)) AS oldest_pending_min
      FROM grid g JOIN j ON j.partition = g.partition
        AND j.eligible_ts <= g.ts AND j.start_ts > g.ts
      GROUP BY 1, 2
    )
    SELECT g.ts, g.partition, g.nodes_total, g.cpus_total,
           COALESCE(r.jobs_running, 0) AS jobs_running,
           COALESCE(r.nodes_alloc, 0) AS nodes_alloc,
           COALESCE(r.cpus_alloc, 0) AS cpus_alloc,
           COALESCE(p2.jobs_pending, 0) AS jobs_pending,
           COALESCE(p2.cpus_pending_requested, 0) AS cpus_pending_requested,
           COALESCE(p2.oldest_pending_min, 0) AS oldest_pending_min
    FROM grid g LEFT JOIN run r USING (ts, partition)
                LEFT JOIN pend p2 USING (ts, partition)
    ORDER BY 1, 2
    """
    s = con.sql(sql).df()
    n = len(s)
    # nodes running work that is not in the accounting export (interactive/OnDemand)
    extra = np.round(s["nodes_total"] * rng.uniform(0.0, 0.05, n)).astype(int)
    s["nodes_alloc"] = np.minimum(s["nodes_alloc"] + extra, s["nodes_total"])
    s["cpus_alloc"] = np.minimum(s["cpus_alloc"] + extra * 128, s["cpus_total"])
    # drained / down nodes, plus the maintenance reservation on `large`
    down = np.round(rng.gamma(1.2, 1.1, n)).astype(int)
    maint = (s["ts"] >= "2026-07-20 20:00") & (s["ts"] < "2026-07-21 02:00") & (s["partition"] == "large")
    s["nodes_down_drain"] = np.minimum(down + np.where(maint, 40, 0), s["nodes_total"])
    s["nodes_alloc"] = np.minimum(s["nodes_alloc"], s["nodes_total"] - s["nodes_down_drain"])
    s["nodes_idle"] = s["nodes_total"] - s["nodes_alloc"] - s["nodes_down_drain"]
    s["reservation_nodes"] = np.where(maint, 40, 0)
    return s


def build_dataset(out_dir="data"):
    """Write jobs.parquet and sched_15m.parquet, once. Returns both paths."""
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "jobs.parquet")
    sp = os.path.join(out_dir, "sched_15m.parquet")
    if os.path.exists(jp) and os.path.exists(sp):
        print("dataset already built")
        return jp, sp
    rng = np.random.default_rng(SEED)
    jobs = build(rng)
    sched = build_sched(jobs, rng)
    jobs.to_parquet(jp, index=False)
    sched.to_parquet(sp, index=False)
    print(f"built {len(jobs):,} job records and {len(sched):,} scheduler samples")
    return jp, sp


if __name__ == "__main__":
    build_dataset()
