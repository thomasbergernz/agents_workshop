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

This repository contains interactive notebook-based lessons that guide you through agent development step by step. Each section builds on previous concepts to create a complete, functional agent.

## Next Steps

Once you've completed the workshop, consider:
- Extending your agent with additional tools
- Integrating with different data sources
- Deploying your agent in a production environment

---

Happy building! 🚀
