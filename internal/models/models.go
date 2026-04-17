// Package models defines shared data structures for agenteval.
package models

import "time"

// PRData holds metadata about a merged GitHub pull request.
type PRData struct {
	Number       int
	Title        string
	Body         string
	Author       string
	MergedAt     *time.Time
	BaseSHA      string
	HeadSHA      string
	FilesChanged []string
	Additions    int
	Deletions    int
}

// TaskDefinition describes a single eval task derived from a PR.
type TaskDefinition struct {
	ID               string         `json:"id"`
	Prompt           string         `json:"prompt"`
	RepoURL          string         `json:"repo_url"`
	BaseCommit       string         `json:"base_commit"`
	ExpectedDiffPath string         `json:"expected_diff_path"`
	TestCommands     []string       `json:"test_commands"`
	Metadata         map[string]any `json:"metadata"`
}

// TaskSuite is a named collection of TaskDefinitions.
type TaskSuite struct {
	Name      string           `json:"name"`
	CreatedAt time.Time        `json:"created_at"`
	RepoURL   string           `json:"repo_url"`
	Tasks     []TaskDefinition `json:"tasks"`
}

// RunResult captures the output of an agent run against a task.
type RunResult struct {
	TaskID          string    `json:"task_id"`
	Agent           string    `json:"agent"`
	ActualDiff      string    `json:"actual_diff"`
	Stdout          string    `json:"stdout"`
	Stderr          string    `json:"stderr"`
	ExitCode        int       `json:"exit_code"`
	DurationSeconds float64   `json:"duration_seconds"`
	StartedAt       time.Time `json:"started_at"`
	CompletedAt     time.Time `json:"completed_at"`
	Success         bool      `json:"success"`
}

// ScoreResult holds evaluation scores for a single run.
// Pointer fields (TestsPass, LintClean, LLMJudgeScore) are nil when
// the dimension could not be evaluated (e.g. no tests detected).
type ScoreResult struct {
	TaskID              string  `json:"task_id"`
	Agent               string  `json:"agent"`
	TestsPass           *bool   `json:"tests_pass"`
	TestOutput          string  `json:"test_output"`
	LintClean           *bool   `json:"lint_clean"`
	LintOutput          string  `json:"lint_output"`
	DiffSimilarity      float64 `json:"diff_similarity"`
	LLMJudgeScore       *int    `json:"llm_judge_score"`
	LLMJudgeExplanation string  `json:"llm_judge_explanation"`
	OverallScore        float64 `json:"overall_score"`
}
