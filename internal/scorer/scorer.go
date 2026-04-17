// Package scorer evaluates agent run results across multiple dimensions.
package scorer

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/anthropics/anthropic-sdk-go"

	"agenteval/internal/models"
)

const judgeModel = "claude-sonnet-4-20250514"

const llmJudgePrompt = `You are an expert code reviewer evaluating a coding agent's work.

## Task
%s

## Expected Solution (diff)
%s

## Agent's Solution (diff)
%s

Rate the agent's solution on a scale of 1-5:
1 = Completely wrong or no meaningful attempt
2 = Partially addresses the task but major issues
3 = Reasonable attempt, some issues
4 = Good solution, minor issues
5 = Excellent solution, correct and clean

Respond with EXACTLY this format:
SCORE: <number>
EXPLANATION: <one paragraph>`

// Scorer evaluates agent runs. When NoLLM is true the LLM judge dimension is skipped.
type Scorer struct {
	NoLLM  bool
	client *anthropic.Client
}

// New creates a Scorer. If noLLM is true no Anthropic client is created.
func New(noLLM bool) *Scorer {
	s := &Scorer{NoLLM: noLLM}
	if !noLLM {
		s.client = anthropic.NewClient()
	}
	return s
}

// ScoreRun evaluates a single RunResult against its TaskDefinition.
// repoPath is the local repo path (used for test/lint commands).
func (s *Scorer) ScoreRun(run models.RunResult, task models.TaskDefinition, repoPath string) models.ScoreResult {
	expectedDiff := ""
	if task.ExpectedDiffPath != "" {
		if data, err := os.ReadFile(task.ExpectedDiffPath); err == nil {
			expectedDiff = string(data)
		}
	}

	similarity := DiffSimilarity(expectedDiff, run.ActualDiff)
	testsPass, testOutput := s.runTests(run, task, repoPath)
	lintClean, lintOutput := s.runLint(task, repoPath)
	llmScore, llmExplanation := s.llmJudge(run, task, expectedDiff)

	overall := computeOverall(testsPass, lintClean, similarity, llmScore)

	return models.ScoreResult{
		TaskID:              run.TaskID,
		Agent:               run.Agent,
		TestsPass:           testsPass,
		TestOutput:          testOutput,
		LintClean:           lintClean,
		LintOutput:          lintOutput,
		DiffSimilarity:      similarity,
		LLMJudgeScore:       llmScore,
		LLMJudgeExplanation: llmExplanation,
		OverallScore:        overall,
	}
}

// DiffSimilarity computes the Jaccard similarity of changed lines between two diffs.
func DiffSimilarity(expected, actual string) float64 {
	exp := extractChangedLines(expected)
	act := extractChangedLines(actual)

	if len(exp) == 0 && len(act) == 0 {
		return 1.0
	}
	if len(exp) == 0 || len(act) == 0 {
		return 0.0
	}

	intersection := 0
	for line := range exp {
		if act[line] {
			intersection++
		}
	}
	union := len(exp) + len(act) - intersection
	return float64(intersection) / float64(union)
}

func extractChangedLines(diff string) map[string]bool {
	lines := map[string]bool{}
	for _, line := range strings.Split(diff, "\n") {
		if (strings.HasPrefix(line, "+") || strings.HasPrefix(line, "-")) &&
			!strings.HasPrefix(line, "+++") && !strings.HasPrefix(line, "---") {
			lines[strings.TrimSpace(line)] = true
		}
	}
	return lines
}

func (s *Scorer) runTests(run models.RunResult, task models.TaskDefinition, repoPath string) (*bool, string) {
	commands := task.TestCommands
	if len(commands) == 0 {
		commands = detectTestCommands(repoPath)
	}
	if len(commands) == 0 {
		return nil, ""
	}

	// Checkout base commit
	if out, err := exec.Command("git", "-C", repoPath, "checkout", task.BaseCommit).CombinedOutput(); err != nil {
		f := false
		return &f, fmt.Sprintf("git checkout failed: %s", out)
	}

	// Apply diff
	if run.ActualDiff != "" {
		applyCmd := exec.Command("git", "-C", repoPath, "apply", "--allow-empty")
		applyCmd.Stdin = strings.NewReader(run.ActualDiff)
		if out, err := applyCmd.CombinedOutput(); err != nil {
			f := false
			return &f, "failed to apply diff: " + string(out)
		}
	}

	var outputs []string
	allPass := true
	for _, cmd := range commands {
		c := exec.Command("sh", "-c", cmd)
		c.Dir = repoPath
		out, _ := c.CombinedOutput()
		outputs = append(outputs, string(out))
		if c.ProcessState == nil || c.ProcessState.ExitCode() != 0 {
			allPass = false
		}
	}
	return &allPass, strings.Join(outputs, "\n")
}

