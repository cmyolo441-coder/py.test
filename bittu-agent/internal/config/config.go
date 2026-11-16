// Package config — BITTU AGENT config loader (v2 ultra)
// Developer by Bittu Chauhan
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

type Developer struct {
	Name    string `toml:"name"`
	Title   string `toml:"title"`
	Version string `toml:"version"`
	Repo    string `toml:"repo"`
}

type AgentCfg struct {
	Name         string `toml:"name"`
	Style        string `toml:"style"`
	SystemPrompt string `toml:"system_prompt"`
	Mode         string `toml:"mode"`
	Persona      string `toml:"persona"`
	Temperature  float64 `toml:"temperature"`
}

type Provider struct {
	Name             string  `toml:"name"`
	BaseURL          string  `toml:"base_url"`
	APIKey           string  `toml:"api_key"`
	APIKeyEnv        string  `toml:"api_key_env"`
	Model            string  `toml:"model"`
	FallbackModel    string  `toml:"fallback_model"`
	MaxContextWindow int     `toml:"max_context_window"`
	MaxOutputTokens  int     `toml:"max_output_tokens"`
	ContextWindow    int     `toml:"context_window"`
	MaxTokens        int     `toml:"max_tokens"`
	Temperature      float64 `toml:"temperature"`
	TopP             float64 `toml:"top_p"`
	Timeout          int     `toml:"timeout"`
}

type Providers struct {
	Active    string   `toml:"active"`
	SambaNova Provider `toml:"sambanova"`
	Groq      Provider `toml:"groq"`
	OpenAI    Provider `toml:"openai"`
	OpenRouter Provider `toml:"openrouter"`
	Gemini    Provider `toml:"gemini"`
	OpenCode  Provider `toml:"opencode"`
}

type Models struct {
	Default   string   `toml:"default"`
	Available []string `toml:"available"`
}

type TUI struct {
	Theme              string `toml:"theme"`
	Animations         bool   `toml:"animations"`
	Streaming          bool   `toml:"streaming"`
	Spinner            string `toml:"spinner"`
	PromptDoubleBorder bool   `toml:"prompt_double_border"`
	ShowTokens         bool   `toml:"show_tokens"`
	ShowContext        bool   `toml:"show_context"`
	Welcome            string `toml:"welcome"`
	DeveloperCredit    string `toml:"developer_credit"`
	ShowSidebar        bool   `toml:"show_sidebar"`
	ShowFPS            bool   `toml:"show_fps"`
	ShowGit            bool   `toml:"show_git"`
	SidebarWidth       int    `toml:"sidebar_width"`
	Density            string `toml:"density"`
	CommandPalette     bool   `toml:"command_palette"`
	Approval           bool   `toml:"approval"`
	AutoCompact        bool   `toml:"auto_compact"`
	CompactThreshold   float64 `toml:"compact_threshold"`
}

type ToolsCfg struct {
	Enabled               bool `toml:"enabled"`
	Shell                 bool `toml:"shell"`
	ReadFile              bool `toml:"read_file"`
	WriteFile             bool `toml:"write_file"`
	ListDir               bool `toml:"list_dir"`
	Grep                  bool `toml:"grep"`
	WebFetch              bool `toml:"web_fetch"`
	GitStatus             bool `toml:"git_status"`
	GitDiff               bool `toml:"git_diff"`
	GoFmt                 bool `toml:"go_fmt"`
	GoTest                bool `toml:"go_test"`
	EditFile              bool `toml:"edit_file"`
	AllowDestructiveShell bool `toml:"allow_destructive_shell"`
	MaxParallel           int  `toml:"max_parallel"`
}

type Sessions struct {
	Dir         string `toml:"dir"`
	AutoSave    bool   `toml:"auto_save"`
	AutoLoad    bool   `toml:"auto_load"`
	MaxHistory  int    `toml:"max_history"`
}

type Keys struct {
	Quit       string `toml:"quit"`
	Send       string `toml:"send"`
	Newline    string `toml:"newline"`
	Clear      string `toml:"clear"`
	Help       string `toml:"help"`
	Model      string `toml:"model"`
	Theme      string `toml:"theme"`
	Palette    string `toml:"palette"`
	Files      string `toml:"files"`
	Mode       string `toml:"mode"`
	Sidebar    string `toml:"sidebar"`
	Save       string `toml:"save"`
	Sessions   string `toml:"sessions"`
	Reasoning  string `toml:"reasoning"`
	Compact    string `toml:"compact"`
}

type CustomCmd struct {
	Name    string `toml:"name"`
	Prompt  string `toml:"prompt"`
	Desc    string `toml:"desc"`
}

