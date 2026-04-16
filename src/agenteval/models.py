"""Pydantic models for agenteval."""

from datetime import datetime
from pydantic import BaseModel, Field


class PRData(BaseModel):
    number: int
    title: str
    body: str = ""
    author: str
    merged_at: datetime | None = None
    base_sha: str
    head_sha: str
    files_changed: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0


class TaskDefinition(BaseModel):
    id: str
    prompt: str
    repo_url: str
    base_commit: str
    expected_diff_path: str
    test_commands: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class TaskSuite(BaseModel):
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    repo_url: str
    tasks: list[TaskDefinition] = Field(default_factory=list)


class RunResult(BaseModel):
    task_id: str
    agent: str
    actual_diff: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_seconds: float = 0.0
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime = Field(default_factory=datetime.now)
    success: bool = False


class ScoreResult(BaseModel):
    task_id: str
    agent: str
    tests_pass: bool | None = None
    test_output: str = ""
    lint_clean: bool | None = None
    lint_output: str = ""
    diff_similarity: float = 0.0
    llm_judge_score: int | None = None
    llm_judge_explanation: str = ""
    overall_score: float = 0.0
