// Package llm — multi-provider streaming client (v2 ultra)
// Developer by Bittu Chauhan
package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"bittu-agent/internal/config"
)

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type Stats struct {
	Chunks      int
	Chars       int
	StartedAt   time.Time
	FirstToken  *time.Duration
	Duration    time.Duration
	Usage       *Usage
	TokensPerSec float64
	Provider    string
	Model       string
}

type Client struct {
	BaseURL   string
	APIKey    string
	Model     string
	MaxTokens int
	Temp      float64
	Timeout   time.Duration
	HTTP      *http.Client
	Provider  string
	Kind      string // "openai" (default) or "opencode" (local bridge)
	LastStats *Stats
}

func New(cfg *config.Config) *Client {
	p := cfg.ActiveProvider()
	to := time.Duration(p.Timeout) * time.Second
	if to == 0 {
		to = 120 * time.Second
	}
	return &Client{
		BaseURL: strings.TrimRight(p.BaseURL, "/"), APIKey: p.APIKey,
		Model: p.Model, MaxTokens: cfg.MaxTokens(), Temp: p.Temperature,
		Timeout: to, HTTP: &http.Client{Timeout: to},
		Provider: cfg.Providers.Active, Kind: providerKind(cfg.Providers.Active),
		LastStats: &Stats{},
	}
}

func providerKind(name string) string {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "opencode", "oc", "hy4":
		return "opencode"
	default:
		return "openai"
	}
}

func NewFor(cfg *config.Config, provider, model string) *Client {
	c := New(cfg)
	if provider != "" {
		if p, ok := cfg.ProviderByName(provider); ok {
			c.BaseURL = strings.TrimRight(p.BaseURL, "/")
			c.APIKey = p.APIKey
			c.Provider = strings.ToLower(provider)
			c.Kind = providerKind(provider)
			if p.Temperature != 0 {
				c.Temp = p.Temperature
			}
		}
	}
	if model != "" {
		c.Model = model
	}
	// refresh defaults
	if cfg.MaxTokens() > 0 {
		c.MaxTokens = cfg.MaxTokens()
	}
	return c
}

type chatReq struct {
	Model     string    `json:"model"`
	Messages  []Message `json:"messages"`
	MaxTokens int       `json:"max_tokens"`
	Stream    bool      `json:"stream"`
	Temp      float64   `json:"temperature,omitempty"`
	TopP      float64   `json:"top_p,omitempty"`
}

type streamChunk struct {
	Choices []struct {
		Delta struct {
			Content string `json:"content"`
			Role    string `json:"role"`
		} `json:"delta"`
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
	Usage *Usage `json:"usage"`
}

func (c *Client) Stream(ctx context.Context, msgs []Message, out chan<- string, done chan<- error) {
	defer close(out)
	defer close(done)
	if c.Kind == "opencode" {
		c.bridgeStream(ctx, msgs, out, done)
		return
	}
	start := time.Now()
	c.LastStats = &Stats{StartedAt: start, Provider: c.Provider, Model: c.Model}
	if c.APIKey == "" {
		done <- fmt.Errorf("API key missing — set %s in config.toml or env (provider: %s)", c.keyEnv(), c.Provider)
		c.LastStats.Duration = time.Since(start)
		return
	}
	payload, err := json.Marshal(chatReq{Model: c.Model, Messages: msgs, MaxTokens: c.MaxTokens, Stream: true, Temp: c.Temp})
	if err != nil {
		done <- err
		return
	}
	req, err := http.NewRequestWithContext(ctx, "POST", c.BaseURL+"/chat/completions", bytes.NewReader(payload))
	if err != nil {
		done <- err
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	req.Header.Set("X-Title", "BITTU AGENT")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		done <- fmt.Errorf("connection failed: %w", err)
		c.LastStats.Duration = time.Since(start)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 8192))
		done <- fmt.Errorf("API error %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
		c.LastStats.Duration = time.Since(start)
		return
	}
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, ":") {
			continue
		}
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if data == "[DONE]" {
			break
		}
		var ch streamChunk
		if err := json.Unmarshal([]byte(data), &ch); err != nil {
			continue
		}
		if ch.Usage != nil {
			c.LastStats.Usage = ch.Usage
		}
		for _, choice := range ch.Choices {
			var tok string
			if choice.Delta.Content != "" {
				tok = choice.Delta.Content
			} else if choice.Message.Content != "" {
				tok = choice.Message.Content
			}
			if tok != "" {
				c.LastStats.Chunks++
				c.LastStats.Chars += len(tok)
				if c.LastStats.FirstToken == nil {
					d := time.Since(start)
					c.LastStats.FirstToken = &d
				}
				out <- tok
			}
		}
	}
	c.LastStats.Duration = time.Since(start)
	if c.LastStats.Duration.Seconds() > 0 && c.LastStats.Chars > 0 {
		c.LastStats.TokensPerSec = float64(c.LastStats.Chars) / c.LastStats.Duration.Seconds() / 4.0
	}
	if err := sc.Err(); err != nil {
		done <- err
		return
	}
	done <- nil
}

func (c *Client) keyEnv() string {
	switch strings.ToLower(c.Provider) {
	case "groq":
		return "GROQ_API_KEY"
	case "openai":
		return "OPENAI_API_KEY"
	case "openrouter":
		return "OPENROUTER_API_KEY"
	case "gemini":
		return "GEMINI_API_KEY"
	case "opencode", "oc", "hy4":
		return "OPENCODE_API_KEY"
	default:
		return "SAMBANOVA_API_KEY"
	}
}

func (c *Client) Chat(ctx context.Context, msgs []Message) (string, *Usage, error) {
	if c.Kind == "opencode" {
		return c.bridgeChat(ctx, msgs)
	}
	payload, _ := json.Marshal(chatReq{Model: c.Model, Messages: msgs, MaxTokens: c.MaxTokens, Stream: false, Temp: c.Temp})
	req, err := http.NewRequestWithContext(ctx, "POST", c.BaseURL+"/chat/completions", bytes.NewReader(payload))
	if err != nil {
		return "", nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return "", nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return "", nil, fmt.Errorf("API %d: %s", resp.StatusCode, string(b))
	}
	var v struct {
		Choices []struct {
			Message Message `json:"message"`
		} `json:"choices"`
		Usage *Usage `json:"usage"`
	}
	if err := json.Unmarshal(b, &v); err != nil {
		return "", nil, err
	}
	if len(v.Choices) == 0 {
		return "", nil, fmt.Errorf("empty response")
	}
	return v.Choices[0].Message.Content, v.Usage, nil
}

func (c *Client) Ping() error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, _, err := c.Chat(ctx, []Message{{Role: "user", Content: "ping"}})
	return err
}

func (c *Client) Models(ctx context.Context) ([]string, error) {
	if c.Kind == "opencode" {
		return c.bridgeModels(ctx)
	}
	req, err := http.NewRequestWithContext(ctx, "GET", c.BaseURL+"/models", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("API %d: %s", resp.StatusCode, string(b))
	}
	var v struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(b, &v); err != nil {
		return nil, err
	}
	var out []string
	for _, d := range v.Data {
		out = append(out, d.ID)
	}
	return out, nil
}
