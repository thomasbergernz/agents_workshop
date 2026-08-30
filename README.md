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

The workshop is designed to run in Jupyter environments, including:
- Local Jupyter installations
- [Google Colab](https://colab.research.google.com)
- JupyterLab servers

### Installation

#### For Google Colab

When running on Colab, most common dependencies are pre-installed. However, you may need to install additional packages for this workshop:

```python
# Run these cells at the beginning of your notebook
!pip install --upgrade pip
!pip install openai  # or other LLM providers you're using
!pip install python-dotenv  # for managing API keys
```

#### For Local Jupyter / JupyterLab

Create a virtual environment and install dependencies:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install jupyter
pip install -r requirements.txt
```

Then start Jupyter:

```bash
jupyter notebook
# or for JupyterLab:
jupyter lab
```

### Running the Notebook

1. Open the workshop notebook in your preferred Jupyter environment
2. Follow the guided sections to build each component of your agent
3. Experiment with the examples and modify them to suit your needs

## Common Issues

### In Colab
- **Module not found errors**: Use `!pip install <package>` to install missing dependencies
- **API key management**: Store sensitive credentials in Colab Secrets or use environment variables via `python-dotenv`
- **File uploads**: Use `from google.colab import files; files.upload()` to import local files

### In Local Jupyter / JupyterLab
- Ensure your virtual environment is activated before running Jupyter
- Install any required packages in the activated environment (not globally)
- Check that Python kernel matches your environment

## Structure

```
kowhai_slurm_agents_workshop.ipynb   the workshop itself, run top to bottom
kowhai-agent/                        the same agent, as an installable package
sacct_to_parquet.py                  turn a real sacct export into the Parquet files the notebook reads
```

The notebook teaches, and most of it should not be shipped: of its 56 code cells, 42
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
uv run pytest                           # 30 tests, no network, no API key
```

Three things differ from the notebook, and they are the point of the split: the system
prompt lives in markdown files under `context/` that a colleague who knows the cluster
but not Python can edit; tool specifications are generated from each function's
signature rather than hand-written beside it; and every run is costed and logged.

See [kowhai-agent/README.md](kowhai-agent/README.md) for how it is put together and
what it is deliberately not for.

## Next Steps

Once you've completed the workshop, consider:
- Extending your agent with additional tools
- Integrating with different data sources
- Deploying your agent in a production environment

---

Happy building! 🚀
