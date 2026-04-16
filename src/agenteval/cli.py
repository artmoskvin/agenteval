"""CLI entry point for agenteval."""

import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agenteval import __version__
from agenteval.models import TaskSuite

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
def version():
    """Show the agenteval version."""
    console.print(f"agenteval {__version__}")


if __name__ == "__main__":
    app()
