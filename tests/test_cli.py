"""Tests for agenteval."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agenteval.cli import app
from agenteval.models import PRData, TaskDefinition, TaskSuite

runner = CliRunner()


# --- Model tests ---

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
    assert pr.files_changed == []


def test_task_definition_model():
    task = TaskDefinition(
        id="t1", prompt="Fix bug", repo_url="https://github.com/x/y",
        base_commit="abc", expected_diff_path="diffs/t1.patch",
    )
    assert task.id == "t1"
    assert task.test_commands == []


def test_task_suite_model():
    suite = TaskSuite(name="test", repo_url="https://github.com/x/y")
    assert suite.tasks == []
    assert isinstance(suite.created_at, datetime)


# --- PR Fetcher tests ---

def _make_mock_pr(
    number=1, title="Fix auth bug", body="Fixes the auth flow",
    merged=True, changed_files=3, additions=50, deletions=20,
    user_login="dev", user_type="User", merged_at=None,
    base_sha="aaa", head_sha="bbb", filenames=None,
):
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = body
    pr.merged = merged
    pr.changed_files = changed_files
    pr.additions = additions
    pr.deletions = deletions
    pr.merged_at = merged_at or datetime(2025, 6, 15, tzinfo=timezone.utc)
    pr.base = MagicMock(sha=base_sha)
    pr.head = MagicMock(sha=head_sha)
    pr.user = MagicMock(login=user_login, type=user_type)
    files = []
    for fn in (filenames or ["src/auth.py", "tests/test_auth.py"]):
        f = MagicMock()
        f.filename = fn
        files.append(f)
    pr.get_files.return_value = files
    return pr


def test_pr_filter_good_candidate():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr()
    assert PRFetcher._is_good_candidate(pr) is True


def test_pr_filter_no_body():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(body="")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_empty_body():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(body="   ")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_too_many_files():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(changed_files=15)
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_too_many_lines():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(additions=400, deletions=200)
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_bot():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(user_type="Bot")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_bot_login():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(user_login="dependabot[bot]")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_chore_title():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(title="chore: update config")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_bump_title():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(title="Bump lodash from 4.17.20 to 4.17.21")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_merge_title():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(title="Merge branch main into dev")
    assert PRFetcher._is_good_candidate(pr) is False


def test_pr_filter_deps_title():
    from agenteval.pr_fetcher import PRFetcher
    pr = _make_mock_pr(title="Update deps for security patch")
    assert PRFetcher._is_good_candidate(pr) is False


def test_get_repo_from_remote_ssh():
    from agenteval.pr_fetcher import PRFetcher
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="git@github.com:owner/repo.git\n")
        assert PRFetcher.get_repo_from_remote("/tmp") == "owner/repo"


def test_get_repo_from_remote_https():
    from agenteval.pr_fetcher import PRFetcher
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="https://github.com/owner/repo.git\n")
        assert PRFetcher.get_repo_from_remote("/tmp") == "owner/repo"


def test_get_repo_from_remote_no_git():
    from agenteval.pr_fetcher import PRFetcher
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="https://github.com/owner/repo\n")
        assert PRFetcher.get_repo_from_remote("/tmp") == "owner/repo"


# --- Task Extractor tests ---

def test_task_extraction(tmp_path):
    from agenteval.task_extractor import TaskExtractor, _slugify

    extractor = TaskExtractor(repo="owner/repo")
    pr = PRData(
        number=42, title="Fix auth bug", body="Fixes the login flow",
        author="dev", base_sha="abc123", head_sha="def456",
        merged_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
        files_changed=["src/auth.py"],
        additions=10, deletions=5,
    )

    with patch.object(extractor, "_fetch_diff", return_value="diff --git a/f b/f\n"):
        tasks = extractor.extract_tasks([pr], tmp_path)

    assert len(tasks) == 1
    t = tasks[0]
    assert "fix-auth-bug-42" == t.id
    assert t.base_commit == "abc123"
    assert t.metadata["pr_number"] == 42
    assert t.metadata["author"] == "dev"
    assert (tmp_path / "fix-auth-bug-42.patch").exists()


def test_slugify():
    from agenteval.task_extractor import _slugify
    assert _slugify("Fix Auth Bug!") == "fix-auth-bug"
    assert _slugify("  spaces  ") == "spaces"


# --- Prompt Cleanup tests ---

def test_prompt_cleanup_no_llm():
    from agenteval.prompt_cleanup import PromptCleaner
    cleaner = PromptCleaner(no_llm=True)
    assert cleaner.cleanup("raw prompt") == "raw prompt"


def test_prompt_cleanup_batch_no_llm():
    from agenteval.prompt_cleanup import PromptCleaner
    cleaner = PromptCleaner(no_llm=True)
    result = cleaner.cleanup_batch(["a", "b"])
    assert result == ["a", "b"]


def test_prompt_cleanup_with_llm():
    from agenteval.prompt_cleanup import PromptCleaner
    with patch("agenteval.prompt_cleanup.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Clean task description")]
        mock_client.messages.create.return_value = mock_response

        cleaner = PromptCleaner(no_llm=False)
        result = cleaner.cleanup("messy prompt with JIRA-123")
        assert result == "Clean task description"


def test_prompt_cleanup_api_error_retries():
    from agenteval.prompt_cleanup import PromptCleaner
    with patch("agenteval.prompt_cleanup.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API down")

        cleaner = PromptCleaner(no_llm=False)
        result = cleaner.cleanup("raw prompt")
        assert result == "raw prompt"
        assert mock_client.messages.create.call_count == 2


# --- CLI tests ---

def test_cli_list_no_suite(tmp_path):
    result = runner.invoke(app, ["list", "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "No suite found" in result.output


def test_cli_list_with_suite(tmp_path):
    suite = TaskSuite(
        name="test-repo",
        repo_url="https://github.com/test/repo",
        tasks=[
            TaskDefinition(
                id="fix-bug-1", prompt="Fix the bug\n\nDetails here",
                repo_url="https://github.com/test/repo",
                base_commit="abc", expected_diff_path="fix-bug-1.patch",
                metadata={"pr_number": 1, "files_changed": ["a.py"]},
            ),
        ],
    )
    (tmp_path / "suite.json").write_text(suite.model_dump_json(indent=2))

    result = runner.invoke(app, ["list", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "fix-bug-1" in result.output
    assert "1 tasks total" in result.output


def test_cli_init_with_mocked_github(tmp_path):
    """Test full init flow with mocked GitHub API."""
    mock_pr = _make_mock_pr(number=99, title="Add feature X", body="Implements feature X")

    with patch("agenteval.pr_fetcher.Github") as MockGithub, \
         patch("agenteval.task_extractor.Github") as MockGithub2, \
         patch("agenteval.task_extractor.requests") as mock_requests:

        # Setup PR fetcher mock
        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = [mock_pr]
        MockGithub.return_value.get_repo.return_value = mock_repo

        # Setup task extractor diff fetch mock
        MockGithub2.return_value.get_repo.return_value = mock_repo
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff content"
        mock_requests.get.return_value = mock_resp
        MockGithub2.return_value._Github__requester._Requester__auth = None

        output = str(tmp_path / "tasks")
        result = runner.invoke(app, [
            "init",
            "--repo", "owner/repo",
            "--since", "2025-06-01",
            "--until", "2025-06-30",
            "--output", output,
            "--no-llm",
            "--github-token", "fake-token",
        ])

        assert result.exit_code == 0, result.output
        assert "Tasks created" in result.output
        suite_file = tmp_path / "tasks" / "suite.json"
        assert suite_file.exists()
        suite = json.loads(suite_file.read_text())
        assert len(suite["tasks"]) == 1
        assert suite["tasks"][0]["id"] == "add-feature-x-99"
