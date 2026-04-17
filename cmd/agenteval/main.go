// Command agenteval turns git history into eval suites for coding agents.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"

	"agenteval/internal/extractor"
	"agenteval/internal/fetcher"
	"agenteval/internal/models"
	"agenteval/internal/prompt"
	"agenteval/internal/runner"
	"agenteval/internal/scorer"
)

const version = "0.1.0"

// ── ANSI colour helpers ────────────────────────────────────────────────────

const (
	red    = "\033[31m"
	green  = "\033[32m"
	yellow = "\033[33m"
	cyan   = "\033[36m"
	dim    = "\033[2m"
	bold   = "\033[1m"
	reset  = "\033[0m"
)

func color(code, s string) string { return code + s + reset }

// ── Root command ───────────────────────────────────────────────────────────

func main() {
	root := &cobra.Command{
		Use:   "agenteval",
		Short: "Turn git history into eval suites for coding agents.",
		CompletionOptions: cobra.CompletionOptions{DisableDefaultCmd: true},
	}
	root.AddCommand(initCmd(), listCmd(), runCmd(), scoreCmd(), versionCmd())
	if err := root.Execute(); err != nil {
		os.Exit(1)
	}
}

// ── agenteval init ─────────────────────────────────────────────────────────

func initCmd() *cobra.Command {
	var (
		repo        string
		since       string
		until       string
		output      string
		githubToken string
		noLLM       bool
	)
	cmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize an eval suite from git/PR history.",
		RunE: func(cmd *cobra.Command, args []string) error {
			// Resolve repo name and detect GHE
			var repoName, baseURL string
			if strings.Contains(repo, "/") && !dirExists(repo) {
				repoName = repo
			} else {
				repoPath, err := filepath.Abs(repo)
				if err != nil {
					return fmt.Errorf("resolving repo path: %w", err)
				}
				if !dirExists(filepath.Join(repoPath, ".git")) {
					return fmt.Errorf("%s is not a git repository", repoPath)
				}
				baseURL = fetcher.DetectGitHubBaseURL(repoPath)
				repoName, err = fetcher.GetRepoFromRemote(repoPath)
				if err != nil {
					return fmt.Errorf("extracting repo from remote: %w", err)
				}
			}

			// Parse dates
			sinceDate, err := time.Parse("2006-01-02", since)
			if err != nil {
				return fmt.Errorf("invalid --since date %q: %w", since, err)
			}
			untilDate := time.Now().UTC()
			if until != "" {
				untilDate, err = time.Parse("2006-01-02", until)
				if err != nil {
					return fmt.Errorf("invalid --until date %q: %w", until, err)
				}
				// Include the full until day
				untilDate = untilDate.Add(23*time.Hour + 59*time.Minute + 59*time.Second)
			}

			outputPath, err := filepath.Abs(output)
			if err != nil {
				return err
			}
			if err := os.MkdirAll(outputPath, 0o755); err != nil {
				return fmt.Errorf("creating output dir: %w", err)
			}

			tokenStatus := color(red, "✗ not set")
			if githubToken != "" {
				tokenStatus = color(green, "✓ provided")
			}
			llmStatus := "enabled"
			if noLLM {
				llmStatus = "disabled"
			}
			untilStr := until
			if untilStr == "" {
				untilStr = "now"
			}
			printBox("agenteval init",
				fmt.Sprintf("Repo:    %s\nSince:   %s\nUntil:   %s\nOutput:  %s\nToken:   %s\nLLM:     %s",
					repoName, since, untilStr, outputPath, tokenStatus, llmStatus))

			if baseURL != "" {
				fmt.Println(color(dim, "Detected GitHub Enterprise: "+baseURL))
			}

			// Split owner/repo
			parts := strings.SplitN(repoName, "/", 2)
			if len(parts) != 2 {
				return fmt.Errorf("repo must be owner/repo, got %q", repoName)
			}
			owner, repo := parts[0], parts[1]

			// Fetch PRs
			f, err := fetcher.New(githubToken, baseURL)
			if err != nil {
				return fmt.Errorf("creating fetcher: %w", err)
			}
			fmt.Printf("%s\n", color(cyan, "Fetching merged PRs from "+repoName+"..."))
			prs, err := f.FetchPRs(cmd.Context(), owner, repo, sinceDate, untilDate)
			if err != nil {
				return err
			}
			if len(prs) == 0 {
				fmt.Println(color(yellow, "No candidate PRs found."))
				return nil
			}

			// Extract tasks
			ext := extractor.New(githubToken, repoName, baseURL)
			tasks, err := ext.ExtractTasks(prs, outputPath)
			if err != nil {
				return fmt.Errorf("extracting tasks: %w", err)
			}

			// Prompt cleanup
			cleaner := prompt.New(noLLM)
			if !noLLM {
				fmt.Println(color(cyan, "Cleaning up prompts with LLM..."))
			}
			rawPrompts := make([]string, len(tasks))
			for i, t := range tasks {
				rawPrompts[i] = t.Prompt
			}
			cleaned := cleaner.CleanupBatch(rawPrompts)
			for i := range tasks {
				tasks[i].Prompt = cleaned[i]
			}

			// Save suite
			suiteName := strings.ReplaceAll(repoName, "/", "-")
			suite := models.TaskSuite{
				Name:      suiteName,
				CreatedAt: time.Now(),
				RepoURL:   "https://github.com/" + repoName,
				Tasks:     tasks,
			}
			suitePath := filepath.Join(outputPath, "suite.json")
			suiteData, err := json.MarshalIndent(suite, "", "  ")
			if err != nil {
				return err
			}
			if err := os.WriteFile(suitePath, suiteData, 0o644); err != nil {
				return fmt.Errorf("writing suite.json: %w", err)
			}

			printBox(color(green, "✓ Done"),
				fmt.Sprintf("PRs found:     %d\nTasks created: %d\nSuite saved:   %s",
					len(prs), len(tasks), suitePath))
			return nil
		},
	}
	cmd.Flags().StringVar(&repo, "repo", ".", "Path to git repo or owner/repo")
	cmd.Flags().StringVar(&since, "since", "", "Start date (YYYY-MM-DD)")
	cmd.Flags().StringVar(&until, "until", "", "End date (YYYY-MM-DD)")
	cmd.Flags().StringVar(&output, "output", ".agenteval/tasks", "Output directory for tasks")
	cmd.Flags().StringVar(&githubToken, "github-token", os.Getenv("GITHUB_TOKEN"), "GitHub API token")
	cmd.Flags().BoolVar(&noLLM, "no-llm", false, "Skip LLM prompt cleanup")
	_ = cmd.MarkFlagRequired("since")
	return cmd
}