type Config struct {
	Developer Developer  `toml:"developer"`
	Agent     AgentCfg   `toml:"agent"`
	Provider  Providers  `toml:"provider"`
	Providers Providers  `toml:"providers"`
	Models    Models     `toml:"models"`
	TUI       TUI        `toml:"tui"`
	Tools     ToolsCfg   `toml:"tools"`
	Keys      Keys       `toml:"keys"`
	Sessions  Sessions   `toml:"sessions"`
	Commands  []CustomCmd `toml:"commands"`
}

func Default() *Config {
	return &Config{
		Developer: Developer{Name: "Bittu Chauhan", Title: "Developer by Bittu Chauhan", Version: "2.0.0-ultra"},
		Agent: AgentCfg{
			Name: "BITTU AGENT", Style: "next-gen-ultra", Mode: "build", Persona: "senior engineer",
			SystemPrompt: "You are BITTU AGENT built by Bittu Chauhan. You are an ultra-advanced terminal AI harness. Be powerful, direct, beautiful, smooth and very advanced. Use tools when needed. Format with markdown.",
			Temperature: 0.7,
		},
		Provider: Providers{Active: "sambanova", SambaNova: Provider{
			Name: "SambaNova", BaseURL: "https://api.sambanova.ai/v1",
			Model: "MiniMax-M3.1-Flash", MaxContextWindow: 1048576,
			MaxOutputTokens: 16384, ContextWindow: 1048576, MaxTokens: 16384, Temperature: 0.7, Timeout: 120,
		}, Groq: Provider{Name: "Groq", BaseURL: "https://api.groq.com/openai/v1", Model: "llama-3.3-70b-versatile", ContextWindow: 128000, MaxTokens: 8192, Temperature: 0.7, Timeout: 60},
			OpenAI: Provider{Name: "OpenAI", BaseURL: "https://api.openai.com/v1", Model: "gpt-4o", ContextWindow: 128000, MaxTokens: 16384, Temperature: 0.7, Timeout: 60},
			OpenRouter: Provider{Name: "OpenRouter", BaseURL: "https://openrouter.ai/api/v1", Model: "openai/gpt-4o", ContextWindow: 128000, MaxTokens: 16384, Temperature: 0.7, Timeout: 60},
			OpenCode: Provider{Name: "OpenCode", BaseURL: "http://127.0.0.1:28894", Model: "hy4-flash-free", ContextWindow: 1048576, MaxTokens: 65536, Temperature: 0.7, Timeout: 180},
		},
		Models: Models{Default: "MiniMax-M3.1-Flash", Available: []string{"MiniMax-M3.1-Flash", "Kiwi-V3.1", "Llama-4-Maverick-17B-128E-Instruct", "meta-llama/Llama-3.1-8B-Instruct", "llama-3.3-70b-versatile", "gpt-4o"}},
		TUI: TUI{Theme: "devin", Animations: true, Streaming: true, PromptDoubleBorder: true, DeveloperCredit: "Developer by Bittu Chauhan",
			ShowSidebar: false, ShowFPS: true, ShowGit: true, SidebarWidth: 34, Density: "comfortable", CommandPalette: true, Approval: true, AutoCompact: true, CompactThreshold: 0.8},
		Tools: ToolsCfg{Enabled: true, Shell: true, ReadFile: true, WriteFile: true, ListDir: true, Grep: true, WebFetch: true, GitStatus: true, GitDiff: true, GoFmt: true, GoTest: true, MaxParallel: 4},
		Sessions: Sessions{AutoSave: true, MaxHistory: 200},
	}
}

