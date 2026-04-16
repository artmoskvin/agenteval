"""Tests for agenteval."""

from datetime import datetime
from typer.testing import CliRunner
from agenteval.cli import app
from agenteval.models import PRData, TaskDefinition, TaskSuite

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "eval suites" in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_pr_data_model():
    pr = PRData(number=1, title="Test", author="dev", base_sha="abc", head_sha="def")
    assert pr.number == 1
    assert pr.additions == 0


def test_task_definition_model():
    task = TaskDefinition(
        id="t1", prompt="Fix bug", repo_url="https://github.com/x/y",
        base_commit="abc", expected_diff_path="diffs/t1.patch",
    )
    assert task.id == "t1"


def test_task_suite_model():
    suite = TaskSuite(name="test", repo_url="https://github.com/x/y")
    assert suite.tasks == []
    assert isinstance(suite.created_at, datetime)
