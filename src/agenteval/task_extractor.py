"""Extract task definitions from PRs."""

from agenteval.models import PRData, TaskDefinition


class TaskExtractor:
    """Extracts eval task definitions from PR data."""

    def extract_task(self, pr: PRData) -> TaskDefinition:
        """Convert a PR into a task definition."""
        raise NotImplementedError("Task extraction not yet implemented")