func Load(path string) (*Config, error) {
	cfg := Default()
	if path == "" {
		candidates := []string{"config.toml", filepath.Join(os.Getenv("HOME"), ".bittu-agent", "config.toml")}
		for _, c := range candidates {
			if _, err := os.Stat(c); err == nil {
				path = c
				break
			}
		}
	}
	if path == "" {
		return cfg, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}
	var raw map[string]any
	if err := toml.Unmarshal(data, &raw); err != nil {
		return cfg, fmt.Errorf("toml parse: %w", err)
	}
	_ = toml.Unmarshal(data, cfg)
	// Normalize dual [provider.] vs [providers.]
	if cfg.Provider.SambaNova.BaseURL != "" {
		cfg.Providers.SambaNova = cfg.Provider.SambaNova
		// Only fall back to the legacy [provider] active value when
		// [providers] did not explicitly choose one.
		if cfg.Provider.Active != "" && cfg.Providers.Active == "" {
			cfg.Providers.Active = cfg.Provider.Active
		}
	}
	if cfg.Provider.OpenCode.BaseURL != "" {
		cfg.Providers.OpenCode = cfg.Provider.OpenCode
	}
	// env var overrides
	applyEnv(&cfg.Providers.SambaNova, "SAMBANOVA_API_KEY")
	applyEnv(&cfg.Providers.Groq, "GROQ_API_KEY")
	applyEnv(&cfg.Providers.OpenAI, "OPENAI_API_KEY")
	applyEnv(&cfg.Providers.OpenRouter, "OPENROUTER_API_KEY")
	applyEnv(&cfg.Providers.Gemini, "GEMINI_API_KEY")
	// OpenCode bridge: reuse the desktop service password automatically.
	if cfg.Providers.OpenCode.APIKey == "" {
		cfg.Providers.OpenCode.APIKey = openCodeServicePassword()
	}
	applyEnv(&cfg.Providers.OpenCode, "OPENCODE_API_KEY")
	if cfg.Providers.OpenCode.BaseURL == "" {
		cfg.Providers.OpenCode.BaseURL = "http://127.0.0.1:28894"
	}
	if cfg.Providers.OpenCode.Model == "" {
		cfg.Providers.OpenCode.Model = "hy4-flash-free"
	}
	if cfg.Providers.SambaNova.Model == "" {
		cfg.Providers.SambaNova.Model = cfg.Models.Default
	}
	if cfg.Providers.SambaNova.MaxTokens == 0 {
		cfg.Providers.SambaNova.MaxTokens = 16384
	}
	if cfg.Providers.SambaNova.ContextWindow == 0 {
		cfg.Providers.SambaNova.ContextWindow = 1048576
	}
	// mirror to Provider for backward compat
	cfg.Provider = cfg.Providers
	return cfg, nil
}

// openCodeServicePassword reads the Basic-auth password of the running
// OpenCode desktop service so no secret has to be pasted into config.toml.
func openCodeServicePassword() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	b, err := os.ReadFile(filepath.Join(home, ".config", "opencode", "service.json"))
	if err != nil {
		return ""
	}
	var v struct {
		Password string `json:"password"`
	}
	if json.Unmarshal(b, &v) != nil {
		return ""
	}
	return v.Password
}

func applyEnv(p *Provider, env string) {
	if p.APIKey == "" && env != "" {
		if v := os.Getenv(env); v != "" {
			p.APIKey = v
		}
	}
	if p.APIKeyEnv != "" {
		if v := os.Getenv(p.APIKeyEnv); v != "" {
			p.APIKey = v
		}
	}
}

func (c *Config) ProviderByName(name string) (Provider, bool) {
	name = strings.ToLower(strings.TrimSpace(name))
	switch name {
	case "sambanova", "samba":
		return c.Providers.SambaNova, c.Providers.SambaNova.BaseURL != ""
	case "groq":
		return c.Providers.Groq, c.Providers.Groq.BaseURL != ""
	case "openai":
		return c.Providers.OpenAI, c.Providers.OpenAI.BaseURL != ""
	case "openrouter", "or":
		return c.Providers.OpenRouter, c.Providers.OpenRouter.BaseURL != ""
	case "gemini":
		return c.Providers.Gemini, c.Providers.Gemini.BaseURL != ""
	case "opencode", "oc", "hy4":
		return c.Providers.OpenCode, c.Providers.OpenCode.BaseURL != ""
	}
	return Provider{}, false
}

func (c *Config) ActiveProvider() Provider {
	if p, ok := c.ProviderByName(c.Providers.Active); ok {
		return p
	}
	return c.Providers.SambaNova
}

func (c *Config) ProviderConfig() Provider { return c.ActiveProvider() }
func (c *Config) BaseURL() string { return c.ActiveProvider().BaseURL }
func (c *Config) APIKey() string { return c.ActiveProvider().APIKey }
func (c *Config) Model() string {
	if m := c.ActiveProvider().Model; m != "" {
		return m
	}
	return c.Models.Default
}
func (c *Config) ContextWindow() int {
	if c.ActiveProvider().ContextWindow > 0 {
		return c.ActiveProvider().ContextWindow
	}
	if c.ActiveProvider().MaxContextWindow > 0 {
		return c.ActiveProvider().MaxContextWindow
	}
	return 1048576
}
func (c *Config) MaxTokens() int {
	if c.ActiveProvider().MaxTokens > 0 {
		return c.ActiveProvider().MaxTokens
	}
	if c.ActiveProvider().MaxOutputTokens > 0 {
		return c.ActiveProvider().MaxOutputTokens
	}
	return 16384
}
func (c *Config) ProviderNames() []string { return []string{"sambanova", "groq", "openai", "openrouter", "opencode"} }
