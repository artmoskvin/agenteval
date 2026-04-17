// Package prompt provides LLM-based cleanup of raw PR prompts.
package prompt

import (
	"context"
	"fmt"

	"github.com/anthropics/anthropic-sdk-go"
)

const systemPrompt = `You are a prompt engineer. Given a raw coding task description extracted from a GitHub PR (title + body), convert it into a clear, actionable coding task prompt.

Rules:
- Remove references to specific people, internal links, JIRA/Linear tickets, etc.
- Remove PR boilerplate (checkboxes, template text)
- Preserve the technical intent and requirements
- Write as a direct instruction to a developer
- Output ONLY the cleaned prompt, nothing else — no preamble, no explanation`

const model = "claude-sonnet-4-20250514"

// Cleaner cleans raw PR prompts using Claude. When NoLLM is true all methods
// return input unchanged (no API calls).
type Cleaner struct {
	NoLLM  bool
	client *anthropic.Client
}

// New creates a Cleaner. If noLLM is true, no Anthropic client is created.
func New(noLLM bool) *Cleaner {
	c := &Cleaner{NoLLM: noLLM}
	if !noLLM {
		c.client = anthropic.NewClient()
	}
	return c
}

// Cleanup returns a cleaned version of rawPrompt. On API failure it falls back
// to rawPrompt after one retry.
func (c *Cleaner) Cleanup(rawPrompt string) string {
	if c.NoLLM {
		return rawPrompt
	}

	for attempt := 0; attempt < 2; attempt++ {
		cleaned, err := c.callAPI(rawPrompt)
		if err == nil {
			return cleaned
		}
		if attempt == 0 {
			fmt.Printf("  LLM API error, retrying: %v\n", err)
		} else {
			fmt.Printf("  LLM API failed, using raw prompt: %v\n", err)
		}
	}
	return rawPrompt
}

// CleanupBatch cleans a slice of prompts sequentially.
func (c *Cleaner) CleanupBatch(prompts []string) []string {
	out := make([]string, len(prompts))
	for i, p := range prompts {
		out[i] = c.Cleanup(p)
	}
	return out
}

func (c *Cleaner) callAPI(userContent string) (string, error) {
	msg, err := c.client.Messages.New(context.Background(), anthropic.MessageNewParams{
		Model:     anthropic.F(anthropic.Model(model)),
		MaxTokens: anthropic.F(int64(1024)),
		System: anthropic.F([]anthropic.TextBlockParam{
			{Text: anthropic.F(systemPrompt), Type: anthropic.F(anthropic.TextBlockParamTypeText)},
		}),
		Messages: anthropic.F([]anthropic.MessageParam{
			anthropic.NewUserMessage(anthropic.NewTextBlock(userContent)),
		}),
	})
	if err != nil {
		return "", err
	}
	for _, block := range msg.Content {
		if block.Type == anthropic.ContentBlockTypeText {
			return block.Text, nil
		}
	}
	return "", fmt.Errorf("no text block in response")
}
