# agents_workshop

Build a working agent from four pieces: a tool, a loop, a description of the data, and notes that explain what the data means.

## Overview

This workshop provides a hands-on introduction to building autonomous agents. You'll learn how to construct an agent by combining essential components:

- **Tool**: Define what actions your agent can take
- **Loop**: Implement the decision-making cycle that drives agent behavior
- **Data Description**: Specify the structure and format of data your agent works with
- **Documentation**: Add explanatory notes that help the agent understand what the data represents

## Getting Started

### Prerequisites

- Python 3.10 or newer for the notebook, 3.11+ for the package, in Jupyter,
  JupyterLab, or [Google Colab](https://colab.research.google.com)
- An [OpenRouter](https://openrouter.ai) API key. A complete run makes a few dozen
  model calls and costs a few cents.

There is nothing to download and no `requirements.txt` to install. The notebook's first
code cell pins everything it needs, and the dataset is generated locally in about five
seconds into two Parquet files. The one exception is Part 13, which is optional and
[has its own setup](#part-13-the-one-thing-you-start-yourself-optional).

### Installation

Open `kowhai_slurm_agents_workshop.ipynb` and run the setup cell in Part 0:

```python
!pip install -q duckdb==1.5.5 matplotlib==3.11.1 numpy==2.5.2 openai==2.53.0 \
    pandas==3.0.5 pyarrow==25.0.1 tabulate==0.10.0
```

Pinned so the workshop behaves the same everywhere, and safe to re-run. It is the same
cell on Colab as on a local kernel.

Locally, create the environment first so those packages land in it rather than in your
system Python. This uses [uv](https://docs.astral.sh/uv/getting-started/installation/),
the same tool the package uses:

```bash
uv venv --seed                  # --seed installs pip, which the notebook's own cell needs
source .venv/bin/activate       # Windows: .venv\Scripts\activate
uv pip install jupyterlab notebook
jupyter lab                     # or: jupyter notebook
```

`--seed` is not optional here. A uv environment has no `pip` at all by default, and the
notebook installs its own dependencies with `!pip install` — without it that cell either
fails outright or silently installs into your system Python instead.

### Your API key

The cell after the install looks in three places, in order:

1. the `OPENROUTER_API_KEY` environment variable
2. a `.env` file beside the notebook containing `OPENROUTER_API_KEY=sk-or-...`
3. a prompt, which hides what you type

On Colab, set it from Colab Secrets or paste it at the prompt. The notebook never
prints the key and never writes it to disk.

OpenRouter is a broker: one key, many models, one OpenAI-compatible interface. Any
model that supports tool calling works — change `MODEL` near the top to try another.

### Running the Notebook

Run the cells in order, top to bottom. Each part builds on the one before it, and
several cells are deliberately wrong and corrected in the part that follows, so read
the prose around a surprising result before assuming your setup is broken.

Part 12 closes with a rubric for deciding whether a task should be an agent at all.
[The package](#the-package) below is what the one task that passed it looks like once
it leaves the notebook.

### Part 13: the one thing you start yourself (optional)

Parts 1 to 12 need nothing but a key. Part 13 puts an [Envoy AI
Gateway](https://github.com/envoyproxy/ai-gateway) between the notebook and the
provider, and that is a process you run — the only piece of this workshop that does not
work in Colab with nothing installed.

Download the `aigw` binary for your platform from the [releases
page](https://github.com/envoyproxy/ai-gateway/releases) — `aigw-darwin-arm64`,
`aigw-linux-amd64` or `aigw-linux-arm64` — then:

```bash
aigw download-envoy                          # once; it fetches an Envoy binary
OPENAI_API_KEY="$OPENROUTER_API_KEY" aigw run aigw.yaml
```

Part 13 writes `aigw.yaml` itself, so run that cell before the command above. No second
key is involved: OpenRouter speaks the OpenAI API, and the gateway reads the key you
already have.

`aigw` will also configure itself from `OPENAI_API_KEY` alone, with no file. That
shortcut does not work for OpenRouter, which serves the OpenAI API under `/api/v1`
rather than `/v1`; the request lands on the website instead of the API and returns a
404 page. Part 13's configuration sets `prefix: api/v1` and explains why.

`download-envoy` is worth running the night before rather than in a room where thirty
people share the wifi.

Docker, if you would rather install nothing:

```bash
docker run --rm -p 1975:1975 -p 1064:1064 \
    -e OPENAI_API_KEY="$OPENROUTER_API_KEY" \
    -v "$PWD/aigw.yaml:/aigw.yaml" \
    envoyproxy/ai-gateway-cli:v1.1.0 run /aigw.yaml
```

Pin the version you tested against; this was written against v1.1.0. Part 13's cells
check whether anything is listening on port 1975 and, when nothing is, print what they
would have done and carry on, so the notebook still reads end to end without any of it.

## Common Issues

### In Colab
- **Module not found**: re-run the Part 0 install cell. A reconnected runtime loses installed packages.
- **API key**: use Colab Secrets, or paste it at the prompt when the setup cell asks.
- **Nothing to upload**: the dataset is generated by the notebook itself. The only thing worth uploading is the output of `sacct_to_parquet.py`, and only if you are swapping in real cluster data.

### In Local Jupyter / JupyterLab
- Activate the virtual environment before starting Jupyter, or the kernel will not see the packages.
- Check that the running kernel is the one from that environment.
- To use real data instead of the synthetic set, put `data/jobs.parquet` and `data/sched_15m.parquet` beside the notebook before running the dataset cell. `build_dataset()` only generates when the files are missing, so it will find yours and skip the generator.

## Structure

```
kowhai_slurm_agents_workshop.ipynb   the workshop itself, run top to bottom
kowhai-agent/                        the same agent, as an installable package
sacct_to_parquet.py                  turn a real sacct export into the Parquet files the notebook reads
```

The notebook teaches, and most of it should not be shipped: of its 62 code cells, 47
are demonstrations and several are deliberately wrong. Extracting it mechanically
would carry the wrong answers into production as if they were features.

## The package

`kowhai-agent/` is the other half — the same loop, the same three tools and the same
guardrails, built for the one task in the workshop that passed its own rubric:
drafting a usage note per research project, which a person then reads and sends.

```bash
cd kowhai-agent
uv sync
export OPENROUTER_API_KEY=...

uv run scripts/make_workshop_data.py    # synthetic data, to try it out
uv run kowhai selfcheck                 # every tool, no model calls, no cost
uv run kowhai ask "Which project wasted the most core-hours?" --trace
uv run kowhai advisory --out drafts/    # one usage note per project
uv run pytest                           # 82 tests, no network, no API key
```

Three things differ from the notebook, and they are the point of the split: the system
prompt lives in markdown files under `context/` that a colleague who knows the cluster
but not Python can edit; tool specifications are generated from each function's
signature rather than hand-written beside it; and every run is costed and logged.

See [kowhai-agent/README.md](kowhai-agent/README.md) for how it is put together and
what it is deliberately not for.

## Next Steps

- Point it at your own cluster: export from `sacct`, convert with
  `sacct_to_parquet.py`, and put the two Parquet files beside the notebook.
- Rewrite `kowhai-agent/context/` for your site. The schema cards and domain notes are
  the asset there; the Python around them is replaceable.
- Score a task of your own against the Part 12 rubric before automating it. Most
  reporting work should be a saved query or a threshold alert, not an agent.

---

Happy building! 🚀
