## Context the tables omit
- All timestamps are UTC. The cluster and its users are in New Zealand, NZST (UTC+12)
  for the whole of this window. Any question about time of day, or about a named day
  such as "Sunday", means local time. Add 12 hours to convert.
- The data covers Monday 6 July to Sunday 2 August 2026 NZST, four complete local
  weeks. In UTC that is 2026-07-05 12:00 to 2026-08-02 12:00.
- New Zealand observes daylight saving from late September to early April. This window
  is entirely NZST, so a fixed +12 is safe here and would not be in October.

## How to talk about queue time
- planned_min is the queued time Slurm itself reports, measured from eligible_ts. It
  is the right default for "how long did it wait".
- date_diff('minute', submit_ts, start_ts) also counts time the job was held or
  waiting on a dependency, which is the workflow's own sequencing rather than queue
  pressure. Use it only when the user asks how long from sbatch to running, and say
  which one you used.
- Queue waits are long-tailed. Report the median with p90, never a bare mean.
- Jobs cancelled while pending have start_ts IS NULL and are dropped by every average.
  Count them separately.
- est_start_ts is the backfill scheduler's prediction. It assumes every running job
  runs to its full time limit, so it is systematically pessimistic. Treat a gap
  between est_start_ts and start_ts as expected, not as an anomaly.