// ── agenteval list ─────────────────────────────────────────────────────────

func listCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List existing tasks from an eval suite.",
		RunE: func(cmd *cobra.Command, args []string) error {
			suitePath := filepath.Join(output, "suite.json")
			if !fileExists(suitePath) {
				fmt.Printf("%s\nRun %sagenteval init%s first.\n",
					color(yellow, "No suite found at "+suitePath), bold, reset)
				return fmt.Errorf("suite not found")
			}

			data, err := os.ReadFile(suitePath)
			if err != nil {
				return err
			}
			var suite models.TaskSuite
			if err := json.Unmarshal(data, &suite); err != nil {
				return fmt.Errorf("parsing suite.json: %w", err)
			}

			fmt.Printf("\n%sTasks: %s%s\n\n", bold, suite.Name, reset)
			w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
			fmt.Fprintln(w, color(cyan, "ID")+"\t"+"PR #\t"+"Files\t"+"Prompt (truncated)")
			fmt.Fprintln(w, strings.Repeat("-", 20)+"\t"+"----\t"+"-----\t"+strings.Repeat("-", 40))
			for _, t := range suite.Tasks {
				prNum := "?"
				if v, ok := t.Metadata["pr_number"]; ok {
					prNum = fmt.Sprintf("%v", v)
				}
				nFiles := "0"
				if v, ok := t.Metadata["files_changed"]; ok {
					switch f := v.(type) {
					case []any:
						nFiles = fmt.Sprintf("%d", len(f))
					case []string:
						nFiles = fmt.Sprintf("%d", len(f))
					}
				}
				snippet := t.Prompt
				snippet = strings.ReplaceAll(snippet, "\n", " ")
				if len(snippet) > 80 {
					snippet = snippet[:80] + "..."
				}
				fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", color(cyan, t.ID), prNum, nFiles, snippet)
			}
			w.Flush()
			fmt.Printf("\n%s%d tasks total%s\n", dim, len(suite.Tasks), reset)
			return nil
		},
	}
	cmd.Flags().StringVar(&output, "output", ".agenteval/tasks", "Tasks directory")
	return cmd
}

