// Package llm — OpenCode local-server bridge (hy4-flash-free etc.)
// Developer by Bittu Chauhan
//
// Free models such as hy4-flash-free can only be reached through the OpenCode
// harness. This file talks to the OpenCode desktop service (a local HTTP API)
// so the CLI can use them without a paid Console API key.
package llm

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ocDo performs an authenticated request against the local OpenCode service.
// Auth is HTTP Basic with username "opencode" and the password from
// ~/.config/opencode/service.json (stored in cfg.Providers.OpenCode.APIKey).
func (c *Client) ocDo(ctx context.Context, method, path string, payload any) ([]byte, error) {
	var rdr io.Reader
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(c.BaseURL, "/")+path, rdr)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth("opencode", c.APIKey)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("OpenCode API %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return body, nil
}

// ocPrompt flattens the harness messages into one prompt for the OpenCode
// session. Each role is labelled so the model keeps the conversation shape.
func ocPrompt(msgs []Message) string {
	var sb strings.Builder
	for _, m := range msgs {
		label := m.Role
		if label == "" {
			label = "user"
		}
		label = strings.ToUpper(label[:1]) + label[1:]
		sb.WriteString(label + ": " + m.Content + "\n\n")
	}
	return strings.TrimSpace(sb.String())
}

type ocAssistantMsg struct {
	Type    string `json:"type"`
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	Error json.RawMessage `json:"error"`
	Tokens *struct {
		Input  int `json:"input"`
		Output int `json:"output"`
		Cache  struct {
			Read  int `json:"read"`
			Write int `json:"write"`
		} `json:"cache"`
	} `json:"tokens"`
}

// bridgeChat creates a fresh OpenCode session, switches it to the requested
// model, sends the prompt, and waits for the assistant text.
func (c *Client) bridgeChat(ctx context.Context, msgs []Message) (string, *Usage, error) {
	start := time.Now()
	c.LastStats = &Stats{StartedAt: start, Provider: c.Provider, Model: c.Model}

	var created struct {
		Data struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	b, err := c.ocDo(ctx, http.MethodPost, "/api/session", map[string]any{})
	if err != nil {
		return "", nil, err
	}
	if err := json.Unmarshal(b, &created); err != nil || created.Data.ID == "" {
		return "", nil, fmt.Errorf("could not create OpenCode session: %s", strings.TrimSpace(string(b)))
	}
	id := created.Data.ID
	// Best-effort cleanup so sessions do not pile up.
	defer func() {
		cleanCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_, _ = c.ocDo(cleanCtx, http.MethodDelete, "/api/session/"+id, nil)
	}()

	modelRef := map[string]any{
		"model": map[string]string{"id": c.Model, "providerID": "opencode"},
	}
	if _, err := c.ocDo(ctx, http.MethodPost, "/api/session/"+id+"/model", modelRef); err != nil {
		return "", nil, err
	}
	if _, err := c.ocDo(ctx, http.MethodPost, "/api/session/"+id+"/prompt", map[string]any{"text": ocPrompt(msgs)}); err != nil {
		return "", nil, err
	}

	deadline := time.Now().Add(c.Timeout)
	for time.Now().Before(deadline) {
		b, err := c.ocDo(ctx, http.MethodGet, "/api/session/"+id+"/message", nil)
		if err != nil {
			return "", nil, err
		}
		var list struct {
			Data []ocAssistantMsg `json:"data"`
		}
		if err := json.Unmarshal(b, &list); err != nil {
			return "", nil, err
		}
		for _, m := range list.Data {
			if m.Type == "error" {
				return "", nil, fmt.Errorf("OpenCode: %s", string(m.Error))
			}
			if m.Type != "assistant" {
				continue
			}
			var sb strings.Builder
			for _, p := range m.Content {
				if p.Type == "text" {
					sb.WriteString(p.Text)
				}
			}
			if sb.Len() == 0 {
				continue // reasoning-only partial message
			}
			u := &Usage{}
			if m.Tokens != nil {
				u.PromptTokens = m.Tokens.Input
				u.CompletionTokens = m.Tokens.Output
				u.TotalTokens = m.Tokens.Input + m.Tokens.Output
			}
			c.LastStats.Usage = u
			c.LastStats.Chunks = 1
			c.LastStats.Chars = sb.Len()
			c.LastStats.Duration = time.Since(start)
			c.LastStats.TokensPerSec = float64(sb.Len()) / c.LastStats.Duration.Seconds() / 4.0
			return sb.String(), u, nil
		}
		time.Sleep(300 * time.Millisecond)
	}
	return "", nil, fmt.Errorf("timed out waiting for OpenCode response")
}

// bridgeStream reuses bridgeChat and streams the result back in small chunks
// so the TUI still animates the answer.
func (c *Client) bridgeStream(ctx context.Context, msgs []Message, out chan<- string, done chan<- error) {
	text, usage, err := c.bridgeChat(ctx, msgs)
	if err != nil {
		done <- err
		return
	}
	_ = usage
	for len(text) > 0 {
		n := 24
		if n > len(text) {
			n = len(text)
		}
		out <- text[:n]
		text = text[n:]
	}
	done <- nil
}

// bridgeModels lists the models exposed by the local OpenCode service.
func (c *Client) bridgeModels(ctx context.Context) ([]string, error) {
	b, err := c.ocDo(ctx, http.MethodGet, "/api/model", nil)
	if err != nil {
		return nil, err
	}
	var list struct {
		Data []struct {
			ID         string `json:"id"`
			ProviderID string `json:"providerID"`
		} `json:"data"`
	}
	if err := json.Unmarshal(b, &list); err != nil {
		return nil, err
	}
	var out []string
	for _, m := range list.Data {
		if m.ProviderID == "opencode" {
			out = append(out, m.ID)
		}
	}
	return out, nil
}
