// BITTU AGENT — Next Gen Terminal AI Harness (v2 ULTRA)
// Developer by Bittu Chauhan
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"

	"bittu-agent/internal/config"
	"bittu-agent/internal/llm"
	"bittu-agent/internal/tui"

	tea "github.com/charmbracelet/bubbletea"
)

func main() {
	configPath := flag.String("config", "config.toml", "path to config.toml")
	model := flag.String("model", "", "override model")
	themeFlag := flag.String("theme", "", "override theme")
	provider := flag.String("provider", "", "provider: sambanova|groq|openai|openrouter|opencode")
	once := flag.String("once", "", "one-shot mode: ask once and exit (e.g. --once \"hello\")")
	modeFlag := flag.String("mode", "", "agent mode: build|plan|ask|research|debug|review")
	version := flag.Bool("version", false, "print version")
	flag.Parse()

	if *version {
		fmt.Println("BITTU AGENT ULTRA 2.0.0 — Developer by Bittu Chauhan")
		return
	}

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	if *model != "" {
		cfg.Provider.SambaNova.Model = *model
		cfg.Providers.SambaNova.Model = *model
		cfg.Provider.OpenCode.Model = *model
		cfg.Providers.OpenCode.Model = *model
		cfg.Models.Default = *model
	}
	if *themeFlag != "" {
		cfg.TUI.Theme = *themeFlag
	}
	if *provider != "" {
		cfg.Providers.Active = strings.ToLower(strings.TrimSpace(*provider))
	}
	if *modeFlag != "" {
		cfg.Agent.Mode = *modeFlag
	}

	// ── One-shot (pipe / CI) ──
	if *once != "" || flag.NArg() > 0 {
		prompt := *once
		if prompt == "" {
			prompt = strings.Join(flag.Args(), " ")
		}
		fmt.Println("✦ BITTU AGENT ULTRA — one-shot")
		client := llm.NewFor(cfg, cfg.Providers.Active, cfg.Model())
		var msgs []llm.Message
		msgs = append(msgs, llm.Message{Role: "system", Content: cfg.Agent.SystemPrompt})
		msgs = append(msgs, llm.Message{Role: "user", Content: prompt})
		out, _, err := client.Chat(context.Background(), msgs)
		if err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		fmt.Println(out)
		fmt.Println("\n— Developer by Bittu Chauhan —")
		return
	}

	m := tui.New(cfg)
	p := tea.NewProgram(m, tea.WithAltScreen(), tea.WithMouseCellMotion())
	if _, err := p.Run(); err != nil {
		fmt.Printf("BITTU AGENT error: %v\n", err)
		os.Exit(1)
	}
	fmt.Println()
	fmt.Println("  ✦ BITTU AGENT ULTRA — session ended. Developer by Bittu Chauhan.")
}