func (s *Scorer) runLint(task models.TaskDefinition, repoPath string) (*bool, string) {
	var changedFiles []string
	if files, ok := task.Metadata["files_changed"]; ok {
		switch v := files.(type) {
		case []string:
			changedFiles = v
		case []any:
			for _, f := range v {
				if str, ok := f.(string); ok {
					changedFiles = append(changedFiles, str)
				}
			}
		}
	}
	commands := detectLintCommands(repoPath, changedFiles)
	if len(commands) == 0 {
		return nil, ""
	}

	var outputs []string
	allClean := true
	for _, cmd := range commands {
		c := exec.Command("sh", "-c", cmd)
		c.Dir = repoPath
		out, _ := c.CombinedOutput()
		outputs = append(outputs, string(out))
		if c.ProcessState == nil || c.ProcessState.ExitCode() != 0 {
			allClean = false
		}
	}
	return &allClean, strings.Join(outputs, "\n")
}

func (s *Scorer) llmJudge(run models.RunResult, task models.TaskDefinition, expectedDiff string) (*int, string) {
	if s.NoLLM {
		return nil, ""
	}

	expDisplay := expectedDiff
	if expDisplay == "" {
		expDisplay = "(not available)"
	}
	actDisplay := run.ActualDiff
	if actDisplay == "" {
		actDisplay = "(empty — agent produced no changes)"
	}

	userContent := fmt.Sprintf(llmJudgePrompt, task.Prompt, expDisplay, actDisplay)

	msg, err := s.client.Messages.New(context.Background(), anthropic.MessageNewParams{
		Model:     anthropic.F(anthropic.Model(judgeModel)),
		MaxTokens: anthropic.F(int64(512)),
		Messages: anthropic.F([]anthropic.MessageParam{
			anthropic.NewUserMessage(anthropic.NewTextBlock(userContent)),
		}),
	})
	if err != nil {
		fmt.Printf("  LLM judge failed: %v\n", err)
		return nil, err.Error()
	}

	var text string
	for _, block := range msg.Content {
		if block.Type == anthropic.ContentBlockTypeText {
			text = block.Text
			break
		}
	}

	score, explanation := parseJudgeResponse(text)
	return score, explanation
}

var (
	scoreRe       = regexp.MustCompile(`(?m)^SCORE:\s*(\d+)`)
	explanationRe = regexp.MustCompile(`(?m)^EXPLANATION:\s*(.+)`)
)

func parseJudgeResponse(text string) (*int, string) {
	var score *int
	if m := scoreRe.FindStringSubmatch(text); m != nil {
		if n, err := strconv.Atoi(m[1]); err == nil {
			clamped := n
			if clamped < 1 {
				clamped = 1
			}
			if clamped > 5 {
				clamped = 5
			}
			score = &clamped
		}
	}
	explanation := ""
	if m := explanationRe.FindStringSubmatch(text); m != nil {
		explanation = strings.TrimSpace(m[1])
	}
	return score, explanation
}

func computeOverall(testsPass, lintClean *bool, diffSim float64, llmScore *int) float64 {
	totalWeight := 0.0
	weightedSum := 0.0

	if testsPass != nil {
		totalWeight += 0.35
		if *testsPass {
			weightedSum += 0.35
		}
	}
	if lintClean != nil {
		totalWeight += 0.10
		if *lintClean {
			weightedSum += 0.10
		}
	}
	totalWeight += 0.25
	weightedSum += 0.25 * diffSim

	if llmScore != nil {
		totalWeight += 0.30
		weightedSum += 0.30 * (float64(*llmScore) / 5.0)
	}

	if totalWeight == 0 {
		return 0
	}
	v := weightedSum / totalWeight
	// Round to 4 decimal places
	return float64(int(v*10000+0.5)) / 10000
}

func detectTestCommands(repoPath string) []string {
	if fileExists(filepath.Join(repoPath, "pytest.ini")) || fileExists(filepath.Join(repoPath, "pyproject.toml")) {
		return []string{"python -m pytest"}
	}
	if fileExists(filepath.Join(repoPath, "package.json")) {
		return []string{"npm test"}
	}
	if fileExists(filepath.Join(repoPath, "Makefile")) {
		return []string{"make test"}
	}
	return nil
}

func detectLintCommands(repoPath string, changedFiles []string) []string {
	var pyFiles, jsFiles []string
	for _, f := range changedFiles {
		if strings.HasSuffix(f, ".py") {
			pyFiles = append(pyFiles, f)
		} else if strings.HasSuffix(f, ".js") || strings.HasSuffix(f, ".ts") ||
			strings.HasSuffix(f, ".jsx") || strings.HasSuffix(f, ".tsx") {
			jsFiles = append(jsFiles, f)
		}
	}
	var cmds []string
	if len(pyFiles) > 0 {
		cmds = append(cmds, "ruff check "+strings.Join(pyFiles, " "))
	}
	if len(jsFiles) > 0 {
		cmds = append(cmds, "eslint "+strings.Join(jsFiles, " "))
	}
	return cmds
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
