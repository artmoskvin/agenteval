"""GitHub PR fetching."""

import re
import subprocess
from datetime import date, datetime, timezone

from github import Github
from rich.console import Console

from agenteval.models import PRData

console = Console()

# Patterns to exclude from eval candidates
EXCLUDE_TITLE_PATTERNS = re.compile(r"\b(bump|deps|chore|merge)\b", re.IGNORECASE)


class PRFetcher:
    """Fetches merged PRs from a GitHub repository."""

    def __init__(self, token: str | None = None):
        self.github = Github(token) if token else Github()

    @staticmethod
    def get_repo_from_remote(repo_path: str = ".") -> str:
        """Extract 'owner/repo' from a local git remote URL."""
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        )
        url = result.stdout.strip()
        # Handle SSH (git@github.com:owner/repo.git) and HTTPS
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        if not m:
            raise ValueError(f"Cannot extract owner/repo from remote URL: {url}")
        return m.group(1)

    def fetch_prs(
        self, repo: str, since: date, until: date | None = None,
    ) -> list[PRData]:
        """Fetch merged PRs in the given date range, filtered for eval quality."""
        gh_repo = self.github.get_repo(repo)
        until_date = until or date.today()

        since_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
        until_dt = datetime(until_date.year, until_date.month, until_date.day, 23, 59, 59, tzinfo=timezone.utc)

        console.print(f"[cyan]Fetching merged PRs from {repo}...[/cyan]")
        pulls = gh_repo.get_pulls(state="closed", sort="updated", direction="desc")

        all_prs: list[PRData] = []
        filtered_count = 0

        for pr in pulls:
            if not pr.merged:
                continue
            if pr.merged_at is None:
                continue

            merged = pr.merged_at.replace(tzinfo=timezone.utc) if pr.merged_at.tzinfo is None else pr.merged_at
            if merged < since_dt:
                break  # sorted by updated desc, safe to stop
            if merged > until_dt:
                continue

            # Apply quality filters
            if not self._is_good_candidate(pr):
                filtered_count += 1
                continue

            files = [f.filename for f in pr.get_files()]
            pr_data = PRData(
                number=pr.number,
                title=pr.title,
                body=pr.body or "",
                author=pr.user.login if pr.user else "unknown",
                merged_at=merged,
                base_sha=pr.base.sha,
                head_sha=pr.head.sha,
                files_changed=files,
                additions=pr.additions,
                deletions=pr.deletions,
            )
            all_prs.append(pr_data)

        console.print(
            f"[green]Found {len(all_prs)} candidate PRs[/green] "
            f"(filtered out {filtered_count})"
        )
        return all_prs

    @staticmethod
    def _is_good_candidate(pr) -> bool:
        """Check if a PR is a good eval candidate."""
        # Must have a description
        if not pr.body or not pr.body.strip():
            return False
        # File count limit
        if pr.changed_files >= 10:
            return False
        # Lines changed limit
        if (pr.additions + pr.deletions) >= 500:
            return False
        # Exclude bot PRs
        if pr.user and pr.user.type == "Bot":
            return False
        if pr.user and pr.user.login.endswith("[bot]"):
            return False
        # Exclude by title
        if EXCLUDE_TITLE_PATTERNS.search(pr.title):
            return False
        return True
