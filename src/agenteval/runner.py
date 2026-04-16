"""Agent runner — executes coding agents against tasks."""

import json
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from agenteval.models import RunResult, TaskDefinition

# Default agent command templates.
# {prompt} is replaced with the prompt file path, {workdir} with the working directory.
AGENT_CONFIGS: dict[str, str] = {
    "claude-code": "claude -p {prompt} --output-dir {workdir}",
    "aider": "aider --message {prompt}",
}


class AgentRunner:
    """Runs a coding agent on a task and captures the result."""

    def __init__(
        self,
        agent: str = "claude-code",
        agent_cmd: str | None = None,
        timeout: int = 300,
    ):
        if agent_cmd:
            self.cmd_template = agent_cmd
        elif agent in AGENT_CONFIGS:
            self.cmd_template = AGENT_CONFIGS[agent]
        else:
            raise ValueError(
                f"Unknown agent '{agent}'. Known agents: {list(AGENT_CONFIGS)}. "
                "Or pass --agent-cmd with a custom command template."
            )
        self.agent = agent
        self.timeout = timeout

    def run_task(self, task: TaskDefinition, workdir: Path) -> RunResult:
        """Clone repo at base_commit, run agent, capture diff."""
        started_at = datetime.now()
        start_time = time.monotonic()

        try:
            # Clone repo into workdir
            workdir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", task.repo_url, str(workdir / "repo")],
                capture_output=True, text=True, check=True, timeout=120,
            )
            repo_dir = workdir / "repo"
            subprocess.run(
                ["git", "checkout", task.base_commit],
                capture_output=True, text=True, check=True,
                cwd=str(repo_dir), timeout=30,
            )

            # Write prompt file
            prompt_file = workdir / "prompt.txt"
            prompt_file.write_text(task.prompt)

            # Build command
            cmd = self.cmd_template.format(
                prompt=str(prompt_file),
                workdir=str(repo_dir),
            )

            # Run agent
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                cwd=str(repo_dir), timeout=self.timeout,
            )

            # Capture diff
            diff_proc = subprocess.run(
                ["git", "diff"],
                capture_output=True, text=True,
                cwd=str(repo_dir), timeout=30,
            )
            # Also include untracked files
            untracked_proc = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True, text=True,
                cwd=str(repo_dir), timeout=30,
            )
            actual_diff = diff_proc.stdout + untracked_proc.stdout

            elapsed = time.monotonic() - start_time
            completed_at = datetime.now()

            return RunResult(
                task_id=task.id,
                agent=self.agent,
                actual_diff=actual_diff,
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                duration_seconds=elapsed,
                started_at=started_at,
                completed_at=completed_at,
                success=proc.returncode == 0,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start_time
            return RunResult(
                task_id=task.id,
                agent=self.agent,
                stderr="Agent timed out",
                exit_code=-1,
                duration_seconds=elapsed,
                started_at=started_at,
                completed_at=datetime.now(),
                success=False,
            )
        except Exception as e:
            elapsed = time.monotonic() - start_time
            return RunResult(
                task_id=task.id,
                agent=self.agent,
                stderr=str(e),
                exit_code=-1,
                duration_seconds=elapsed,
                started_at=started_at,
                completed_at=datetime.now(),
                success=False,
            )


def save_run_result(result: RunResult, output_dir: Path) -> Path:
    """Save a RunResult to JSON, return the file path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = result.started_at.strftime("%Y%m%d%H%M%S")
    filename = f"{result.task_id}-{result.agent}-{timestamp}.json"
    path = output_dir / filename
    path.write_text(result.model_dump_json(indent=2))
    return path
