// Package extractor converts PRData into TaskDefinitions and saves diffs to disk.
package extractor

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"agenteval/internal/models"
)

// TaskExtractor converts PRData into TaskDefinitions, fetching diffs via the
// GitHub REST API.
type TaskExtractor struct {
	repo    string // "owner/repo"
	token   string
	apiBase string // e.g. "https://api.github.com" or "https://ghe.example.com/api/v3"
}

// New creates a TaskExtractor. baseURL is the GHE API root or "" for github.com.
func New(token, repo, baseURL string) *TaskExtractor {
	apiBase := strings.TrimRight(baseURL, "/")
	if apiBase == "" {
		apiBase = "https://api.github.com"
	}
	return &TaskExtractor{repo: repo, token: token, apiBase: apiBase}
}

// ExtractTasks converts a slice of PRData into TaskDefinitions, writing each
// diff patch to outputDir/<task-id>.patch.
func (e *TaskExtractor) ExtractTasks(prs []*models.PRData, outputDir string) ([]models.TaskDefinition, error) {
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return nil, fmt.Errorf("creating output dir: %w", err)
	}

	var tasks []models.TaskDefinition
	for _, pr := range prs {
		taskID := fmt.Sprintf("%s-%d", slugify(pr.Title), pr.Number)
		prompt := pr.Title + "\n\n" + pr.Body

		diffFile := filepath.Join(outputDir, taskID+".patch")
		diff, err := e.fetchDiff(pr.Number)
		if err != nil {
			fmt.Printf("  warning: could not fetch diff for PR #%d: %v\n", pr.Number, err)
			diff = ""
		}
		if werr := os.WriteFile(diffFile, []byte(diff), 0o644); werr != nil {
			return nil, fmt.Errorf("writing diff for %s: %w", taskID, werr)
		}

		var mergedStr string
		if pr.MergedAt != nil {
			mergedStr = pr.MergedAt.Format(time.RFC3339)
		}

		tasks = append(tasks, models.TaskDefinition{
			ID:               taskID,
			Prompt:           prompt,
			RepoURL:          "https://github.com/" + e.repo,
			BaseCommit:       pr.BaseSHA,
			ExpectedDiffPath: diffFile,
			TestCommands:     []string{},
			Metadata: map[string]any{
				"pr_number":     pr.Number,
				"author":        pr.Author,
				"merged_at":     mergedStr,
				"files_changed": pr.FilesChanged,
			},
		})
		fmt.Printf("  ✓ %s\n", taskID)
	}

	fmt.Printf("Extracted %d tasks\n", len(tasks))
	return tasks, nil
}

// fetchDiff retrieves the unified diff for a PR via the GitHub REST API.
func (e *TaskExtractor) fetchDiff(prNumber int) (string, error) {
	url := fmt.Sprintf("%s/repos/%s/pulls/%d", e.apiBase, e.repo, prNumber)

	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Accept", "application/vnd.github.v3.diff")
	if e.token != "" {
		req.Header.Set("Authorization", "token "+e.token)
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("HTTP %d from %s", resp.StatusCode, url)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

var nonAlnum = regexp.MustCompile(`[^a-z0-9]+`)

// slugify converts text to a URL-friendly slug, max 60 characters.
func slugify(text string) string {
	s := strings.ToLower(text)
	s = nonAlnum.ReplaceAllString(s, "-")
	s = strings.Trim(s, "-")
	if len(s) > 60 {
		s = s[:60]
		s = strings.TrimRight(s, "-")
	}
	return s
}
