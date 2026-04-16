"""LLM-based prompt cleanup."""


class PromptCleaner:
    """Cleans up raw prompts using an LLM."""

    def cleanup(self, raw_prompt: str) -> str:
        """Clean up a raw prompt extracted from PR data."""
        raise NotImplementedError("Prompt cleanup not yet implemented")
