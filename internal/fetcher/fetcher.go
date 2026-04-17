// Package fetcher retrieves merged PRs from GitHub and filters them for eval quality.
package fetcher

import (
	"context"
	"fmt"
	"net/http"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"github.com/google/go-github/v62/github"
	"golang.org/x/oauth2"

	"agenteval/internal/models"
)

var excludeTitleRe = regexp.MustCompile(`(?i)\b(bump|deps|chore|merge)\b`)

// PRFetcher fetches merged PRs from a GitHub (or GHE) repository.
type PRFetcher struct {
	client *github.Client
}

// New creates a PRFetcher. token may be empty (unauthenticated). baseURL is the
// GHE API root (e.g. "https://ghe.example.com/api/v3"); leave empty for github.com.
func New(token, baseURL string) (*PRFetcher, error) {
	var hc *http.Client
	if token != "" {
		ts := oauth2.StaticTokenSource(&oauth2.Token{AccessToken: token})
		hc = oauth2.NewClient(context.Background(), ts)
	}

	var client *github.Client
	if baseURL != "" {
		// Ensure trailing slash required by go-github.
		u := strings.TrimRight(baseURL, "/") + "/"
		var err error
		client, err = github.NewClient(hc).WithEnterpriseURLs(u, u)
		if err != nil {
			return nil, fmt.Errorf("configuring GitHub Enterprise URLs: %w", err)
		}
	} else {
		client = github.NewClient(hc)
	}
	return &PRFetcher{client: client}, nil
}

// DetectGitHubBaseURL reads the git remote for repoPath and returns the GHE API
// base URL (e.g. "https://ghe.example.com/api/v3"), or "" for github.com.
func DetectGitHubBaseURL(repoPath string) string {
	out, err := exec.Command("git", "-C", repoPath, "remote", "get-url", "origin").Output()
	if err != nil {
		return ""
	}
	url := strings.TrimSpace(string(out))

	// SSH:   git@host:owner/repo.git
	if m := regexp.MustCompile(`git@([^:]+):`).FindStringSubmatch(url); m != nil {
		if m[1] != "github.com" {
			return "https://" + m[1] + "/api/v3"
		}
	}
	// HTTPS: https://host/owner/repo.git
	if m := regexp.MustCompile(`https?://([^/]+)/`).FindStringSubmatch(url); m != nil {
		if m[1] != "github.com" {
			return "https://" + m[1] + "/api/v3"
		}
	}
	return ""
}

// GetRepoFromRemote returns "owner/repo" extracted from the origin remote of repoPath.
func GetRepoFromRemote(repoPath string) (string, error) {
	out, err := exec.Command("git", "-C", repoPath, "remote", "get-url", "origin").Output()
	if err != nil {
		return "", fmt.Errorf("git remote get-url origin: %w", err)
	}
	url := strings.TrimSpace(string(out))
	m := regexp.MustCompile(`[:/]([^/]+/[^/]+?)(?:\.git)?$`).FindStringSubmatch(url)
	if m == nil {
		return "", fmt.Errorf("cannot extract owner/repo from remote URL: %s", url)
	}
	return m[1], nil
}

// FetchPRs returns merged PRs in [since, until) filtered for eval quality.
// owner and repo are separate strings (e.g. "google", "go").
func (f *PRFetcher) FetchPRs(ctx context.Context, owner, repo string, since, until time.Time) ([]*models.PRData, error) {
	opts := &github.PullRequestListOptions{
		State:     "closed",
		Sort:      "updated",
		Direction: "desc",
		ListOptions: github.ListOptions{PerPage: 100},
	}

	var results []*models.PRData
	filtered := 0

	for {
		prs, resp, err := f.client.PullRequests.List(ctx, owner, repo, opts)
		if err != nil {
			ghErr, ok := err.(*github.ErrorResponse)
			if ok {
				switch ghErr.Response.StatusCode {
				case 401:
					return nil, fmt.Errorf("GitHub API 401 Unauthorized — check your token")
				case 403:
					return nil, fmt.Errorf("GitHub API 403 Forbidden — is your GITHUB_TOKEN valid?")
				case 404:
					return nil, fmt.Errorf("repository %s/%s not found — check name and token permissions", owner, repo)
				}
			}
			return nil, fmt.Errorf("listing PRs: %w", err)
		}

		done := false
		for _, pr := range prs {
			if pr.MergedAt == nil {
				continue
			}
			mergedAt := pr.MergedAt.Time.UTC()
			if mergedAt.Before(since) {
				done = true
				break
			}
			if mergedAt.After(until) {
				continue
			}
			if !isGoodCandidate(pr) {
				filtered++
				continue
			}

			files, _, err := f.client.PullRequests.ListFiles(ctx, owner, repo, pr.GetNumber(), nil)
			if err != nil {
				return nil, fmt.Errorf("listing files for PR #%d: %w", pr.GetNumber(), err)
			}
			fileNames := make([]string, len(files))
			for i, f := range files {
				fileNames[i] = f.GetFilename()
			}

			t := mergedAt // copy for pointer
			results = append(results, &models.PRData{
				Number:       pr.GetNumber(),
				Title:        pr.GetTitle(),
				Body:         pr.GetBody(),
				Author:       pr.GetUser().GetLogin(),
				MergedAt:     &t,
				BaseSHA:      pr.GetBase().GetSHA(),
				HeadSHA:      pr.GetHead().GetSHA(),
				FilesChanged: fileNames,
				Additions:    pr.GetAdditions(),
				Deletions:    pr.GetDeletions(),
			})
		}

		if done || resp.NextPage == 0 {
			break
		}
		opts.Page = resp.NextPage
	}

	fmt.Printf("Found %d candidate PRs (filtered out %d)\n", len(results), filtered)
	return results, nil
}

func isGoodCandidate(pr *github.PullRequest) bool {
	if strings.TrimSpace(pr.GetBody()) == "" {
		return false
	}
	if pr.GetChangedFiles() >= 10 {
		return false
	}
	if pr.GetAdditions()+pr.GetDeletions() >= 500 {
		return false
	}
	u := pr.GetUser()
	if u.GetType() == "Bot" || strings.HasSuffix(u.GetLogin(), "[bot]") {
		return false
	}
	if excludeTitleRe.MatchString(pr.GetTitle()) {
		return false
	}
	return true
}
