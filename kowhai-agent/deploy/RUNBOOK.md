# Monthly usage notes, on your own infrastructure

`monthly-notes.sh` exports last month from `sacct`, converts it, drafts one usage
note per project, and leaves them in a private directory. It sends nothing.

It runs from cron on a host that can reach `slurmdbd`. Not from GitHub Actions:
a hosted runner cannot see the cluster, and this repository is public, so
workflow artifacts are downloadable by anyone. `.github/workflows/advisory.yml`
shows the same shape for teaching purposes and is deliberately manual-only.

## Before you install anything

Decide whether sending this data to a third-party model provider is allowed.
The notes are drafted by a model reached through OpenRouter, and the prompt puts
job names, project names and usage figures in front of it. `--anonymise`
pseudonymises **usernames only** — account, project name, job name, job id and
exact timestamps are kept, which is enough to re-identify. If that needs sign-off
at your institution, get it first; nothing below is useful without it.

## What you need

- A host with `sacct` against the cluster you want to report on
- `uv` on that host's `PATH`
- An OpenRouter API key
- Somewhere private for the drafts, on local disk

## Install

```bash
sudo install -d -m 750 -o kowhai -g kowhai /opt/kowhai /var/lib/kowhai/drafts /var/log/kowhai
sudo install -d -m 750 -o root  -g kowhai /etc/kowhai

sudo -u kowhai git clone https://github.com/thomasbergernz/agents_workshop /opt/kowhai/agents_workshop
sudo -u kowhai sh -c 'cd /opt/kowhai/agents_workshop/kowhai-agent && uv sync --locked'

printf 'sk-or-...\n' | sudo tee /etc/kowhai/openrouter.key >/dev/null
sudo chown root:kowhai /etc/kowhai/openrouter.key && sudo chmod 640 /etc/kowhai/openrouter.key
```

The script refuses to run on a key file that is not `600` or `400`. If you use
`640` with a group, run the script as a user in that group and relax the check,
or keep the file `600` and owned by the cron user.

## Configure

`/etc/kowhai/monthly-notes.env` — every line is exported to the job, so anything
`kowhai` reads from the environment can go here.

```sh
KOWHAI_REPO=/opt/kowhai/agents_workshop
KOWHAI_DRAFTS_ROOT=/var/lib/kowhai/drafts
KOWHAI_LOG_DIR=/var/log/kowhai
KOWHAI_KEY_FILE=/etc/kowhai/openrouter.key
KOWHAI_TZ=Pacific/Auckland

# Keep this secret and keep it the same. It is what makes pseudonyms stable
# between months; change it and u0001 becomes a different person.
KOWHAI_ANON_SALT=<a long random string>

# Optional
KOWHAI_MODEL=google/gemini-3.5-flash-lite
KOWHAI_NOTIFY=mail -s "kowhai usage notes" rse-team@example.org
KOWHAI_KEEP_MONTHS=13
```

## Schedule

```cron
# 07:00 on the 2nd, so a late-arriving accounting record for the 1st is in.
0 7 2 * *  /opt/kowhai/agents_workshop/kowhai-agent/deploy/monthly-notes.sh
```

Host `cron` uses the machine's local time, unlike the GitHub Actions schedule in
`advisory.yml`, which is always UTC. Do not copy the cron expression across from
one to the other: `0 19 1 * *` is 07:00 NZST on the 2nd in Actions, and 7 p.m.
on the 1st here.

`cron` runs with almost no environment. The script sets its own `PATH` and reads
its config file, so the crontab needs nothing else.

## First run, without waiting for the 1st

```bash
sudo -u kowhai /opt/kowhai/agents_workshop/kowhai-agent/deploy/monthly-notes.sh; echo "exit=$?"
```

Exit `0` means every account produced a note. Check:

- `ls /var/lib/kowhai/drafts/` — a directory named for the month it covers, one
  `.md` per project inside. (Do not reach for `date -d 'last month'` to guess the
  name: on the 31st of a month following a shorter one it silently returns the
  wrong month. The script anchors to the 1st for the same reason.)
- Each file's second comment line names the account and the row count it came from
- Directory is `drwx------`, files are `-rw-------`
- `/var/log/kowhai/monthly-notes.log` ends with the summary line

Read one note against `/var/log/kowhai/runs.jsonl` before you trust the other
seventeen. The arithmetic can be right while the sentence overstates it.

## Every month

The job drafts. A person reads and sends. That is the control this design rests
on, and none of the guardrails replace it.

Skim the log for the summary line. `N failed` means those accounts have a file
saying why instead of a note — the rest are still good.

## When it fails

| Message | What it means |
|---|---|
| `another run holds ... exiting` | A previous month is still running. Normal if `slurmdbd` was slow; investigate if it repeats. |
| `sacct is not on PATH` | Wrong host, or cron's `PATH` does not include it. |
| `sacct returned no rows` | No jobs in the window. Deliberate: better to fail than to draft from nothing. |
| `... is mode 644; want 600` | The key file is readable by others. |
| `advisory exited 1` | At least one account failed. Others succeeded and are worth reading. |
| Notes name a group's own jobs but the numbers look wrong | Check the converter's reconciliation output in the log — null counts and CPU-time warnings appear there. |

## Turning it off

Comment the crontab line. Nothing else is stateful. Drafts already written stay
where they are; `KOWHAI_KEEP_MONTHS` prunes them on the next run, so raise it
first if you want them kept.

## What leaves the host

Per account, the model receives: the SQL the agent writes, the rows it returns
(job names, project names, pseudonymised usernames, usage figures) and the prompt.
Drafts and `runs.jsonl` stay on local disk. `runs.jsonl` records every query with
its literal values, so treat it as identifying and keep it off any public share.
