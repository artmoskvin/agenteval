# agenteval

Turn git history into eval suites for coding agents.

## Quick Start

```bash
# Install (using uv)
uv tool install .

# Or run without installing
uvx --from . agenteval <command>

# Or install with pip
pip install -e .

# 1. Extract tasks from your repo's PR history
agenteval init --repo owner/repo --since 2024-01-01

# 2. Run an agent against the tasks
agenteval run --suite .agenteval/tasks/suite.json --agent claude-code

# 3. Score the results
agenteval score --runs .agenteval/runs/
```

## Commands

### `agenteval init`

Extract eval tasks from merged PRs.

```bash
agenteval init --repo owner/repo --since 2024-01-01
```

| Option | Default | Description |
|--------|---------|-------------|
| `--repo` | `.` | Path to local git repo or `owner/repo` for GitHub |
| `--since` | *(required)* | Start date (`YYYY-MM-DD`) |
| `--until` | now | End date (`YYYY-MM-DD`) |
| `--output` | `.agenteval/tasks` | Output directory for task definitions |
| `--github-token` | `$GITHUB_TOKEN` | GitHub API token |
| `--no-llm` | `false` | Skip LLM-based prompt cleanup |

PRs are filtered for eval quality: excludes bots, dependency bumps, chore PRs, large diffs (≥500 lines or ≥10 files), and PRs without descriptions.

### `agenteval run`

Run a coding agent against one or more tasks.

```bash
# Run entire suite
agenteval run --suite .agenteval/tasks/suite.json --agent claude-code

# Run a single task
agenteval run --task .agenteval/tasks/my-task.json --agent aider

# Custom agent command
agenteval run --suite .agenteval/tasks/suite.json \
  --agent-cmd "my-agent solve {prompt} --dir {workdir}"
```

| Option | Default | Description |
|--------|---------|-------------|
| `--task` | — | Path to a single task JSON |
| `--suite` | — | Path to suite JSON (runs all tasks) |
| `--agent` | `claude-code` | Agent name (`claude-code`, `aider`, or use `--agent-cmd`) |
| `--agent-cmd` | — | Custom command template (`{prompt}` and `{workdir}` are replaced) |
| `--output` | `.agenteval/runs` | Output directory for run results |
| `--timeout` | `300` | Max seconds per task |

### `agenteval score`

Score agent run results across multiple dimensions.

```bash
# Score all runs in a directory
agenteval score --runs .agenteval/runs/

# Score a single run
agenteval score --run .agenteval/runs/result.json

# Skip LLM judge (faster, cheaper)
agenteval score --runs .agenteval/runs/ --no-llm --output report.json
```

| Option | Default | Description |
|--------|---------|-------------|
| `--run` | — | Path to a single run result JSON |
| `--runs` | — | Directory of run result files |
| `--task-dir` | `.agenteval/tasks` | Directory containing task definitions |
| `--output` | — | Save score report as JSON |
| `--no-llm` | `false` | Skip LLM judge dimension |

### `agenteval list`

List tasks in an existing suite.

```bash
agenteval list --output .agenteval/tasks
```

## How It Works

```
GitHub PRs  →  Task Extraction  →  Agent Run  →  Scoring
```

1. **Init**: Fetches merged PRs from GitHub, filters for eval quality, extracts each PR into a task (prompt from title/body, expected solution from the diff). Optionally cleans prompts with an LLM to remove internal references and boilerplate.
2. **Run**: Clones the repo at the PR's base commit, hands the prompt to the agent, captures the resulting diff.
3. **Score**: Compares the agent's output against the expected solution across four dimensions.

## Supported Agents

| Agent | Command |
|-------|---------|
| `claude-code` | `claude -p {prompt} --output-dir {workdir}` |
| `aider` | `aider --message {prompt}` |
| Custom | Any command via `--agent-cmd` — use `{prompt}` and `{workdir}` placeholders |

## Scoring Dimensions

Each run is scored across four dimensions, combined into a weighted overall score (0–1):

| Dimension | Weight | Description |
|-----------|--------|-------------|
| **Tests** | 35% | Do the repo's tests pass after applying the agent's diff? Auto-detects pytest, npm test, or make test. |
| **LLM Judge** | 30% | An LLM rates the solution 1–5 by comparing the agent's diff against the expected diff. |
| **Diff Similarity** | 25% | Jaccard similarity of changed lines between expected and actual diffs. |
| **Lint** | 10% | Do linters pass on changed files? Auto-detects ruff (Python) and eslint (JS/TS). |

Weights are normalized — if a dimension is unavailable (e.g., no tests detected), the remaining dimensions are re-weighted proportionally.

## Configuration

| Variable | Required For | Description |
|----------|-------------|-------------|
| `GITHUB_TOKEN` | `init` | GitHub API access (higher rate limits, private repos) |
| `ANTHROPIC_API_KEY` | `init`, `score` | LLM prompt cleanup and LLM judge (skip with `--no-llm`) |

## Development

```bash
# Clone and install (using uv)
git clone https://github.com/artmoskvin/agenteval.git
cd agenteval
uv sync --all-extras

# Run tests
uv run pytest tests/ -v
```

<details>
<summary>Using pip instead</summary>

```bash
git clone https://github.com/artmoskvin/agenteval.git
cd agenteval
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

</details>

## License

MIT
