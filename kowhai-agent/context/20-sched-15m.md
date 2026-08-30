## Table: sched_15m
One row per 15-minute sample, per partition: 13,440 rows covering the same four weeks
as jobs. Join to jobs on partition, and on ts against the interval
[start_ts, end_ts) for running work or [eligible_ts, start_ts) for queued work.
Use it to answer "what state was the machine in at that moment".

  ts                       TIMESTAMP, UTC, start of the 15-minute sample
  partition                VARCHAR, joins to jobs.partition
  nodes_total, cpus_total  size of the partition
  nodes_alloc, cpus_alloc  in use at the sample instant
  nodes_down_drain         nodes unavailable: failed, drained, or held by a reservation
  nodes_idle               nodes_total - nodes_alloc - nodes_down_drain. Idle nodes
                           beside a long queue mean jobs that do not fit, not spare
                           capacity
  reservation_nodes        nodes held by a maintenance reservation
  jobs_running             jobs occupying the partition
  jobs_pending             jobs eligible but not yet started. This is the backlog
  cpus_pending_requested   cores those pending jobs are asking for
  oldest_pending_min       age of the oldest eligible pending job, in minutes

The totals do not reconcile exactly with jobs. nodes_alloc includes interactive and
Open OnDemand sessions that the accounting export does not contain, up to about 5% of
nodes. Treat a small discrepancy as expected and a large one as a finding.

Jobs still running when the export was taken are absent from jobs entirely, so the
last days of the window under-count long jobs. Do not read a trend off the final days.
