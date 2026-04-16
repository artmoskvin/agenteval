"""Scorer — evaluates agent output across multiple dimensions."""

import subprocess
from pathlib import Path

import anthropic
from rich.console import Console

from agenteval.models import RunResult, ScoreResult, TaskDefinition

console = Console()

LLM_JUDGE_PROMPT = """\
You are an expert code reviewer evaluating a coding agent's work.

## Task
{prompt}

## Expected Solution (diff)
{expected_diff}

## Agent's Solution (diff)
{actual_diff}

Rate the agent's solution on a scale of 1-5:
1 = Completely wrong or no meaningful attempt
2 = Partially addresses the task but major issues
3 = Reasonable attempt, some issues
4 = Good solution, minor issues
5 = Excellent solution, correct and clean

Respond with EXACTLY this format:
SCORE: <number>
EXPLANATION: <one paragraph>
"""


def diff_similarity(expected: str, actual: str) -> float:
    """Jaccard similarity of changed lines between two diffs."""
    def _extract_changed_lines(diff_text: str) -> set[str]:
        lines = set()
        for line in diff_text.splitlines():
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                lines.add(line.strip())
        return lines

    expected_lines = _extract_changed_lines(expected)
    actual_lines = _extract_changed_lines(actual)

    if not expected_lines and not actual_lines:
        return 1.0
    if not expected_lines or not actual_lines:
        return 0.0

    intersection = expected_lines & actual_lines
    union = expected_lines | actual_lines
    return len(intersection) / len(union)


def _detect_test_commands(repo_path: Path) -> list[str]:
    """Detect test commands based on repo contents."""
    if (repo_path / "pytest.ini").exists() or (repo_path / "pyproject.toml").exists():
        return ["python -m pytest"]
    if (repo_path / "package.json").exists():
        return ["npm test"]
    if (repo_path / "Makefile").exists():
        return ["make test"]
    return []


def _detect_lint_commands(repo_path: Path, changed_files: list[str]) -> list[str]:
    """Detect linter commands based on repo contents."""
    py_files = [f for f in changed_files if f.endswith(".py")]
    js_files = [f for f in changed_files if f.endswith((".js", ".ts", ".jsx", ".tsx"))]

    commands = []
    if py_files:
        file_args = " ".join(py_files)
        commands.append(f"ruff check {file_args}")
    if js_files:
        file_args = " ".join(js_files)
        commands.append(f"eslint {file_args}")
    return commands


class Scorer:
    """Scores agent run results against task definitions."""

    def __init__(self, no_llm: bool = False):
        self.no_llm = no_llm
        if not no_llm:
            self.client = anthropic.Anthropic()

    def score_run(
        self, run: RunResult, task: TaskDefinition, repo_path: Path,
    ) -> ScoreResult:
        """Score a single run result against a task definition."""
        # Load expected diff
        expected_diff = ""
        if task.expected_diff_path and Path(task.expected_diff_path).exists():
            expected_diff = Path(task.expected_diff_path).read_text()

        # Diff similarity
        similarity = diff_similarity(expected_diff, run.actual_diff)

        # Tests
        tests_pass, test_output = self._run_tests(run, task, repo_path)

        # Lint
        lint_clean, lint_output = self._run_lint(run, task, repo_path)

        # LLM judge
        llm_score, llm_explanation = self._llm_judge(run, task, expected_diff)

        # Overall score (weighted composite)
        overall = self._compute_overall(
            tests_pass=tests_pass,
            lint_clean=lint_clean,
            diff_similarity=similarity,
            llm_judge_score=llm_score,
        )

        return ScoreResult(
            task_id=run.task_id,
            agent=run.agent,
            tests_pass=tests_pass,
            test_output=test_output,
            lint_clean=lint_clean,
            lint_output=lint_output,
            diff_similarity=similarity,
            llm_judge_score=llm_score,
            llm_judge_explanation=llm_explanation,
            overall_score=overall,
        )

    def _run_tests(
        self, run: RunResult, task: TaskDefinition, repo_path: Path,
    ) -> tuple[bool | None, str]:
        """Run test commands and return (pass, output)."""
        commands = task.test_commands or _detect_test_commands(repo_path)
        if not commands:
            return None, ""

        try:
            # Checkout base, apply diff
            subprocess.run(
                ["git", "checkout", task.base_commit],
                capture_output=True, text=True, check=True,
                cwd=str(repo_path), timeout=30,
            )
            if run.actual_diff:
                proc = subprocess.run(
                    ["git", "apply", "--allow-empty"],
                    input=run.actual_diff,
                    capture_output=True, text=True,
                    cwd=str(repo_path), timeout=30,
                )
                if proc.returncode != 0:
                    return False, f"Failed to apply diff: {proc.stderr}"

            # Run tests
            outputs = []
            all_pass = True
            for cmd in commands:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    cwd=str(repo_path), timeout=120,
                )
                outputs.append(result.stdout + result.stderr)
                if result.returncode != 0:
                    all_pass = False

            return all_pass, "\n".join(outputs)
        except Exception as e:
            return False, str(e)

    def _run_lint(
        self, run: RunResult, task: TaskDefinition, repo_path: Path,
    ) -> tuple[bool | None, str]:
        """Run linter on changed files."""
        changed_files = task.metadata.get("files_changed", [])
        commands = _detect_lint_commands(repo_path, changed_files)
        if not commands:
            return None, ""

        try:
            outputs = []
            all_clean = True
            for cmd in commands:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    cwd=str(repo_path), timeout=60,
                )
                outputs.append(result.stdout + result.stderr)
                if result.returncode != 0:
                    all_clean = False
            return all_clean, "\n".join(outputs)
        except Exception as e:
            return False, str(e)

    def _llm_judge(
        self, run: RunResult, task: TaskDefinition, expected_diff: str,
    ) -> tuple[int | None, str]:
        """Use LLM to judge quality. Returns (score 1-5, explanation)."""
        if self.no_llm:
            return None, ""

        prompt = LLM_JUDGE_PROMPT.format(
            prompt=task.prompt,
            expected_diff=expected_diff or "(not available)",
            actual_diff=run.actual_diff or "(empty — agent produced no changes)",
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text

            # Parse response
            score = None
            explanation = ""
            for line in text.splitlines():
                if line.startswith("SCORE:"):
                    try:
                        score = int(line.split(":")[1].strip())
                        score = max(1, min(5, score))
                    except ValueError:
                        pass
                elif line.startswith("EXPLANATION:"):
                    explanation = line.split(":", 1)[1].strip()

            return score, explanation
        except Exception as e:
            console.print(f"[yellow]LLM judge failed: {e}[/yellow]")
            return None, str(e)

    @staticmethod
    def _compute_overall(
        tests_pass: bool | None,
        lint_clean: bool | None,
        diff_similarity: float,
        llm_judge_score: int | None,
    ) -> float:
        """Compute weighted overall score (0-1)."""
        total_weight = 0.0
        weighted_sum = 0.0

        if tests_pass is not None:
            total_weight += 0.35
            weighted_sum += 0.35 * (1.0 if tests_pass else 0.0)

        if lint_clean is not None:
            total_weight += 0.1
            weighted_sum += 0.1 * (1.0 if lint_clean else 0.0)

        # Diff similarity always contributes
        total_weight += 0.25
        weighted_sum += 0.25 * diff_similarity

        if llm_judge_score is not None:
            total_weight += 0.3
            weighted_sum += 0.3 * (llm_judge_score / 5.0)

        if total_weight == 0:
            return 0.0
        return round(weighted_sum / total_weight, 4)