// ── agenteval run ──────────────────────────────────────────────────────────

func runCmd() *cobra.Command {
	var (
		taskFile string
		suiteFile string
		agent    string
		agentCmd string
		output   string
		timeout  int
	)
	cmd := &cobra.Command{
		Use:   "run",
		Short: "Run a coding agent against task(s).",
		RunE: func(cmd *cobra.Command, args []string) error {
			var tasks []models.TaskDefinition

			switch {
			case taskFile != "":
				data, err := os.ReadFile(taskFile)
				if err != nil {
					return fmt.Errorf("reading task file: %w", err)
				}
				var t models.TaskDefinition
				if err := json.Unmarshal(data, &t); err != nil {
					return fmt.Errorf("parsing task JSON: %w", err)
				}
				tasks = []models.TaskDefinition{t}

			case suiteFile != "":
				data, err := os.ReadFile(suiteFile)
				if err != nil {
					return fmt.Errorf("reading suite file: %w", err)
				}
				var suite models.TaskSuite
				if err := json.Unmarshal(data, &suite); err != nil {
					return fmt.Errorf("parsing suite JSON: %w", err)
				}
				tasks = suite.Tasks

			default:
				return fmt.Errorf("provide --task or --suite")
			}

			if len(tasks) == 0 {
				fmt.Println(color(yellow, "No tasks to run."))
				return nil
			}

			r, err := runner.New(agent, agentCmd, time.Duration(timeout)*time.Second)
			if err != nil {
				return err
			}

			fmt.Printf("Running %d task(s) with agent %q...\n\n", len(tasks), agent)

			var results []models.RunResult
			for _, t := range tasks {
				fmt.Printf("  → %s", t.ID)
				tmpDir, err := os.MkdirTemp("", "agenteval-*")
				if err != nil {
					return fmt.Errorf("creating temp dir: %w", err)
				}
				result := r.RunTask(t, tmpDir)
				os.RemoveAll(tmpDir)

				savedPath, err := runner.SaveRunResult(result, output)
				if err != nil {
					fmt.Printf(" %s (save failed: %v)\n", color(red, "✗"), err)
				} else {
					status := color(green, "✓")
					if !result.Success {
						status = color(red, "✗")
					}
					fmt.Printf(" %s (%.1fs) → %s\n", status, result.DurationSeconds, savedPath)
				}
				results = append(results, result)
			}

			passed := 0
			for _, r := range results {
				if r.Success {
					passed++
				}
			}
			fmt.Printf("\n%s%d/%d tasks completed successfully%s\n", bold, passed, len(results), reset)
			return nil
		},
	}
	cmd.Flags().StringVar(&taskFile, "task", "", "Path to single task JSON")
	cmd.Flags().StringVar(&suiteFile, "suite", "", "Path to suite JSON (run all tasks)")
	cmd.Flags().StringVar(&agent, "agent", "claude-code", "Agent name")
	cmd.Flags().StringVar(&agentCmd, "agent-cmd", "", "Custom agent command template")
	cmd.Flags().StringVar(&output, "output", ".agenteval/runs", "Output directory for results")
	cmd.Flags().IntVar(&timeout, "timeout", 300, "Max seconds per task")
	return cmd
}

// ── agenteval score ────────────────────────────────────────────────────────

