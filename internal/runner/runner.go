// Package runner clones a repo at a specific commit, executes a coding agent,
// and captures the resulting git diff.
package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"agenteval/internal/models"
)

// AgentConfigs maps well-known agent names to their command templates.
// {prompt} is replaced with the path to the prompt file.
// {workdir} is replaced with the repo working directory.
var AgentConfigs = map[string]string{
	"claude-code": "claude -p {prompt} --output-dir {workdir}",
	"aider":       "aider --message {prompt}",
}

// Runner executes a coding agent against a TaskDefinition.
type Runner struct {
	agent       string
	cmdTemplate string
	timeout     time.Duration
}

// New creates a Runner. If agentCmd is non-empty it overrides the built-in
// template for the named agent.
func New(agent, agentCmd string, timeout time.Duration) (*Runner, error) {
	var tmpl string
	if agentCmd != "" {
		tmpl = agentCmd
	} else if t, ok := AgentConfigs[agent]; ok {
		tmpl = t
	} else {
		known := make([]string, 0, len(AgentConfigs))
		for k := range AgentConfigs {
			known = append(known, k)
		}
		return nil, fmt.Errorf("unknown agent %q — known agents: %s — or pass --agent-cmd", agent, strings.Join(known, ", "))
	}
	return &Runner{agent: agent, cmdTemplate: tmpl, timeout: timeout}, nil
}

// RunTask clones the task's repo at base_commit into workDir, runs the agent,
// then returns the combined git diff.
func (r *Runner) RunTask(task models.TaskDefinition, workDir string) models.RunResult {
	startedAt := time.Now()

	result, err := r.runTask(task, workDir, startedAt)
	if err != nil {
		return models.RunResult{
			TaskID:          task.ID,
			Agent:           r.agent,
			Stderr:          err.Error(),
			ExitCode:        -1,
			DurationSeconds: time.Since(startedAt).Seconds(),
			StartedAt:       startedAt,
			CompletedAt:     time.Now(),
			Success:         false,
		}
	}
	return result
}

func (r *Runner) runTask(task models.TaskDefinition, workDir string, startedAt time.Time) (models.RunResult, error) {
	if err := os.MkdirAll(workDir, 0o755); err != nil {
		return models.RunResult{}, fmt.Errorf("creating workdir: %w", err)
	}

	repoDir := filepath.Join(workDir, "repo")

	// Clone
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	if out, err := exec.CommandContext(ctx, "git", "clone", task.RepoURL, repoDir).CombinedOutput(); err != nil {
		return models.RunResult{}, fmt.Errorf("git clone: %w\n%s", err, out)
	}

	// Checkout base commit
	ctx2, cancel2 := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel2()
	if out, err := exec.CommandContext(ctx2, "git", "-C", repoDir, "checkout", task.BaseCommit).CombinedOutput(); err != nil {
		return models.RunResult{}, fmt.Errorf("git checkout %s: %w\n%s", task.BaseCommit, err, out)
	}

	// Write prompt file
	promptFile := filepath.Join(workDir, "prompt.txt")
	if err := os.WriteFile(promptFile, []byte(task.Prompt), 0o644); err != nil {
		return models.RunResult{}, fmt.Errorf("writing prompt file: %w", err)
	}

	// Build command
	cmd := strings.NewReplacer(
		"{prompt}", promptFile,
		"{workdir}", repoDir,
	).Replace(r.cmdTemplate)

	// Run agent
	agentCtx, agentCancel := context.WithTimeout(context.Background(), r.timeout)
	defer agentCancel()

	agentCmd := exec.CommandContext(agentCtx, "sh", "-c", cmd)
	agentCmd.Dir = repoDir
	agentOut, agentErr := agentCmd.CombinedOutput()

	stdout := string(agentOut)
	stderr := ""
	exitCode := 0
	if agentCmd.ProcessState != nil {
		exitCode = agentCmd.ProcessState.ExitCode()
	}
	if agentErr != nil {
		if agentCtx.Err() == context.DeadlineExceeded {
			stderr = "agent timed out"
			exitCode = -1
		} else {
			stderr = agentErr.Error()
		}
	}

	// Capture diff (staged + unstaged)
	diffCtx, diffCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer diffCancel()
	diffOut, _ := exec.CommandContext(diffCtx, "git", "-C", repoDir, "diff").Output()
	cachedOut, _ := exec.CommandContext(diffCtx, "git", "-C", repoDir, "diff", "--cached").Output()
	actualDiff := string(diffOut) + string(cachedOut)

	return models.RunResult{
		TaskID:          task.ID,
		Agent:           r.agent,
		ActualDiff:      actualDiff,
		Stdout:          stdout,
		Stderr:          stderr,
		ExitCode:        exitCode,
		DurationSeconds: time.Since(startedAt).Seconds(),
		StartedAt:       startedAt,
		CompletedAt:     time.Now(),
		Success:         exitCode == 0,
	}, nil
}

// SaveRunResult writes result as JSON to outputDir and returns the file path.
func SaveRunResult(result models.RunResult, outputDir string) (string, error) {
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return "", fmt.Errorf("creating output dir: %w", err)
	}
	timestamp := result.StartedAt.Format("20060102150405")
	filename := fmt.Sprintf("%s-%s-%s.json", result.TaskID, result.Agent, timestamp)
	path := filepath.Join(outputDir, filename)

	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return "", err
	}
	return path, os.WriteFile(path, data, 0o644)
}
