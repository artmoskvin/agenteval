"""LLM-based prompt cleanup."""

import anthropic
from rich.console import Console

console = Console()

SYSTEM_PROMPT = """\
You are a prompt engineer. Given a raw coding task description extracted from a GitHub PR \
(title + body), convert it into a clear, actionable coding task prompt.

Rules:
- Remove references to specific people, internal links, JIRA/Linear tickets, etc.
- Remove PR boilerplate (checkboxes, template text)
- Preserve the technical intent and requirements
- Write as a direct instruction to a developer
- Output ONLY the cleaned prompt, nothing else — no preamble, no explanation
"""


class PromptCleaner:
    """Cleans up raw prompts using an LLM."""

    def __init__(self, no_llm: bool = False):
        self.no_llm = no_llm
        if not no_llm:
            self.client = anthropic.Anthropic()

    def cleanup(self, raw_prompt: str) -> str:
        """Clean up a raw prompt extracted from PR data."""
        if self.no_llm:
            return raw_prompt

        for attempt in range(2):
            try:
                response = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": raw_prompt}],
                )
                return response.content[0].text
            except Exception as e:
                if attempt == 0:
                    console.print(f"[yellow]LLM API error, retrying: {e}[/yellow]")
                else:
                    console.print(f"[yellow]LLM API failed, using raw prompt: {e}[/yellow]")
                    return raw_prompt
        return raw_prompt  # unreachable but satisfies type checker

    def cleanup_batch(self, prompts: list[str]) -> list[str]:
        """Clean up a batch of prompts."""
        return [self.cleanup(p) for p in prompts]
