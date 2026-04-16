"""GitHub PR fetching."""

from datetime import date
from agenteval.models import PRData


class PRFetcher:
    """Fetches merged PRs from a GitHub repository."""

    def __init__(self, token: str | None = None):
        self.token = token

    async def fetch_prs(self, repo: str, since: date, until: date | None = None) -> list[PRData]:
        """Fetch merged PRs in the given date range."""
        raise NotImplementedError("PR fetching not yet implemented")