func scoreCmd() *cobra.Command {
	var (
		runFile  string
		runsDir  string
		output   string
		noLLM    bool
		taskDir  string
	)
	cmd := &cobra.Command{
		Use:   "score",
		Short: "Score agent run result(s).",
		RunE: func(cmd *cobra.Command, args []string) error {
			var runPaths []string
			switch {
			case runFile != "":
				if !fileExists(runFile) {
					return fmt.Errorf("run file not found: %s", runFile)
				}
				runPaths = []string{runFile}

			case runsDir != "":
				entries, err := filepath.Glob(filepath.Join(runsDir, "*.json"))
				if err != nil {
					return err
				}
				runPaths = entries

			default:
				return fmt.Errorf("provide --run or --runs")
			}

			if len(runPaths) == 0 {
				fmt.Println(color(yellow, "No run results found."))
				return nil
			}

			// Load task definitions from suite
			tasksByID := map[string]models.TaskDefinition{}
			suitePath := filepath.Join(taskDir, "suite.json")
			if fileExists(suitePath) {
				data, err := os.ReadFile(suitePath)
				if err == nil {
					var suite models.TaskSuite
					if err := json.Unmarshal(data, &suite); err == nil {
						for _, t := range suite.Tasks {
							tasksByID[t.ID] = t
						}
					}
				}
			}

			s := scorer.New(noLLM)
			var scores []models.ScoreResult

			for _, rp := range runPaths {
				data, err := os.ReadFile(rp)
				if err != nil {
					fmt.Printf("%s: %v\n", color(yellow, "Warning reading "+rp), err)
					continue
				}
				var run models.RunResult
				if err := json.Unmarshal(data, &run); err != nil {
					fmt.Printf("%s: %v\n", color(yellow, "Warning parsing "+rp), err)
					continue
				}

				task, ok := tasksByID[run.TaskID]
				if !ok {
					individualPath := filepath.Join(taskDir, run.TaskID+".json")
					if fileExists(individualPath) {
						d, err := os.ReadFile(individualPath)
						if err == nil {
							_ = json.Unmarshal(d, &task)
							ok = true
						}
					}
					if !ok {
						fmt.Printf("%s\n", color(yellow, "Warning: no task definition for "+run.TaskID+", skipping"))
						continue
					}
				}

				// repoPath mirrors Python's behaviour: use repo_url as a local path fallback
				repoPath := task.RepoURL
				score := s.ScoreRun(run, task, repoPath)
				scores = append(scores, score)
			}

			// Print table
			fmt.Printf("\n%sScoring Results%s\n\n", bold, reset)
			w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
			fmt.Fprintln(w, color(cyan, "Task")+"\tAgent\tTests\tLint\tDiff %\tLLM 1-5")
			fmt.Fprintln(w, strings.Repeat("-", 20)+"\t"+strings.Repeat("-", 10)+"\t-----\t----\t------\t-------")
			for _, sc := range scores {
				tests := "—"
				if sc.TestsPass != nil {
					if *sc.TestsPass {
						tests = color(green, "✓")
					} else {
						tests = color(red, "✗")
					}
				}
				lint := "—"
				if sc.LintClean != nil {
					if *sc.LintClean {
						lint = color(green, "✓")
					} else {
						lint = color(red, "✗")
					}
				}
				llm := "—"
				if sc.LLMJudgeScore != nil {
					llm = fmt.Sprintf("%d", *sc.LLMJudgeScore)
				}
				fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%.2f\t%s\n",
					color(cyan, sc.TaskID), sc.Agent, tests, lint, sc.DiffSimilarity, llm)
			}
			w.Flush()

			// Save report
			if output != "" {
				data, err := json.MarshalIndent(scores, "", "  ")
				if err != nil {
					return err
				}
				if err := os.WriteFile(output, data, 0o644); err != nil {
					return fmt.Errorf("writing report: %w", err)
				}
				fmt.Printf("\n%sReport saved to %s%s\n", color(green, ""), output, reset)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&runFile, "run", "", "Single run result JSON")
	cmd.Flags().StringVar(&runsDir, "runs", "", "Directory of run results")
	cmd.Flags().StringVar(&output, "output", "", "Save report JSON")
	cmd.Flags().BoolVar(&noLLM, "no-llm", false, "Skip LLM judge")
	cmd.Flags().StringVar(&taskDir, "task-dir", ".agenteval/tasks", "Task definitions directory")
	return cmd
}

// ── agenteval version ──────────────────────────────────────────────────────

func versionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Show the agenteval version.",
		Run: func(cmd *cobra.Command, args []string) {
			fmt.Printf("agenteval %s\n", version)
		},
	}
}

// ── Helpers ────────────────────────────────────────────────────────────────

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// printBox prints a simple ASCII panel to stdout.
func printBox(title, body string) {
	lines := strings.Split(body, "\n")
	width := len(title) + 4
	for _, l := range lines {
		if len(l)+4 > width {
			width = len(l) + 4
		}
	}
	border := strings.Repeat("─", width-2)
	fmt.Printf("┌%s┐\n", border)
	fmt.Printf("│ %s%s%s%s │\n", bold, title, reset, strings.Repeat(" ", width-4-len(title)))
	fmt.Printf("├%s┤\n", border)
	for _, l := range lines {
		pad := width - 4 - len(l)
		if pad < 0 {
			pad = 0
		}
		fmt.Printf("│ %s%s │\n", l, strings.Repeat(" ", pad))
	}
	fmt.Printf("└%s┘\n", border)
}
