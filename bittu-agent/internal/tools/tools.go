// Package tools — BITTU AGENT tool engine v2 ultra
// Developer by Bittu Chauhan
package tools

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type Tool struct {
	Name        string
	Description string
	Run         func(args string) (string, error)
	ReadOnly    bool
	Mutating    bool
}

func DefaultRegistry() map[string]Tool {
	return map[string]Tool{
		"shell":      {Name: "shell", Description: "Run shell / PowerShell", Run: RunShell, Mutating: true},
		"read_file":  {Name: "read_file", Description: "Read file (path)", Run: ReadFile, ReadOnly: true},
		"write_file": {Name: "write_file", Description: "Write file path:::content", Run: WriteFile, Mutating: true},
		"edit_file":  {Name: "edit_file", Description: "Patch file path:::<old>:::<new>", Run: EditFile, Mutating: true},
		"list_dir":   {Name: "list_dir", Description: "List directory (path, default .)", Run: ListDir, ReadOnly: true},
		"tree":       {Name: "tree", Description: "File tree (stable)", Run: Tree, ReadOnly: true},
		"grep":       {Name: "grep", Description: "Search regex pattern:::path", Run: Grep, ReadOnly: true},
		"search":     {Name: "search", Description: "Alias of grep", Run: Grep, ReadOnly: true},
		"web_fetch":  {Name: "web_fetch", Description: "Fetch URL as text", Run: WebFetch, ReadOnly: true},
		"git_status": {Name: "git_status", Description: "git status --short", Run: GitStatus, ReadOnly: true},
		"git_diff":   {Name: "git_diff", Description: "git diff", Run: GitDiff, ReadOnly: true},
		"go_fmt":     {Name: "go_fmt", Description: "go fmt ./...", Run: GoFmt, Mutating: true},
		"go_test":    {Name: "go_test", Description: "go test ./... (path optional)", Run: GoTest, ReadOnly: true},
		"now":        {Name: "now", Description: "Current time", Run: Now, ReadOnly: true},
	}
}

func RunShell(args string) (string, error) {
	args = strings.TrimSpace(args)
	if args == "" {
		return "", fmt.Errorf("empty command")
	}
	lower := strings.ToLower(args)
	for _, bad := range []string{"rm -rf /", "format c:", "mkfs.", "shutdown", "del /s /q c:\\windows"} {
		if strings.Contains(lower, bad) {
			return "", fmt.Errorf("blocked potentially destructive command")
		}
	}
	var cmd *exec.Cmd
	if runtimeOS() == "windows" {
		cmd = exec.Command("powershell", "-NoProfile", "-Command", args)
	} else {
		cmd = exec.Command("sh", "-c", args)
	}
	cmd.Dir = mustCwd()
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Sprintf("$ %s\n%s\nexit: %v", args, string(out), err), nil
	}
	return fmt.Sprintf("$ %s\n%s", args, string(out)), nil
}

func ReadFile(args string) (string, error) {
	p := strings.TrimSpace(args)
	if p == "" {
		return "", fmt.Errorf("path required")
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return "", err
	}
	s := string(b)
	if len(s) > 200_000 {
		s = s[:200_000] + "\n...[truncated]"
	}
	return fmt.Sprintf("%% %s (%d bytes)\n\n%s", p, len(b), s), nil
}

func WriteFile(args string) (string, error) {
	parts := strings.SplitN(args, ":::", 2)
	if len(parts) != 2 {
		return "", fmt.Errorf("format: path:::content")
	}
	path := strings.TrimSpace(parts[0])
	content := parts[1]
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil && filepath.Dir(path) != "." {
		return "", err
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", err
	}
	return fmt.Sprintf("wrote %s (%d bytes)", path, len(content)), nil
}

func EditFile(args string) (string, error) {
	// path:::old:::new (split into 3)
	parts := strings.SplitN(args, ":::", 3)
	if len(parts) != 3 {
		return "", fmt.Errorf("format: path:::old:::new")
	}
	path := strings.TrimSpace(parts[0])
	oldS := parts[1]
	newS := parts[2]
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	s := string(b)
	if !strings.Contains(s, oldS) {
		return "", fmt.Errorf("old snippet not found in %s", path)
	}
	s = strings.Replace(s, oldS, newS, 1)
	if err := os.WriteFile(path, []byte(s), 0o644); err != nil {
		return "", err
	}
	return fmt.Sprintf("patched %s", path), nil
}

func ListDir(args string) (string, error) {
	p := strings.TrimSpace(args)
	if p == "" {
		p = "."
	}
	ents, err := os.ReadDir(p)
	if err != nil {
		return "", err
	}
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("## %s — %d entries\n\n", p, len(ents)))
	for _, e := range ents {
		icon := "📄"
		if e.IsDir() {
			icon = "📁"
		}
		sb.WriteString(fmt.Sprintf("%s %-40s\n", icon, e.Name()))
	}
	return sb.String(), nil
}

