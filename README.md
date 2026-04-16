# agenteval

Turn git history into eval suites for coding agents.

> ⚠️ WIP — core functionality is stubbed out.

## Installation

```bash
pip install -e .
```

## Usage

```bash
agenteval init --repo . --since 2025-01-01
agenteval version
```

## How it works

1. Fetches merged PRs from a GitHub repo
2. Extracts task definitions (prompt, base commit, expected diff)
3. Cleans up prompts via LLM
4. Outputs a structured eval suite
