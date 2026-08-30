## Table: jobs
One row per Slurm job allocation, already rolled up from job steps. Array tasks are
separate rows. Use it to answer "who ran what, how long did it wait, and how well did
it use what it was given". Get the row count and the window from the data rather than
assuming them.

Identity:
  job_id           BIGINT
  array_job_id     BIGINT, shared by every task of one array; -1 if not an array
  array_task_id    BIGINT, index within the array; -1 if not an array
  job_name         VARCHAR, the sbatch --job-name
  user             VARCHAR, the submitting username
  account          VARCHAR, the project code the job is charged to
  project_name     VARCHAR, human readable project title
  institution      VARCHAR
  partition        VARCHAR, on this cluster debug, large, long, hugemem, gpu.
                   Check with list_values rather than assuming
  qos              VARCHAR, quality of service: a policy bucket that adjusts priority
                   and limits ('debug' jumps the queue, 'normal' does not)
  state            VARCHAR, commonly COMPLETED, FAILED, TIMEOUT, CANCELLED,
                   OUT_OF_MEMORY, NODE_FAIL. Not a closed set: an export taken
                   while the cluster is busy also contains RUNNING and PENDING,
                   whose usage columns are partial or NULL. Filtering to the six
                   above silently drops them, so say so if you do
  exit_code        VARCHAR
  last_reason      VARCHAR, the last pending reason Slurm recorded, NULL if it never
                   waited. Priority: outranked by other jobs. Resources: next in
                   line, waiting for cores to free. Dependency: waiting on another
                   job. ReqNodeNotAvail: nodes held, usually for maintenance

Timestamps, all UTC:
  submit_ts        when sbatch ran
  eligible_ts      when the job became runnable. Later than submit_ts if it was held
                   or waiting on a dependency
  est_start_ts     the backfill scheduler's predicted start, recorded while pending.
                   NULL when Slurm produced no estimate. Slurm does not retain
                   these, so in data converted from a real sacct export this
                   column is NULL for every row -- say it is unavailable rather
                   than reporting that no job had an estimate
  start_ts         when it actually started. NULL if it never started
  end_ts           when it stopped for any reason

Requested, and therefore charged:
  timelimit_min    walltime requested
  req_nodes, req_cpus, req_gpus
  req_mem_mb       memory requested, total across the allocation

Used:
  elapsed_min      wall time actually used. NULL if the job never started
  total_cpu_min    CPU time consumed, summed over every allocated core
  max_rss_mb       peak resident memory across the job's steps
  gpu_util_pct     mean GPU utilisation from job profiling, NULL outside the gpu
                   partition. Comes from profiling (DCGM, jobstats), not from
                   accounting, so in data converted from a real sacct export it
                   is NULL everywhere
  planned_min      minutes spent queued, measured from eligible_ts to start_ts

Derived quantities, computed rather than stored:
  core-hours charged = req_cpus * elapsed_min / 60
  CPU efficiency     = total_cpu_min / (req_cpus * elapsed_min)
  memory efficiency  = max_rss_mb / req_mem_mb -- but only comparable within one
                       job shape: req_mem_mb is the total across the allocation
                       while max_rss_mb is the peak of a single task, so on a
                       multi-node job this ratio understates memory use by up to
                       the node count. Quote it for single-node jobs; for larger
                       ones say the two numbers separately instead
  walltime efficiency= elapsed_min / timelimit_min
Charging follows the request, not the use. Never average an efficiency ratio across
jobs of different sizes; sum the numerator and the denominator instead.
