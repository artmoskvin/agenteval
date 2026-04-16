"""Tests for runner, scorer, and their CLI commands."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agenteval.cli import app
from agenteval.models import RunResult, ScoreResult, TaskDefinition, TaskSuite
from agenteval.runner import AgentRunner, save_run_result
from agenteval.scorer import Scorer, diff_similarity

cli_runner = CliRunner()


# --- Model tests ---

def test_run_result_model():
    r = RunResult(task_id="t1", agent="claude-code")
    assert r.task_id == "t1"
    assert r.exit_code == -1
    assert r.success is False
    assert r.actual_diff == ""


def test_run_result_serialization():
    r = RunResult(
        task_id="t1", agent="claude-code", actual_diff="diff",
        stdout="ok", stderr="", exit_code=0, duration_seconds=1.5,
        success=True,
    )
    data = json.loads(r.model_dump_json())
    assert data["task_id"] == "t1"
    assert data["success"] is True


def test_score_result_model():
    s = ScoreResult(task_id="t1", agent="claude-code")
    assert s.tests_pass is None
    assert s.diff_similarity == 0.0
    assert s.llm_judge_score is None
    assert s.overall_score == 0.0


def test_score_result_with_values():
    s = ScoreResult(
        task_id="t1", agent="claude-code",
        tests_pass=True, lint_clean=True,
        diff_similarity=0.8, llm_judge_score=4,
        overall_score=0.85,
    )
    assert s.tests_pass is True
    assert s.llm_judge_score == 4


# --- Runner tests ---

def test_agent_runner_unknown_agent():
    import pytest
    with pytest.raises(ValueError, match="Unknown agent"):
        AgentRunner(agent="nonexistent")


def test_agent_runner_custom_cmd():
    runner = AgentRunner(agent="custom", agent_cmd="echo {prompt} {workdir}")
    assert runner.cmd_template == "echo {prompt} {workdir}"


def test_save_run_result(tmp_path):
    r = RunResult(
        task_id="fix-bug-1", agent="claude-code",
        started_at=datetime(2026, 4, 16, 12, 0, 0),
    )
    path = save_run_result(r, tmp_path)
    assert path.exists()
    assert "fix-bug-1-claude-code-" in path.name
    loaded = json.loads(path.read_text())
    assert loaded["task_id"] == "fix-bug-1"


@patch("agenteval.runner.subprocess.run")
def test_run_task_success(mock_run, tmp_path):
    """Test run_task with mocked subprocess."""
    task = TaskDefinition(
        id="t1", prompt="Fix the bug", repo_url="https://github.com/x/y",
        base_commit="abc123", expected_diff_path="t1.patch",
    )

    # Mock subprocess calls: clone, checkout, agent, diff, cached diff
    clone_result = MagicMock(returncode=0, stdout="", stderr="")
    checkout_result = MagicMock(returncode=0, stdout="", stderr="")
    agent_result = MagicMock(returncode=0, stdout="agent output", stderr="")
    diff_result = MagicMock(returncode=0, stdout="diff --git a/f b/f\n+new line", stderr="")
    cached_result = MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = [clone_result, checkout_result, agent_result, diff_result, cached_result]

    runner = AgentRunner(agent="claude-code", timeout=10)
    result = runner.run_task(task, tmp_path)

    assert result.task_id == "t1"
    assert result.success is True
    assert result.exit_code == 0
    assert "agent output" in result.stdout
    assert "diff --git" in result.actual_diff


@patch("agenteval.runner.subprocess.run")
def test_run_task_agent_failure(mock_run, tmp_path):
    task = TaskDefinition(
        id="t1", prompt="Fix bug", repo_url="https://github.com/x/y",
        base_commit="abc", expected_diff_path="t1.patch",
    )

    clone_result = MagicMock(returncode=0, stdout="", stderr="")
    checkout_result = MagicMock(returncode=0, stdout="", stderr="")
    agent_result = MagicMock(returncode=1, stdout="", stderr="error occurred")
    diff_result = MagicMock(returncode=0, stdout="", stderr="")
    cached_result = MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = [clone_result, checkout_result, agent_result, diff_result, cached_result]

    runner = AgentRunner(agent="claude-code")
    result = runner.run_task(task, tmp_path)

    assert result.success is False
    assert result.exit_code == 1


@patch("agenteval.runner.subprocess.run")
def test_run_task_timeout(mock_run, tmp_path):
    import subprocess
    task = TaskDefinition(
        id="t1", prompt="Fix", repo_url="https://github.com/x/y",
        base_commit="abc", expected_diff_path="t1.patch",
    )

    clone_result = MagicMock(returncode=0)
    checkout_result = MagicMock(returncode=0)
    mock_run.side_effect = [clone_result, checkout_result, subprocess.TimeoutExpired("cmd", 10)]

    runner = AgentRunner(agent="claude-code", timeout=10)
    result = runner.run_task(task, tmp_path)

    assert result.success is False
    assert "timed out" in result.stderr.lower()


# --- Scorer tests ---

def test_diff_similarity_identical():
    diff = "+added line\n-removed line"
    assert diff_similarity(diff, diff) == 1.0


def test_diff_similarity_empty():
    assert diff_similarity("", "") == 1.0


def test_diff_similarity_no_overlap():
    assert diff_similarity("+line a", "+line b") == 0.0


def test_diff_similarity_partial():
    expected = "+line a\n+line b\n-line c"
    actual = "+line a\n+line d\n-line c"
    # shared: {+line a, -line c}, union: {+line a, +line b, -line c, +line d} = 4
    assert diff_similarity(expected, actual) == 2.0 / 4.0


def test_diff_similarity_ignores_header_lines():
    expected = "--- a/file.py\n+++ b/file.py\n+real change"
    actual = "--- a/other.py\n+++ b/other.py\n+real change"
    assert diff_similarity(expected, actual) == 1.0


def test_scorer_no_llm():
    scorer = Scorer(no_llm=True)
    run = RunResult(task_id="t1", agent="claude-code", actual_diff="+new line")
    task = TaskDefinition(
        id="t1", prompt="Fix", repo_url="/tmp/repo",
        base_commit="abc", expected_diff_path="/nonexistent",
    )
    # With no_llm, should skip LLM judge
    with patch.object(scorer, "_run_tests", return_value=(None, "")), \
         patch.object(scorer, "_run_lint", return_value=(None, "")):
        result = scorer.score_run(run, task, Path("/tmp/repo"))

    assert result.llm_judge_score is None
    assert result.task_id == "t1"


def test_scorer_with_mocked_llm():
    with patch("agenteval.scorer.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="SCORE: 4\nEXPLANATION: Good solution")]
        mock_client.messages.create.return_value = mock_response

        scorer = Scorer(no_llm=False)
        run = RunResult(task_id="t1", agent="claude-code", actual_diff="+fix")
        task = TaskDefinition(
            id="t1", prompt="Fix", repo_url="/tmp",
            base_commit="abc", expected_diff_path="/nonexistent",
        )

        with patch.object(scorer, "_run_tests", return_value=(True, "ok")), \
             patch.object(scorer, "_run_lint", return_value=(True, "")):
            result = scorer.score_run(run, task, Path("/tmp"))

    assert result.llm_judge_score == 4
    assert "Good solution" in result.llm_judge_explanation


def test_compute_overall_all_dimensions():
    overall = Scorer._compute_overall(
        tests_pass=True, lint_clean=True,
        diff_similarity=0.8, llm_judge_score=4,
    )
    # (0.35*1 + 0.1*1 + 0.25*0.8 + 0.3*0.8) / 1.0
    expected = 0.35 + 0.1 + 0.25 * 0.8 + 0.3 * (4 / 5.0)
    assert abs(overall - expected) < 0.001


def test_compute_overall_no_tests_no_llm():
    overall = Scorer._compute_overall(
        tests_pass=None, lint_clean=None,
        diff_similarity=0.5, llm_judge_score=None,
    )
    assert overall == 0.5  # only diff_similarity contributes


# --- CLI run command tests ---

def test_cli_run_no_args():
    result = cli_runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "Provide --task or --suite" in result.output


def test_cli_run_missing_task():
    result = cli_runner.invoke(app, ["run", "--task", "/nonexistent.json"])
    assert result.exit_code == 1
    assert "not found" in result.output


@patch("agenteval.runner.subprocess.run")
def test_cli_run_single_task(mock_run, tmp_path):
    task = TaskDefinition(
        id="t1", prompt="Fix bug", repo_url="https://github.com/x/y",
        base_commit="abc", expected_diff_path="t1.patch",
    )
    task_file = tmp_path / "task.json"
    task_file.write_text(task.model_dump_json())

    clone_result = MagicMock(returncode=0, stdout="", stderr="")
    checkout_result = MagicMock(returncode=0, stdout="", stderr="")
    agent_result = MagicMock(returncode=0, stdout="done", stderr="")
    diff_result = MagicMock(returncode=0, stdout="diff output", stderr="")
    cached_result = MagicMock(returncode=0, stdout="", stderr="")
    mock_run.side_effect = [clone_result, checkout_result, agent_result, diff_result, cached_result]

    output_dir = tmp_path / "runs"
    result = cli_runner.invoke(app, [
        "run", "--task", str(task_file), "--agent", "claude-code",
        "--output", str(output_dir),
    ])

    assert result.exit_code == 0
    assert "1/1" in result.output
    assert list(output_dir.glob("*.json"))


@patch("agenteval.runner.subprocess.run")
def test_cli_run_suite(mock_run, tmp_path):
    suite = TaskSuite(
        name="test", repo_url="https://github.com/x/y",
        tasks=[
            TaskDefinition(
                id="t1", prompt="Fix", repo_url="https://github.com/x/y",
                base_commit="abc", expected_diff_path="t1.patch",
            ),
        ],
    )
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(suite.model_dump_json())

    clone_result = MagicMock(returncode=0, stdout="", stderr="")
    checkout_result = MagicMock(returncode=0, stdout="", stderr="")
    agent_result = MagicMock(returncode=0, stdout="ok", stderr="")
    diff_result = MagicMock(returncode=0, stdout="", stderr="")
    cached_result = MagicMock(returncode=0, stdout="", stderr="")
    mock_run.side_effect = [clone_result, checkout_result, agent_result, diff_result, cached_result]

    output_dir = tmp_path / "runs"
    result = cli_runner.invoke(app, [
        "run", "--suite", str(suite_file), "--output", str(output_dir),
    ])

    assert result.exit_code == 0


# --- CLI score command tests ---

def test_cli_score_no_args():
    result = cli_runner.invoke(app, ["score"])
    assert result.exit_code == 1
    assert "Provide --run or --runs" in result.output


def test_cli_score_missing_run():
    result = cli_runner.invoke(app, ["score", "--run", "/nonexistent.json"])
    assert result.exit_code == 1


def test_cli_score_single_run(tmp_path):
    # Create run result
    run = RunResult(
        task_id="t1", agent="claude-code", actual_diff="+line",
        exit_code=0, success=True,
    )
    run_file = tmp_path / "run.json"
    run_file.write_text(run.model_dump_json())

    # Create task suite
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    suite = TaskSuite(
        name="test", repo_url="https://github.com/x/y",
        tasks=[TaskDefinition(
            id="t1", prompt="Fix bug", repo_url="https://github.com/x/y",
            base_commit="abc", expected_diff_path=str(tmp_path / "nonexistent.patch"),
        )],
    )
    (task_dir / "suite.json").write_text(suite.model_dump_json())

    with patch("agenteval.scorer.Scorer.score_run") as mock_score:
        mock_score.return_value = ScoreResult(
            task_id="t1", agent="claude-code",
            tests_pass=True, lint_clean=True,
            diff_similarity=0.75, llm_judge_score=4,
            overall_score=0.85,
        )

        result = cli_runner.invoke(app, [
            "score", "--run", str(run_file),
            "--task-dir", str(task_dir), "--no-llm",
        ])

    assert result.exit_code == 0
    assert "t1" in result.output


def test_cli_score_with_output(tmp_path):
    run = RunResult(task_id="t1", agent="claude-code", actual_diff="+x")
    run_file = tmp_path / "run.json"
    run_file.write_text(run.model_dump_json())

    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    suite = TaskSuite(
        name="test", repo_url="https://github.com/x/y",
        tasks=[TaskDefinition(
            id="t1", prompt="Fix", repo_url="https://github.com/x/y",
            base_commit="abc", expected_diff_path="/nonexistent",
        )],
    )
    (task_dir / "suite.json").write_text(suite.model_dump_json())

    report_file = tmp_path / "report.json"

    with patch("agenteval.scorer.Scorer.score_run") as mock_score:
        mock_score.return_value = ScoreResult(
            task_id="t1", agent="claude-code", diff_similarity=0.5,
        )
        result = cli_runner.invoke(app, [
            "score", "--run", str(run_file),
            "--task-dir", str(task_dir), "--no-llm",
            "--output", str(report_file),
        ])

    assert result.exit_code == 0
    assert report_file.exists()
    report = json.loads(report_file.read_text())
    assert len(report) == 1
    assert report[0]["task_id"] == "t1"