func Tree(args string) (string, error) {
	p := strings.TrimSpace(args)
	if p == "" {
		p = "."
	}
	var sb strings.Builder
	_ = filepath.Walk(p, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(p, path)
		if rel == "." {
			return nil
		}
		depth := strings.Count(rel, string(filepath.Separator))
		name := info.Name()
		if info.IsDir() {
			if name == ".git" || name == "node_modules" || name == "vendor" {
				return filepath.SkipDir
			}
			sb.WriteString(strings.Repeat("  ", depth) + "📁 " + name + "\n")
		} else {
			sb.WriteString(strings.Repeat("  ", depth) + "📄 " + name + "\n")
		}
		// limit
		if sb.Len() > 20000 {
			return filepath.SkipAll
		}
		return nil
	})
	if sb.Len() == 0 {
		return "empty tree", nil
	}
	return "```\n" + sb.String() + "```", nil
}

func Grep(args string) (string, error) {
	parts := strings.SplitN(args, ":::", 2)
	pattern := strings.TrimSpace(parts[0])
	root := "."
	if len(parts) == 2 {
		root = strings.TrimSpace(parts[1])
		if root == "" {
			root = "."
		}
	}
	if pattern == "" {
		return "", fmt.Errorf("pattern required")
	}
	re, err := regexp.Compile("(?i)" + pattern)
	if err != nil {
		re = regexp.MustCompile("(?i)" + regexp.QuoteMeta(pattern))
	}
	var hits []string
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		if info.Size() > 1_000_000 {
			return nil
		}
		if strings.Contains(path, ".git") || strings.Contains(path, "node_modules") {
			return nil
		}
		b, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		for i, line := range strings.Split(string(b), "\n") {
			if re.MatchString(line) {
				hits = append(hits, fmt.Sprintf("%s:%d: %s", path, i+1, strings.TrimSpace(line)))
				if len(hits) > 200 {
					return filepath.SkipAll
				}
			}
		}
		return nil
	})
	if len(hits) == 0 {
		return fmt.Sprintf("no matches for %q in %s", pattern, root), nil
	}
	return strings.Join(hits, "\n"), nil
}

func WebFetch(args string) (string, error) {
	u := strings.TrimSpace(args)
	if u == "" {
		return "", fmt.Errorf("url required")
	}
	client := &http.Client{Timeout: 25 * time.Second}
	resp, err := client.Get(u)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, 500_000))
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s -> %d\n\n%s", u, resp.StatusCode, string(b)), nil
}

func GitStatus(args string) (string, error) {
	out, err := exec.Command("git", "status", "--short", "--branch").CombinedOutput()
	if err != nil {
		return string(out) + "\n" + err.Error(), nil
	}
	if strings.TrimSpace(string(out)) == "" {
		return "clean tree", nil
	}
	return "```\n" + string(out) + "```", nil
}

func GitDiff(args string) (string, error) {
	out, err := exec.Command("git", "diff", "--color=never").CombinedOutput()
	if err != nil {
		return string(out) + "\n" + err.Error(), nil
	}
	s := string(out)
	if len(s) > 50000 {
		s = s[:50000]
	}
	if strings.TrimSpace(s) == "" {
		return "no diff (clean)", nil
	}
	return "```diff\n" + s + "```", nil
}

func GoFmt(args string) (string, error) {
	out, err := exec.Command("go", "fmt", "./...").CombinedOutput()
	if err != nil {
		return string(out) + "\n" + err.Error(), nil
	}
	if strings.TrimSpace(string(out)) == "" {
		return "gofmt clean (no files changed)", nil
	}
	return string(out), nil
}

func GoTest(args string) (string, error) {
	pkg := strings.TrimSpace(args)
	if pkg == "" {
		pkg = "./..."
	}
	out, err := exec.Command("go", "test", pkg).CombinedOutput()
	if err != nil {
		return "```\n" + string(out) + "```\n" + err.Error(), nil
	}
	return "```\n" + string(out) + "```", nil
}

func Now(args string) (string, error) {
	return time.Now().Format(time.RFC3339), nil
}

func runtimeOS() string {
	if os.Getenv("OS") == "Windows_NT" || filepath.Separator == '\\' {
		return "windows"
	}
	return "unix"
}

func mustCwd() string {
	wd, _ := os.Getwd()
	return wd
}

func ParseToolCall(text string) (name, args string, ok bool) {
	re := regexp.MustCompile("(?s)```tool\\s*\\n\\s*([a-z_]+)\\s*:\\s*(.*?)```")
	m := re.FindStringSubmatch(text)
	if len(m) >= 3 {
		return strings.TrimSpace(m[1]), strings.TrimSpace(m[2]), true
	}
	return "", "", false
}

func ParseAllToolCalls(text string) [][2]string {
	re := regexp.MustCompile("(?s)```tool\\s*\\n\\s*([a-z_]+)\\s*:\\s*(.*?)```")
	ms := re.FindAllStringSubmatch(text, -1)
	var out [][2]string
	for _, m := range ms {
		if len(m) >= 3 {
			out = append(out, [2]string{strings.TrimSpace(m[1]), strings.TrimSpace(m[2])})
		}
	}
	return out
}

func Execute(reg map[string]Tool, name, args string) (string, error) {
	t, ok := reg[name]
	if !ok {
		return "", fmt.Errorf("unknown tool %q", name)
	}
	return t.Run(args)
}

// GitBranch returns current branch or empty.
func GitBranch() string {
	out, err := exec.Command("git", "rev-parse", "--abbrev-ref", "HEAD").CombinedOutput()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
