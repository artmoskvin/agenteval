"""CLI entry point for agenteval."""

import os
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from agenteval import __version__

app = typer.Typer(help="Turn git history into eval suites for coding agents.")
console = Console()


@app.command()
def init(
    repo: str = typer.Option(".", help="Path to git repo"),
    since: str = typer.Option(..., help="Start date (YYYY-MM-DD)"),
    until: Optional[str] = typer.Option(None, help="End date (YYYY-MM-DD)"),
    output: str = typer.Option(".agenteval/tasks", help="Output directory for tasks"),
    github_token: Optional[str] = typer.Option(None, envvar="GITHUB_TOKEN", help="GitHub API token"),
):
    """Initialize an eval suite from git/PR history."""
    # Validate repo path
    repo_path = Path(repo).resolve()
    if not (repo_path / ".git").exists():
        console.print(f"[red]Error:[/red] {repo_path} is not a git repository")
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

    # Create output directory
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    # Print summary
    token_status = "✓ provided" if github_token else "✗ not set"
    console.print(Panel.fit(
        f"[bold]Repo:[/bold]    {repo_path}\n"
        f"[bold]Since:[/bold]   {since_date}\n"
        f"[bold]Until:[/bold]   {until_date or 'now'}\n"
        f"[bold]Output:[/bold]  {output_path.resolve()}\n"
        f"[bold]Token:[/bold]   {token_status}",
        title="agenteval init",
    ))
    console.print(f"[green]Created output directory:[/green] {output_path.resolve()}")


@app.command()
def version():
    """Show the agenteval version."""
    console.print(f"agenteval {__version__}")


if __name__ == "__main__":
    app()
