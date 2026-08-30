"""The real-cluster path. It had no tests; every case here is a reported defect."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sacct_to_parquet import convert, duration_minutes, mem_mb

COLUMNS = ("JobID|JobIDRaw|JobName|User|Account|Partition|QOS|State|ExitCode|Submit|Eligible|"
           "Start|End|Timelimit|Elapsed|Planned|NNodes|NCPUS|ReqMem|ReqTRES|TotalCPU|MaxRSS|Reason")


def dump(tmp_path, *rows, columns=COLUMNS):
    path = tmp_path / "sacct.txt"
    path.write_text(columns + "\n" + "\n".join(rows) + "\n")
    return str(path)


def row(**over):
    f = {"JobID": "1", "JobIDRaw": "1", "JobName": "g", "User": "arangi", "Account": "uoa03521",
             "Partition": "large", "QOS": "normal", "State": "COMPLETED", "ExitCode": "0:0",
             "Submit": "2026-07-06T01:00:00", "Eligible": "2026-07-06T01:00:00",
             "Start": "2026-07-06T01:10:00", "End": "2026-07-06T05:10:00", "Timelimit": "08:00:00",
             "Elapsed": "04:00:00", "Planned": "00:10:00", "NNodes": "1", "NCPUS": "128",
             "ReqMem": "4Gc", "ReqTRES": "cpu=128,node=1", "TotalCPU": "466-16:00:00",
             "MaxRSS": "", "Reason": "None"}
    f.update(over)
    return "|".join(str(f[c]) for c in COLUMNS.split("|"))


# --- crashes -----------------------------------------------------------------

def test_a_blank_maxrss_on_every_row_does_not_abort_the_run(tmp_path):
    """Universal in real exports: MaxRSS lives on step rows, not allocations."""
    out = convert(dump(tmp_path, row(), row(JobID="2", JobIDRaw="2")), "Pacific/Auckland",
                  None, False)
    assert str(out["max_rss_mb"].dtype) == "Int64"
    assert out["max_rss_mb"].isna().all()


def test_an_unlimited_timelimit_on_every_row_does_not_abort_the_run(tmp_path):
    out = convert(dump(tmp_path, row(Timelimit="UNLIMITED")), "Pacific/Auckland", None, False)
    assert out["timelimit_min"].isna().all()


def test_a_heterogeneous_job_component_does_not_abort_the_run(tmp_path):
    """Slurm writes het components as 1234+0."""
    out = convert(dump(tmp_path, row(JobID="1234+0", JobIDRaw="1234+0")),
                  "Pacific/Auckland", None, False)
    assert out["job_id"].tolist() == [1234]


def test_a_part_started_array_does_not_abort_the_run(tmp_path):
    """The container and its task 0 share a JobIDRaw, which collided in a map."""
    out = convert(dump(tmp_path,
                       row(JobID="5000100_[2-99]", JobIDRaw="5000100"),
                       row(JobID="5000100_0", JobIDRaw="5000100")),
                  "Pacific/Auckland", None, False)
    assert len(out) == 2


def test_an_export_with_only_the_required_columns_converts(tmp_path):
    """The guard says five columns are required, so the rest must degrade."""
    out = convert(dump(tmp_path, "1|COMPLETED|2026-07-06T01:00:00|1|128",
                       columns="JobID|State|Submit|NNodes|NCPUS"),
                  "Pacific/Auckland", None, False)
    assert len(out) == 1 and out["timelimit_min"].isna().all()


# --- silent corruption -------------------------------------------------------

def test_sub_hour_cpu_time_with_fractional_seconds_is_not_dropped():
    """sacct prints TotalCPU as MM:SS.mmm under an hour. It parsed to None,
    nulling CPU efficiency for exactly the short jobs that dominate the table."""
    assert duration_minutes("11:20.500", style="ms") == pytest.approx(11.34, rel=1e-3)
    assert duration_minutes("13:20.104", style="ms") == pytest.approx(13.335, rel=1e-3)


def test_a_two_part_duration_reads_differently_for_a_limit_and_for_cpu_time():
    """2-00:00 is a walltime limit; 11:20 of CPU time is minutes and seconds."""
    assert duration_minutes("2-00:00", style="hm") == 2880
    assert duration_minutes("11:20", style="hm") == 680
    assert duration_minutes("11:20", style="ms") == pytest.approx(11.333, rel=1e-3)


def test_memory_suffixes_are_binary_as_slurm_writes_them():
    assert mem_mb("4G", 1, 1) == pytest.approx(4096)
    assert mem_mb("1024K", 1, 1) == pytest.approx(1.0)
    assert mem_mb("4294967296", 1, 1) == pytest.approx(4096)     # bare bytes, same request


def test_a_job_cancelled_while_pending_has_null_usage_not_zero(tmp_path):
    """The domain notes tell the agent these are dropped by every average.
    With 0 they are not dropped -- they drag it down and divide by zero."""
    out = convert(dump(tmp_path, row(State="CANCELLED by 12345", Start="Unknown", End="Unknown",
                                     Elapsed="00:00:00", TotalCPU="00:00:00")),
                  "Pacific/Auckland", None, False)
    assert out["start_ts"].isna().all()
    assert out["elapsed_min"].isna().all()
    assert out["total_cpu_min"].isna().all()


def test_a_daylight_saving_transition_does_not_null_the_timestamp(tmp_path):
    """The domain notes flag DST as the risk. An hour of the year became NaT."""
    out = convert(dump(tmp_path, row(Submit="2026-09-27T02:30:00")),
                  "Pacific/Auckland", None, False)
    assert out["submit_ts"].notna().all()


def test_anonymised_pseudonyms_are_stable_across_runs(tmp_path):
    """Both the docstring and --help promise 'stable'. The salt was random."""
    path = dump(tmp_path, row(User="arangi"), row(JobID="2", JobIDRaw="2", User="bpatel"),
                row(JobID="3", JobIDRaw="3", User="cwiremu"))
    first = convert(path, "Pacific/Auckland", None, True)["user"].tolist()
    second = convert(path, "Pacific/Auckland", None, True)["user"].tolist()
    assert first == second
    assert set(first) != {"arangi", "bpatel", "cwiremu"}
