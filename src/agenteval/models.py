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
