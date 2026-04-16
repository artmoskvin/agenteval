"""Extract task definitions from PRs."""

import re
from pathlib import Path

import requests
from github import Github
from rich.console import Console

from agenteval.models import PRData, TaskDefinition

console = Console()


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a URL-friendly slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:max_len].rstrip("-")


class TaskExtractor:
    """Extracts eval task definitions from PR data."""

    def __init__(self, token: str | None = None, repo: str = ""):
        self.github = Github(token) if token else Github()
        self.repo = repo

    def extract_tasks(
        self, prs: list[PRData], output_dir: Path,
    ) -> list[TaskDefinition]:
        """Convert PRs into task definitions, saving diffs to output_dir."""
        output_dir.mkdir(parents=True, exist_ok=True)
        tasks: list[TaskDefinition] = []

        for pr in prs:
            task_id = f"{_slugify(pr.title)}-{pr.number}"
            prompt = f"{pr.title}\n\n{pr.body}"

            # Fetch and save diff
            diff_filename = f"{task_id}.patch"
            diff_path = output_dir / diff_filename
            diff_content = self._fetch_diff(pr.number)
            if diff_content is not None:
                diff_path.write_text(diff_content)
            else:
                console.print(f"[yellow]Warning: Could not fetch diff for PR #{pr.number}[/yellow]")
                diff_path.write_text("")

            task = TaskDefinition(
                id=task_id,
                prompt=prompt,
                repo_url=f"https://github.com/{self.repo}",
                base_commit=pr.base_sha,
                expected_diff_path=str(diff_path),
                test_commands=[],
                metadata={
                    "pr_number": pr.number,
                    "author": pr.author,
                    "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                    "files_changed": pr.files_changed,
                },
            )
            tasks.append(task)
            console.print(f"  [dim]✓ {task_id}[/dim]")

        console.print(f"[green]Extracted {len(tasks)} tasks[/green]")
        return tasks

    def _fetch_diff(self, pr_number: int) -> str | None:
        """Fetch the diff for a PR via GitHub API."""
        try:
            gh_repo = self.github.get_repo(self.repo)
            pr = gh_repo.get_pull(pr_number)
            # Get diff by requesting files and reconstructing, or use the diff URL
            resp = requests.get(
                f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}",
                headers={
                    "Accept": "application/vnd.github.v3.diff",
                    "Authorization": f"token {self.github._Github__requester._Requester__auth.token}"
                    if self.github._Github__requester._Requester__auth
                    else "",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.text
            return None
        except Exception as e:
            console.print(f"[yellow]Warning fetching diff for PR #{pr_number}: {e}[/yellow]")
            return None
