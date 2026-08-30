## Table: sched_15m
One row per 15-minute sample, per partition, covering the same window as jobs.
Join to jobs on partition, and on ts against the interval
[start_ts, end_ts) for running work or [eligible_ts, start_ts) for queued work.
Use it to answer "what state was the machine in at that moment".

  ts                       TIMESTAMP, UTC, start of the 15-minute sample
  partition                VARCHAR, joins to jobs.partition
  nodes_total, cpus_total  size of the partition
  nodes_alloc, cpus_alloc  in use at the sample instant
  nodes_down_drain         nodes unavailable: failed, drained, or held by a reservation.
                           NULL in a derived table (see provenance below): accounting
                           data cannot see a drained node, so a NULL means "unknown",
                           never "none"
  nodes_idle               nodes_total - nodes_alloc - nodes_down_drain. Idle nodes
                           beside a long queue mean jobs that do not fit, not spare
                           capacity. NULL wherever nodes_down_drain is
  reservation_nodes        nodes held by a maintenance reservation
  jobs_running             jobs occupying the partition
  jobs_pending             jobs eligible but not yet started. This is the backlog
  cpus_pending_requested   cores those pending jobs are asking for
  oldest_pending_min       age of the oldest eligible pending job, in minutes

Provenance matters, and the two kinds of this table behave differently.

SAMPLED (collected from sinfo/squeue at the time): the totals do not reconcile exactly
with jobs. nodes_alloc includes interactive and Open OnDemand sessions that the
accounting export does not contain, up to about 5% of nodes. Treat a small discrepancy
as expected and a large one as a finding.

DERIVED (rebuilt from the jobs table by sacct_to_parquet.py --derive-sched): it is
computed from those same jobs, so it reconciles exactly by construction and cannot
reveal anything jobs does not already contain. nodes_total is the observed peak, so it
is a lower bound on the real size of the partition and utilisation computed from it is
an upper bound. nodes_down_drain, reservation_nodes and nodes_idle are NULL.

Jobs still running when the export was taken have a NULL end_ts and partial usage
columns, so the last days of the window under-count completed work. Do not read a trend
off the final days.
