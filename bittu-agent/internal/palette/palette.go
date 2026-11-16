// Package palette — BITTU AGENT command palette + fuzzy file finder
// Developer by Bittu Chauhan
package palette

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Item is a palette entry.
type Item struct {
	Title    string
	Hint     string
	Value    string
	Category string
	Score    float64
}

// Commands returns all slash commands + actions.
func Commands() []Item {
	return []Item{
		{Title: "/help", Hint: "show help", Value: "/help", Category: "cmd"},
		{Title: "/clear", Hint: "clear chat", Value: "/clear", Category: "cmd"},
		{Title: "/model", Hint: "switch model", Value: "/model", Category: "cmd"},
		{Title: "/theme neon|matrix|sunset|ocean|midnight", Hint: "change theme", Value: "/theme", Category: "cmd"},
		{Title: "/tools", Hint: "list tools", Value: "/tools", Category: "cmd"},
		{Title: "/mode build|plan|ask|research|debug|review", Hint: "switch agent mode", Value: "/mode", Category: "cmd"},
		{Title: "/sessions", Hint: "list saved sessions", Value: "/sessions", Category: "cmd"},
		{Title: "/save [title]", Hint: "save session", Value: "/save", Category: "cmd"},
		{Title: "/resume <id>", Hint: "restore session", Value: "/resume", Category: "cmd"},
		{Title: "/export <id> [file.md]", Hint: "export to markdown", Value: "/export", Category: "cmd"},
		{Title: "/compact", Hint: "summarize & compact context", Value: "/compact", Category: "cmd"},
		{Title: "/context", Hint: "show context usage", Value: "/context", Category: "cmd"},
		{Title: "/diff", Hint: "git diff", Value: "/diff", Category: "cmd"},
		{Title: "/review", Hint: "review git diff", Value: "/review", Category: "cmd"},
		{Title: "/fmt", Hint: "go fmt ./...", Value: "/fmt", Category: "cmd"},
		{Title: "/test", Hint: "go test ./...", Value: "/test", Category: "cmd"},
		{Title: "/run <cmd>", Hint: "run shell cmd", Value: "/run", Category: "cmd"},
		{Title: "/tree", Hint: "file tree", Value: "/tree", Category: "cmd"},
		{Title: "/search <q>", Hint: "grep workspace", Value: "/search", Category: "cmd"},
		{Title: "/add @file", Hint: "attach file to context", Value: "/add", Category: "cmd"},
		{Title: "/undo", Hint: "undo last tool (git checkout .)", Value: "/undo", Category: "cmd"},
		{Title: "/status", Hint: "git + perf status", Value: "/status", Category: "cmd"},
		{Title: "/persona", Hint: "show persona", Value: "/persona", Category: "cmd"},
		{Title: "/config", Hint: "show config", Value: "/config", Category: "cmd"},
		{Title: "/developer", Hint: "about Bittu Chauhan", Value: "/developer", Category: "cmd"},
		// Modes quick
		{Title: "Mode: build", Hint: "🛠️ full auto", Value: "/mode build", Category: "mode"},
		{Title: "Mode: plan", Hint: "🗺️ plan first", Value: "/mode plan", Category: "mode"},
		{Title: "Mode: ask", Hint: "💬 read-only", Value: "/mode ask", Category: "mode"},
		{Title: "Mode: research", Hint: "🔬 web+grep", Value: "/mode research", Category: "mode"},
		{Title: "Mode: debug", Hint: "🐛 reproduce→fix", Value: "/mode debug", Category: "mode"},
		{Title: "Mode: review", Hint: "🔭 audit only", Value: "/mode review", Category: "mode"},
		// Providers quick
		{Title: "Use SambaNova (default)", Hint: "MiniMax-M3.1-Flash", Value: "/provider sambanova", Category: "provider"},
		{Title: "Use Groq", Hint: "llama-3.3-70b", Value: "/provider groq", Category: "provider"},
		{Title: "Use OpenRouter", Hint: "any model", Value: "/provider openrouter", Category: "provider"},
		{Title: "Use OpenAI", Hint: "gpt-4o", Value: "/provider openai", Category: "provider"},
	}
}

// Fuzzy ranks items by substring score.
func Filter(items []Item, q string) []Item {
	q = strings.ToLower(strings.TrimSpace(q))
	if q == "" {
		out := make([]Item, len(items))
		copy(out, items)
		return out
	}
	var out []Item
	for _, it := range items {
		score := scoreMatch(q, strings.ToLower(it.Title+" "+it.Hint+" "+it.Value))
		if score >= 0 {
			it.Score = score
			out = append(out, it)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Score == out[j].Score {
			return out[i].Title < out[j].Title
		}
		return out[i].Score > out[j].Score
	})
	return out
}

func scoreMatch(needle, hay string) float64 {
	// subsequence match scoring
	var si, ni int
	score := 0.0
	// exact substring bonus
	if strings.Contains(hay, needle) {
		score += 100
	}
	// subsequence
	for si < len(hay) && ni < len(needle) {
		if hay[si] == needle[ni] {
			score += 1
			if si == 0 || hay[si-1] == ' ' || hay[si-1] == '/' {
				score += 2 // word boundary
			}
			ni++
		}
		si++
	}
	if ni == len(needle) {
		return score
	}
	return -1
}

// FileFinder lists files under root (lightweight).
func FileFinder(root string, max int) []string {
	if root == "" {
		root = "."
	}
	var out []string
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			// prune heavy dirs
			if info != nil && info.IsDir() {
				name := info.Name()
				if name == ".git" || name == "node_modules" || name == "vendor" {
					return filepath.SkipDir
				}
			}
			return nil
		}
		if info.Size() > 2_000_000 {
			return nil
		}
		rel, _ := filepath.Rel(root, path)
		// skip hidden
		if strings.HasPrefix(rel, ".") {
			return nil
		}
		out = append(out, rel)
		if max > 0 && len(out) >= max {
			return filepath.SkipAll
		}
		return nil
	})
	sort.Strings(out)
	return out
}
