"""CLI entry point for agenteval."""

import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from agenteval import __version__
from agenteval.models import RunResult, ScoreResult, TaskDefinition, TaskSuite

app = typer.Typer(help="Turn git history into eval suites for coding agents.")
console = Console()


@app.command()
def init(
    repo: str = typer.Option(".", help="Path to git repo or owner/repo"),
    since: str = typer.Option(..., help="Start date (YYYY-MM-DD)"),
    until: Optional[str] = typer.Option(None, help="End date (YYYY-MM-DD)"),
    output: str = typer.Option(".agenteval/tasks", help="Output directory for tasks"),
    github_token: Optional[str] = typer.Option(None, envvar="GITHUB_TOKEN", help="GitHub API token"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM prompt cleanup"),
):
    """Initialize an eval suite from git/PR history."""
    from agenteval.pr_fetcher import PRFetcher
    from agenteval.prompt_cleanup import PromptCleaner
    from agenteval.task_extractor import TaskExtractor

    # Determine repo name
    if "/" in repo and not Path(repo).exists():
        repo_name = repo
    else:
        repo_path = Path(repo).resolve()
        if not (repo_path / ".git").exists():
            console.print(f"[red]Error:[/red] {repo_path} is not a git repository")
            raise typer.Exit(1)
        try:
            repo_name = PRFetcher.get_repo_from_remote(str(repo_path))
        except Exception as e:
            console.print(f"[red]Error extracting repo from remote:[/red] {e}")
            raise typer.Exit(1)

    # Validate dates
    try:
        since_date = date.fromisoformat(since)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid date format: {since}")
        raise typer.Exit(1)

    until_date = None
    if until:
        try:
            until_date = date.fromisoformat(until)
        except ValueError:
            console.print(f"[red]Error:[/red] Invalid date format: {until}")
            raise typer.Exit(1)

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    token_status = "✓ provided" if github_token else "✗ not set"
    console.print(Panel.fit(
        f"[bold]Repo:[/bold]    {repo_name}\n"
        f"[bold]Since:[/bold]   {since_date}\n"
        f"[bold]Until:[/bold]   {until_date or 'now'}\n"
        f"[bold]Output:[/bold]  {output_path.resolve()}\n"
        f"[bold]Token:[/bold]   {token_status}\n"
        f"[bold]LLM:[/bold]     {'disabled' if no_llm else 'enabled'}",
        title="agenteval init",
    ))

    # Fetch PRs
    fetcher = PRFetcher(token=github_token)
    prs = fetcher.fetch_prs(repo_name, since_date, until_date)

    if not prs:
        console.print("[yellow]No candidate PRs found.[/yellow]")
        raise typer.Exit(0)

    # Extract tasks
    extractor = TaskExtractor(token=github_token, repo=repo_name)
    tasks = extractor.extract_tasks(prs, output_path)

    # Prompt cleanup
    cleaner = PromptCleaner(no_llm=no_llm)
    if not no_llm:
        console.print("[cyan]Cleaning up prompts with LLM...[/cyan]")
    cleaned = cleaner.cleanup_batch([t.prompt for t in tasks])
    for task, prompt in zip(tasks, cleaned):
        task.prompt = prompt

    # Save suite
    suite = TaskSuite(
        name=repo_name.replace("/", "-"),
        repo_url=f"https://github.com/{repo_name}",
        tasks=tasks,
    )
    suite_path = output_path / "suite.json"
    suite_path.write_text(suite.model_dump_json(indent=2))

    console.print(Panel.fit(
        f"[bold]PRs found:[/bold]     {len(prs)}\n"
        f"[bold]Tasks created:[/bold] {len(tasks)}\n"
        f"[bold]Suite saved:[/bold]   {suite_path.resolve()}",
        title="[green]✓ Done[/green]",
    ))


@app.command(name="list")
def list_tasks(
    output: str = typer.Option(".agenteval/tasks", help="Tasks directory"),
):
    """List existing tasks from an eval suite."""
    suite_path = Path(output) / "suite.json"
    if not suite_path.exists():
        console.print(f"[yellow]No suite found at {suite_path}[/yellow]")
        console.print("Run [bold]agenteval init[/bold] first.")
        raise typer.Exit(1)

    suite = TaskSuite.model_validate_json(suite_path.read_text())

    table = Table(title=f"Tasks: {suite.name}")
    table.add_column("ID", style="cyan")
    table.add_column("PR #", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Prompt (truncated)")

    for task in suite.tasks:
        pr_num = str(task.metadata.get("pr_number", "?"))
        n_files = str(len(task.metadata.get("files_changed", [])))
        prompt_short = task.prompt[:80].replace("\n", " ") + ("..." if len(task.prompt) > 80 else "")
        table.add_row(task.id, pr_num, n_files, prompt_short)

    console.print(table)
    console.print(f"\n[dim]{len(suite.tasks)} tasks total[/dim]")


@app.command()
def run(
    task: Optional[str] = typer.Option(None, help="Path to single task JSON"),
    suite: Optional[str] = typer.Option(None, help="Path to suite JSON (run all tasks)"),
    agent: str = typer.Option("claude-code", help="Agent name"),
    agent_cmd: Optional[str] = typer.Option(None, "--agent-cmd", help="Custom agent command template"),
    output: str = typer.Option(".agenteval/runs", help="Output directory for results"),
    timeout: int = typer.Option(300, help="Max seconds per task"),
):
    """Run a coding agent against task(s)."""
    import tempfile

    from agenteval.runner import AgentRunner, save_run_result

    tasks: list[TaskDefinition] = []
    if task:
        task_path = Path(task)
        if not task_path.exists():
            console.print(f"[red]Error:[/red] Task file not found: {task}")
            raise typer.Exit(1)
        tasks.append(TaskDefinition.model_validate_json(task_path.read_text()))
    elif suite:
        suite_path = Path(suite)
        if not suite_path.exists():
            console.print(f"[red]Error:[/red] Suite file not found: {suite}")
            raise typer.Exit(1)
        task_suite = TaskSuite.model_validate_json(suite_path.read_text())
        tasks = task_suite.tasks
    else:
        console.print("[red]Error:[/red] Provide --task or --suite")
        raise typer.Exit(1)

    if not tasks:
        console.print("[yellow]No tasks to run.[/yellow]")
        raise typer.Exit(0)

    try:
        runner_inst = AgentRunner(agent=agent, agent_cmd=agent_cmd, timeout=timeout)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    output_dir = Path(output)
    results: list[RunResult] = []

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        overall = progress.add_task(f"Running {len(tasks)} task(s)...", total=len(tasks))
        for t in tasks:
            progress.update(overall, description=f"Running: {t.id}")
            with tempfile.TemporaryDirectory() as tmpdir:
                result = runner_inst.run_task(t, Path(tmpdir))
            saved = save_run_result(result, output_dir)
            results.append(result)
            status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
            console.print(f"  {status} {t.id} ({result.duration_seconds:.1f}s) → {saved}")
            progress.advance(overall)

    passed = sum(1 for r in results if r.success)
    console.print(f"\n[bold]{passed}/{len(results)} tasks completed successfully[/bold]")


@app.command()
def score(
    run_file: Optional[str] = typer.Option(None, "--run", help="Single run result JSON"),
    runs_dir: Optional[str] = typer.Option(None, "--runs", help="Directory of run results"),
    output: Optional[str] = typer.Option(None, help="Save report JSON"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM judge"),
    task_dir: str = typer.Option(".agenteval/tasks", "--task-dir", help="Task definitions directory"),
):
    """Score agent run result(s)."""
    from agenteval.scorer import Scorer

    # Collect run files
    run_paths: list[Path] = []
    if run_file:
        p = Path(run_file)
        if not p.exists():
            console.print(f"[red]Error:[/red] Run file not found: {run_file}")
            raise typer.Exit(1)
        run_paths.append(p)
    elif runs_dir:
        d = Path(runs_dir)
        if not d.is_dir():
            console.print(f"[red]Error:[/red] Not a directory: {runs_dir}")
            raise typer.Exit(1)
        run_paths = sorted(d.glob("*.json"))
    else:
        console.print("[red]Error:[/red] Provide --run or --runs")
        raise typer.Exit(1)

    if not run_paths:
        console.print("[yellow]No run results found.[/yellow]")
        raise typer.Exit(0)

    # Load task definitions for lookup
    task_dir_path = Path(task_dir)
    tasks_by_id: dict[str, TaskDefinition] = {}
    suite_path = task_dir_path / "suite.json"
    if suite_path.exists():
        task_suite = TaskSuite.model_validate_json(suite_path.read_text())
        for t in task_suite.tasks:
            tasks_by_id[t.id] = t

    scorer = Scorer(no_llm=no_llm)
    scores: list[ScoreResult] = []

    for rp in run_paths:
        run_result = RunResult.model_validate_json(rp.read_text())
        task_def = tasks_by_id.get(run_result.task_id)
        if not task_def:
            # Try loading individual task file
            individual = task_dir_path / f"{run_result.task_id}.json"
            if individual.exists():
                task_def = TaskDefinition.model_validate_json(individual.read_text())
            else:
                console.print(f"[yellow]Warning: No task definition for {run_result.task_id}, skipping[/yellow]")
                continue

        repo_path = Path(task_def.repo_url)  # for local repos
        score_result = scorer.score_run(run_result, task_def, repo_path)
        scores.append(score_result)

    # Display table
    table = Table(title="Scoring Results")
    table.add_column("Task", style="cyan")
    table.add_column("Agent")
    table.add_column("Tests", justify="center")
    table.add_column("Lint", justify="center")
    table.add_column("Diff %", justify="right")
    table.add_column("LLM 1-5", justify="right")

    for s in scores:
        tests = "✓" if s.tests_pass else ("✗" if s.tests_pass is False else "—")
        lint = "✓" if s.lint_clean else ("✗" if s.lint_clean is False else "—")
        llm = str(s.llm_judge_score) if s.llm_judge_score is not None else "—"
        table.add_row(s.task_id, s.agent, tests, lint, f"{s.diff_similarity:.2f}", llm)

    console.print(table)

    # Save report
    if output:
        report = [s.model_dump() for s in scores]
        out_path = Path(output)
        out_path.write_text(json.dumps(report, indent=2, default=str))
        console.print(f"\n[green]Report saved to {out_path}[/green]")


@app.command()
def version():
    """Show the agenteval version."""
    console.print(f"agenteval {__version__}")


if __name__ == "__main__":
    app()
