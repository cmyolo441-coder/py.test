// Package tui — BITTU AGENT Ultra TUI Engine (v2)
// Developer by Bittu Chauhan
package tui

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"bittu-agent/internal/config"
	"bittu-agent/internal/llm"
	"bittu-agent/internal/mode"
	"bittu-agent/internal/palette"
	"bittu-agent/internal/session"
	"bittu-agent/internal/theme"
	"bittu-agent/internal/tools"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
)

type Msg struct {
	Role    string
	Content string
	Tool    bool
}

type tickMsg struct{ t time.Time }
type streamMsg struct{ chunk string }
type doneMsg struct{ err error }
type toolMsg struct {
	content string
	err     error
}
type compactMsg struct{ summary string }
type saveMsg struct{ path string }

type PaletteItem = palette.Item

type Model struct {
	cfg       *config.Config
	client    *llm.Client
	tools     map[string]tools.Tool
	theme     theme.Theme
	themeName string

	width  int
	height int
	ready  bool

	viewport viewport.Model
	input    textarea.Model
	spinner  spinner.Model

	messages  []Msg
	streaming bool
	streamBuf string
	streamSrc chan string

	frame     int
	status    string
	showHelp  bool
	showThemes bool
	statusErr string

	promptTokens     int
	completionTokens int
	toolCalls        int
	attachedFiles    []string
	pendingTool      *[2]string // awaiting approval
	pendingAsk       bool

	// Ultra v2
	modeName     string
	provider     string
	showSidebar  bool
	showPalette  bool
	showFiles    bool
	showReason   bool
	paletteQuery string
	paletteItems []PaletteItem
	paletteSel   int
	fileItems    []string
	fileSel      int
	sessionID    string
	sessionTitle string
	fps          int
	fpsCounter   int
	fpsLast      time.Time
	latencyMs    int64
	tokensPerSec float64
	gitBranch    string
	reasoningBuf string
	cancel       context.CancelFunc
}

func New(cfg *config.Config) Model {
	th := theme.Get(cfg.TUI.Theme)
	ta := textarea.New()
	ta.Placeholder = "Ask Devin…  @file to attach · Ctrl+K palette · Ctrl+P files"
	ta.Focus()
	ta.CharLimit = 12000
	ta.SetHeight(3)
	ta.ShowLineNumbers = false
	ta.Prompt = "❯ "
	ta.FocusedStyle.Prompt = lipgloss.NewStyle().Foreground(th.Primary).Bold(true)
	ta.FocusedStyle.Text = lipgloss.NewStyle().Foreground(lipgloss.Color("#FFFFFF"))
	ta.FocusedStyle.Placeholder = lipgloss.NewStyle().Foreground(lipgloss.Color("#6B7280")).Italic(true)
	ta.BlurredStyle.Placeholder = ta.FocusedStyle.Placeholder

	sp := spinner.New()
	sp.Spinner = spinner.Dot

	modeName := cfg.Agent.Mode
	if modeName == "" {
		modeName = "build"
	}
	provider := cfg.Providers.Active
	if provider == "" {
		provider = "sambanova"
	}
	m := Model{
		cfg: cfg, client: llm.New(cfg), tools: tools.DefaultRegistry(),
		theme: th, themeName: cfg.TUI.Theme,
		input: ta, spinner: sp, status: "● Ready",
		modeName: modeName, provider: provider,
		showSidebar: cfg.TUI.ShowSidebar,
		sessionID: session.NewID(),
		gitBranch: tools.GitBranch(),
	}
	welcome := fmt.Sprintf(`%s

Welcome to **BITTU AGENT** — Devin-style terminal harness.

- **Model:** %s · **Mode:** %s · **Provider:** %s
- **Shortcuts:** Ctrl+K palette · Ctrl+P files · Ctrl+A mode · Ctrl+J sidebar
- **Tools:** shell · files · edit · git · go — auto or approve
- **Try:** /help · /mode · /sessions · /diff

Developer by **Bittu Chauhan**
`, cfg.TUI.Welcome, m.client.Model, m.modeName, m.provider)
	m.messages = append(m.messages, Msg{Role: "assistant", Content: welcome})
	m.syncViewport()
	return m
}

func (m Model) Init() tea.Cmd {
	return tea.Batch(tea.EnterAltScreen, tea.EnableMouseCellMotion, tickCmd(), spinner.Tick)
}

func tickCmd() tea.Cmd {
	return tea.Tick(80*time.Millisecond, func(t time.Time) tea.Msg { return tickMsg{t} })
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		if !m.ready {
			m.viewport = viewport.New(m.width, m.height-11)
			m.ready = true
		} else {
			m.viewport.Width = m.width
			if m.showSidebar {
				m.viewport.Width = m.width - (m.cfg.TUI.SidebarWidth + 4)
			}
			m.viewport.Height = m.height - 11
		}
		bodyW := max(1, m.width-10)
		m.input.SetWidth(bodyW)
		m.syncViewport()
		m.viewport.GotoBottom()

	case tickMsg:
		m.frame++
		// FPS meter
		m.fpsCounter++
		if m.fpsLast.IsZero() {
			m.fpsLast = msg.t
		}
		if msg.t.Sub(m.fpsLast) >= time.Second {
			m.fps = m.fpsCounter
			m.fpsCounter = 0
			m.fpsLast = msg.t
		}
		if m.cfg.TUI.Animations {
			return m, tickCmd()
		}
		return m, nil

	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spinner, cmd = m.spinner.Update(msg)
		return m, cmd

	case streamMsg:
		m.streamBuf += msg.chunk
		if len(m.messages) > 0 {
			m.messages[len(m.messages)-1].Content = m.streamBuf
		}
		m.syncViewport()
		m.viewport.GotoBottom()
		return m, m.pollStream()

	case doneMsg:
		m.streamSrc = nil
		if st := m.client.LastStats; st != nil {
			if st.Duration > 0 {
				m.latencyMs = st.Duration.Milliseconds()
			}
			m.tokensPerSec = st.TokensPerSec
		}
		if msg.err != nil {
			m.statusErr = msg.err.Error()
			m.messages = append(m.messages, Msg{Role: "system", Content: "⚠️ " + msg.err.Error()})
			m.status = "● Ready"
			m.streaming = false
		} else {
			m.completionTokens += len(m.streamBuf) / 4
			if m.client.LastStats != nil && m.client.LastStats.Usage != nil {
				m.promptTokens = m.client.LastStats.Usage.PromptTokens
				m.completionTokens = m.client.LastStats.Usage.CompletionTokens
			}
			toolCalls := tools.ParseAllToolCalls(m.streamBuf)
			if len(toolCalls) > 0 && m.cfg.Tools.Enabled {
				name := toolCalls[0][0]
				args := toolCalls[0][1]
				// Approval gate for mutating tools
				if t, ok := m.tools[name]; ok && t.Mutating && m.cfg.TUI.Approval && mode.Get(m.modeName).ToolPolicy != "auto" ||
					(t.Mutating && m.cfg.TUI.Approval && strings.ToLower(m.modeName) == "plan") {
					m.pendingTool = &[2]string{name, args}
					m.pendingAsk = true
					m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("⏸️ [approve] execute `%s`? Reply `y` to run, `n` to skip.\nargs:\n%s", name, truncate(args, 800))})
					m.status = "⏸️ Approval"
					m.streaming = false
					m.syncViewport()
					m.viewport.GotoBottom()
					return m, nil
				}
				m.toolCalls++
				m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("🧰 tool: %s(%s)", name, truncate(args, 120))})
				return m, func() tea.Msg {
					out, err := tools.Execute(m.tools, name, args)
					return toolMsg{content: out, err: err}
				}
			}
			m.status = "● Ready"
			m.streaming = false
		}
		m.syncViewport()
		m.viewport.GotoBottom()
		// auto-compact check
		if m.cfg.TUI.AutoCompact && m.contextPct() > int(m.cfg.TUI.CompactThreshold*100) && (m.promptTokens+m.completionTokens) > 2000 {
			return m, m.compactCmd()
		}
		// autosave
		if m.cfg.Sessions.AutoSave && len(m.messages) > 2 {
			go m.autosave()
		}
		return m, nil

	case toolMsg:
		if msg.err != nil {
			m.messages = append(m.messages, Msg{Role: "system", Content: "❌ tool error: " + msg.err.Error()})
		} else {
			m.messages = append(m.messages, Msg{Role: "tool", Content: msg.content, Tool: true})
		}
		m.syncViewport()
		return m, m.streamWithToolResult()

	case compactMsg:
		m.messages = append(m.messages, Msg{Role: "assistant", Content: "### 📦 Context compacted\n\n" + msg.summary + "\n\n*Original history summarized. Continuing...*"})
		m.status = "● Ready"
		m.syncViewport()
		m.viewport.GotoBottom()

	case saveMsg:
		m.messages = append(m.messages, Msg{Role: "system", Content: "💾 saved → " + msg.path})
		m.syncViewport()

	case tea.KeyMsg:
		// Palette navigation first
		if m.showPalette || m.showFiles {
			switch msg.String() {
			case "esc", "ctrl+k", "ctrl+p":
				m.showPalette, m.showFiles = false, false
				return m, nil
			case "up", "ctrl+up":
				if m.showFiles {
					m.fileSel = max(0, m.fileSel-1)
				} else {
					m.paletteSel = max(0, m.paletteSel-1)
				}
				return m, nil
			case "down", "ctrl+down":
				if m.showFiles {
					if m.fileSel < len(m.fileItems)-1 {
						m.fileSel++
					}
				} else {
					if m.paletteSel < len(m.paletteItems)-1 {
						m.paletteSel++
					}
				}
				return m, nil
			case "enter":
				if m.showFiles && len(m.fileItems) > 0 {
					f := m.fileItems[m.fileSel]
					m.showFiles = false
					m.attachFile(f)
					return m, nil
				}
				if m.showPalette && len(m.paletteItems) > 0 {
					v := m.paletteItems[m.paletteSel].Value
					m.showPalette = false
					m.paletteQuery = ""
					m.paletteItems = nil
					return m, m.execPalette(v)
				}
				return m, nil
			case "backspace":
				if m.showFiles {
					return m, nil // files are not editable
				}
				if len(m.paletteQuery) > 0 {
					m.paletteQuery = m.paletteQuery[:len(m.paletteQuery)-1]
					m.paletteItems = palette.Filter(palette.Commands(), m.paletteQuery)
					m.paletteSel = 0
				}
				return m, nil
			default:
				if m.showPalette {
					// printable chars append to query (except ctrl+...)
					s := msg.String()
					if len(s) == 1 && s != " " {
						m.paletteQuery += s
					} else if strings.HasPrefix(s, "tab") {
						// ignore
					} else if len(s) > 1 && !strings.HasPrefix(s, "ctrl") && !strings.HasPrefix(s, "alt") {
						m.paletteQuery += s
					}
					m.paletteItems = palette.Filter(palette.Commands(), m.paletteQuery)
					m.paletteSel = 0
					return m, nil
				}
			}
		}

		switch msg.String() {
		case "ctrl+q", "ctrl+c":
			if m.cancel != nil {
				m.cancel()
			}
			// autosave on quit
			if m.cfg.Sessions.AutoSave {
				go m.autosave()
			}
			return m, tea.Quit
		case "ctrl+l":
			m.messages = m.messages[:1]
			m.promptTokens, m.completionTokens = 0, 0
			m.syncViewport()
			m.viewport.GotoTop()
			return m, nil
		case "ctrl+h":
			m.showHelp = !m.showHelp
			return m, nil
		case "ctrl+t":
			m.showThemes = !m.showThemes
			return m, nil
		case "ctrl+m":
			m.cycleModel()
			m.syncViewport()
			return m, nil
		case "ctrl+k":
			m.showPalette = true
			m.showFiles = false
			m.paletteQuery = ""
			m.paletteItems = palette.Filter(palette.Commands(), "")
			m.paletteSel = 0
			return m, nil
		case "ctrl+p":
			m.showFiles = true
			m.showPalette = false
			cwd, _ := os.Getwd()
			m.fileItems = palette.FileFinder(cwd, 300)
			m.fileSel = 0
			return m, nil
		case "ctrl+a":
			m.modeName = mode.Next(m.modeName)
			m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("Mode → %s (%s)", m.modeName, mode.Get(m.modeName).Description)})
			m.syncViewport()
			m.viewport.GotoBottom()
			return m, nil
		case "ctrl+j":
			m.showSidebar = !m.showSidebar
			m.viewport.Width = m.width - boolToInt(m.showSidebar)*(m.cfg.TUI.SidebarWidth+4)
			m.syncViewport()
			return m, nil
		case "ctrl+s":
			m.sessionTitle = strings.TrimSpace(m.input.Value())
			if m.sessionTitle == "" {
				m.sessionTitle = "bittu-" + time.Now().Format("150405")
			}
			m.input.Reset()
			_ = m.sessionTitle
			path, err := m.saveSession(m.sessionTitle)
			if err != nil {
				m.messages = append(m.messages, Msg{Role: "system", Content: "💾 save error: " + err.Error()})
			} else {
				m.messages = append(m.messages, Msg{Role: "system", Content: "💾 saved → " + path})
			}
			m.syncViewport()
			return m, nil
		case "ctrl+o":
			return m, m.listSessionsCmd()
		case "ctrl+r":
			m.showReason = !m.showReason
			m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("reasoning panel: %v", m.showReason)})
			m.syncViewport()
			return m, nil
		case "ctrl+d":
			if m.streaming && m.cancel != nil {
				m.cancel()
				m.streaming = false
				m.status = "● Cancelled"
				return m, nil
			}
		case "shift+ctrl+c", "ctrl+shift+c":
			return m, m.compactCmd()
		case "enter":
			if m.pendingAsk && m.pendingTool != nil {
				val := strings.TrimSpace(strings.ToLower(m.input.Value()))
				if val == "y" || val == "yes" {
					name, args := m.pendingTool[0], m.pendingTool[1]
					m.pendingTool, m.pendingAsk = nil, false
					m.input.Reset()
					m.toolCalls++
					m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("✅ approved %s", name)})
					return m, func() tea.Msg {
						out, err := tools.Execute(m.tools, name, args)
						return toolMsg{content: out, err: err}
					}
				} else if val == "n" || val == "no" || val == "" {
					m.pendingTool, m.pendingAsk = nil, false
					m.input.Reset()
					m.messages = append(m.messages, Msg{Role: "system", Content: "⏭️ skipped tool"})
					m.status = "● Ready"
					m.streaming = false
					m.syncViewport()
					return m, nil
				}
				// else fall through (user typed something else → treat as new msg, drop pending)
				m.pendingTool, m.pendingAsk = nil, false
			}
			val := strings.TrimSpace(m.input.Value())
			if val == "" {
				return m, nil
			}
			if m.streaming {
				return m, nil
			}
			// @file attach inline
			val = m.expandAtFiles(val)
			m.input.Reset()
			if strings.HasPrefix(val, "/") {
				return m, m.handleSlash(val)
			}
			if strings.HasPrefix(val, "!") {
				// !shell fast-path
				cmd := strings.TrimSpace(strings.TrimPrefix(val, "!"))
				m.messages = append(m.messages, Msg{Role: "user", Content: "!" + cmd})
				m.promptTokens += len(val) / 4
				m.toolCalls++
				m.messages = append(m.messages, Msg{Role: "system", Content: "🧰 tool: shell(...)"})
				return m, func() tea.Msg {
					out, err := tools.Execute(m.tools, "shell", cmd)
					return toolMsg{content: out, err: err}
				}
			}
			m.messages = append(m.messages, Msg{Role: "user", Content: val})
			m.promptTokens += len(val) / 4
			m.syncViewport()
			m.viewport.GotoBottom()
			return m, m.startStream(val)
		}
		if !m.streaming {
			var cmd tea.Cmd
			m.input, cmd = m.input.Update(msg)
			return m, cmd
		}
	}

	return m, nil
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func (m *Model) contextPct() int {
	ctw := m.cfg.ContextWindow()
	if ctw == 0 {
		return 0
	}
	return (m.promptTokens + m.completionTokens) * 100 / ctw
}

func (m Model) compactCmd() tea.Cmd {
	msgs := m.buildLLMMessages()
	client := m.client
	return func() tea.Msg {
		summarize := append([]llm.Message{{Role: "system", Content: "Summarize this chat history into a compact context block for continuing work. Keep: goals, files touched, decisions, TODOs. Max 500 words."}}, msgs...)
		out, _, err := client.Chat(context.Background(), summarize)
		if err != nil {
			out = "compact failed: " + err.Error()
		}
		return compactMsg{summary: out}
	}
}

func (m *Model) saveSession(title string) (string, error) {
	if title == "" {
		title = "bittu-" + time.Now().Format("150405")
	}
	e := session.Entry{
		ID: m.sessionID, Title: title, Model: m.client.Model,
		Theme: m.themeName, Mode: m.modeName,
		TokensIn: m.promptTokens, TokensOut: m.completionTokens,
	}
	for _, mm := range m.messages {
		e.Messages = append(e.Messages, session.Message{Role: mm.Role, Content: mm.Content})
	}
	return session.Save(&e)
}

func (m *Model) autosave() {
	_, _ = m.saveSession("autosave-" + time.Now().Format("150405"))
}

func (m Model) listSessionsCmd() tea.Cmd {
	return func() tea.Msg {
		entries, err := session.List(10)
		if err != nil {
			return toolMsg{content: "sessions error: " + err.Error()}
		}
		var sb strings.Builder
		sb.WriteString("### 🗂️ Sessions\n\n")
		if len(entries) == 0 {
			sb.WriteString("No sessions yet. `Ctrl+S` to save.\n")
		}
		for _, e := range entries {
			sb.WriteString(fmt.Sprintf("- **%s** — `%s` • %s • %d msgs • %s\n", e.Title, e.ID, e.Model, len(e.Messages), e.UpdatedAt.Format("15:04")))
		}
		sb.WriteString("\n`/resume <id>` to restore • `/export <id> out.md`")
		return toolMsg{content: sb.String()}
	}
}

func (m *Model) attachFile(path string) {
	if path == "" {
		return
	}
	m.attachedFiles = append(m.attachedFiles, path)
	m.messages = append(m.messages, Msg{Role: "system", Content: "📎 attached @" + path})
	m.syncViewport()
}

func (m *Model) expandAtFiles(val string) string {
	// supports @path tokens — inject file contents as context
	parts := strings.Fields(val)
	var out []string
	for _, p := range parts {
		if strings.HasPrefix(p, "@") && len(p) > 1 {
			fp := strings.TrimPrefix(p, "@")
			if b, err := os.ReadFile(fp); err == nil {
				s := string(b)
				if len(s) > 8000 {
					s = s[:8000] + "\n...[truncated]"
				}
				out = append(out, fmt.Sprintf("\n\n[@%s]\n%s", fp, s))
				if !contains(m.attachedFiles, fp) {
					m.attachedFiles = append(m.attachedFiles, fp)
				}
			} else {
				out = append(out, p)
			}
		} else {
			out = append(out, p)
		}
	}
	return strings.Join(out, " ")
}

func contains(s []string, v string) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

func (m *Model) cycleModel() {
	models := m.cfg.Models.Available
	if len(models) == 0 {
		return
	}
	cur := m.client.Model
	idx := 0
	for i, mm := range models {
		if mm == cur {
			idx = i
		}
	}
	next := models[(idx+1)%len(models)]
	m.client.Model = next
	m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("🔀 model switched → %s", next)})
}

func (m Model) execPalette(v string) tea.Cmd {
	v = strings.TrimSpace(v)
	if v == "" {
		return nil
	}
	if strings.HasPrefix(v, "/") {
		return m.handleSlash(v)
	}
	// plain text → as user message
	m.input.SetValue(v)
	return nil
}

func (m Model) handleSlash(cmd string) tea.Cmd {
	parts := strings.Fields(cmd)
	name := strings.ToLower(parts[0])
	args := ""
	if len(parts) > 1 {
		args = strings.TrimSpace(strings.TrimPrefix(cmd, parts[0]))
	}

	// custom user commands first
	for _, cc := range m.cfg.Commands {
		if name == "/"+cc.Name {
			m.messages = append(m.messages, Msg{Role: "user", Content: args})
			prompt := cc.Prompt
			if args != "" {
				prompt = prompt + "\n\nUser: " + args
			}
			m.messages = append(m.messages, Msg{Role: "system", Content: "⚡ custom /" + cc.Name})
			m.syncViewport()
			return m.startStream(prompt)
		}
	}

	switch name {
	case "/help", "/h":
		m.messages = append(m.messages, Msg{Role: "assistant", Content: helpText()})
	case "/clear":
		m.messages = m.messages[:1]
		m.promptTokens, m.completionTokens = 0, 0
		m.attachedFiles = nil
	case "/model":
		if args != "" {
			m.client.Model = args
			m.messages = append(m.messages, Msg{Role: "system", Content: "🔀 model → " + args})
		} else {
			m.messages = append(m.messages, Msg{Role: "assistant", Content: fmt.Sprintf("**Models:**\n- active: `%s` (%s)\n- available: %s\n- base: %s\n- context: %d • max_out: %d", m.client.Model, m.provider, strings.Join(m.cfg.Models.Available, ", "), m.client.BaseURL, m.cfg.ContextWindow(), m.cfg.MaxTokens())})
		}
	case "/theme":
		if args != "" {
			m.theme = theme.Get(args)
			m.themeName = args
			m.input.FocusedStyle.Prompt = lipgloss.NewStyle().Foreground(m.theme.Primary).Bold(true)
			m.messages = append(m.messages, Msg{Role: "system", Content: "🎨 theme → " + args})
		} else {
			m.showThemes = !m.showThemes
		}
	case "/tools":
		var sb strings.Builder
		sb.WriteString("**Tools (`enabled=" + fmt.Sprintf("%v", m.cfg.Tools.Enabled) + "`):**\n")
		for k, v := range m.tools {
			sb.WriteString(fmt.Sprintf("- `%s` — %s\n", k, v.Description))
		}
		sb.WriteString("\nUse: ` ```tool\\n shell: dir\\n ``` `")
		m.messages = append(m.messages, Msg{Role: "assistant", Content: sb.String()})
	case "/provider":
		if args != "" {
			prov := strings.ToLower(strings.TrimSpace(args))
			if p, ok := m.cfg.ProviderByName(prov); ok {
				m.client = llm.NewFor(m.cfg, prov, p.Model)
				m.provider = prov
				m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("🌐 provider → %s (model %s, %s)", prov, p.Model, p.BaseURL)})
			} else {
				m.messages = append(m.messages, Msg{Role: "system", Content: "❌ unknown provider. Try: sambanova, groq, openai, openrouter"})
			}
		} else {
			m.messages = append(m.messages, Msg{Role: "assistant", Content: fmt.Sprintf("**Providers:**\n- active: `%s`\n- base: %s\n- model: %s\n\nUse `/provider groq|openai|openrouter|sambanova`", m.provider, m.client.BaseURL, m.client.Model)})
		}
	case "/mode":
		if args != "" {
			md := mode.Get(args)
			m.modeName = md.Name
			m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("%s mode → %s — %s", md.Icon, md.Name, md.Description)})
		} else {
			var sb strings.Builder
			sb.WriteString("**Modes** (`Ctrl+A` to cycle):\n")
			for _, mm := range mode.All {
				sb.WriteString(fmt.Sprintf("- `%s` %s %s — policy `%s`\n", mm.Name, mm.Icon, mm.Description, mm.ToolPolicy))
			}
			sb.WriteString(fmt.Sprintf("\nactive: `%s`", m.modeName))
			m.messages = append(m.messages, Msg{Role: "assistant", Content: sb.String()})
		}
	case "/sessions":
		return m.listSessionsCmd()
	case "/save":
		title := args
		if title == "" {
			title = "bittu-" + time.Now().Format("150405")
		}
		path, err := m.saveSession(title)
		if err != nil {
			m.messages = append(m.messages, Msg{Role: "system", Content: "💾 save error: " + err.Error()})
		} else {
			m.messages = append(m.messages, Msg{Role: "system", Content: "💾 saved → " + path})
		}
	case "/resume":
		if args == "" {
			m.messages = append(m.messages, Msg{Role: "system", Content: "usage: /resume <id> (see /sessions)"})
			break
		}
		e, err := session.Load(strings.TrimSpace(args))
		if err != nil {
			m.messages = append(m.messages, Msg{Role: "system", Content: "❌ load error: " + err.Error()})
			break
		}
		m.sessionID = e.ID
		m.messages = m.messages[:1]
		for _, mm := range e.Messages {
			m.messages = append(m.messages, Msg{Role: mm.Role, Content: mm.Content})
		}
		m.promptTokens, m.completionTokens = e.TokensIn, e.TokensOut
		if e.Theme != "" {
			m.theme = theme.Get(e.Theme)
			m.themeName = e.Theme
		}
		if e.Mode != "" {
			m.modeName = e.Mode
		}
		m.messages = append(m.messages, Msg{Role: "system", Content: "📂 restored " + e.Title})
	case "/export":
		// /export [id] [file.md]
		eparts := strings.Fields(args)
		id := m.sessionID
		out := "session-export.md"
		if len(eparts) >= 1 && eparts[0] != "" {
			id = eparts[0]
		}
		if len(eparts) >= 2 {
			out = eparts[1]
		}
		if err := session.Export(id, out); err != nil {
			m.messages = append(m.messages, Msg{Role: "system", Content: "❌ export: " + err.Error()})
		} else {
			m.messages = append(m.messages, Msg{Role: "system", Content: "📤 exported → " + out})
		}
	case "/compact", "/context":
		if name == "/context" {
			pct := m.contextPct()
			m.messages = append(m.messages, Msg{Role: "assistant", Content: fmt.Sprintf("**Context**\n- used: %d tokens (%d%% of %d)\n- in: %d • out: %d • msgs: %d • files: %d\n- auto-compact at %d%%\n\n`/compact` to summarize now.", m.promptTokens+m.completionTokens, pct, m.cfg.ContextWindow(), m.promptTokens, m.completionTokens, len(m.messages), len(m.attachedFiles), int(m.cfg.TUI.CompactThreshold*100))})
		} else {
			return m.compactCmd()
		}
	case "/diff":
		out, err := tools.Execute(m.tools, "git_diff", "")
		if err != nil {
			m.messages = append(m.messages, Msg{Role: "system", Content: err.Error()})
		} else {
			m.messages = append(m.messages, Msg{Role: "assistant", Content: out})
		}
	case "/review":
		m.modeName = "review"
		m.messages = append(m.messages, Msg{Role: "user", Content: "Review current uncommitted changes. Use git_diff tool, then audit by severity."})
		m.syncViewport()
		return m.startStream("Review current uncommitted changes. Use git_diff tool, then audit by severity Critical/Major/Minor with file:line and suggested fix. Do not edit files.")
	case "/fmt":
		out, _ := tools.Execute(m.tools, "go_fmt", "")
		m.messages = append(m.messages, Msg{Role: "assistant", Content: out})
	case "/test":
		out, _ := tools.Execute(m.tools, "go_test", args)
		m.messages = append(m.messages, Msg{Role: "assistant", Content: out})
	case "/run":
		if args == "" {
			m.messages = append(m.messages, Msg{Role: "system", Content: "usage: /run <shell cmd>"})
			break
		}
		m.messages = append(m.messages, Msg{Role: "system", Content: "🧰 tool: shell(" + args + ")"})
		return func() tea.Msg {
			o, e := tools.Execute(m.tools, "shell", args)
			return toolMsg{content: o, err: e}
		}
	case "/tree":
		out, _ := tools.Execute(m.tools, "tree", args)
		m.messages = append(m.messages, Msg{Role: "assistant", Content: out})
	case "/search":
		if args == "" {
			m.messages = append(m.messages, Msg{Role: "system", Content: "usage: /search <query>"})
			break
		}
		out, _ := tools.Execute(m.tools, "grep", args+":::.")
		m.messages = append(m.messages, Msg{Role: "assistant", Content: out})
	case "/add":
		if args != "" {
			m.attachFile(strings.TrimSpace(args))
		} else {
			m.messages = append(m.messages, Msg{Role: "system", Content: "usage: /add @path or Ctrl+P picker"})
		}
	case "/undo":
		out, _ := tools.Execute(m.tools, "shell", "git checkout -- . 2>&1 || git clean -fd 2>&1")
		m.messages = append(m.messages, Msg{Role: "assistant", Content: out})
	case "/status":
		gs, _ := tools.Execute(m.tools, "git_status", "")
		m.messages = append(m.messages, Msg{Role: "assistant", Content: fmt.Sprintf("### Status\n\n- model: `%s` (%s)\n- mode: `%s`\n- theme: `%s`\n- fps: %d\n- latency: %dms • tok/s: %.1f\n- branch: `%s`\n\n%s", m.client.Model, m.provider, m.modeName, m.themeName, m.fps, m.latencyMs, m.tokensPerSec, m.gitBranch, gs)})
	case "/persona":
		m.messages = append(m.messages, Msg{Role: "assistant", Content: fmt.Sprintf("**Persona**\n- agent: `%s`\n- mode: `%s` (%s)\n- system: %s\n\nsystem_prompt injected per mode.", m.cfg.Agent.Name, m.modeName, mode.Get(m.modeName).Description, truncate(m.cfg.Agent.SystemPrompt, 200))})
	case "/config":
		m.messages = append(m.messages, Msg{Role: "assistant", Content: fmt.Sprintf("**Config**\n- developer: %s\n- model: %s (%s)\n- base_url: %s\n- api_key: %s…\n- context: %d • max_out: %d\n- theme: %s • mode: %s", m.cfg.Developer.Title, m.client.Model, m.provider, m.client.BaseURL, mask(m.client.APIKey), m.cfg.ContextWindow(), m.cfg.MaxTokens(), m.themeName, m.modeName)})
	case "/developer", "/about", "/bittu":
		m.messages = append(m.messages, Msg{Role: "assistant", Content: "## Developer by Bittu Chauhan\n\n**BITTU AGENT ULTRA v2** — next-gen terminal AI harness in Go.\n\n- Go + BubbleTea + Lip Gloss + Glamour\n- SambaNova + MiniMax-M3.1-Flash (multi-provider: Groq/OpenAI/OpenRouter)\n- 1M context • 16k max output • Streaming • Tools • Modes • Sessions • Palette\n\nBuilt with passion by **Bittu Chauhan**."})
	case "/exit", "/quit", "/bye":
		if m.cfg.Sessions.AutoSave {
			_, _ = m.saveSession("auto-quit")
		}
		return tea.Quit
	default:
		m.messages = append(m.messages, Msg{Role: "system", Content: fmt.Sprintf("unknown cmd %s — try /help or Ctrl+K", name)})
	}
	m.syncViewport()
	m.viewport.GotoBottom()
	return nil
}

func (m Model) startStream(userText string) tea.Cmd {
	m.streaming = true
	m.streamBuf = ""
	m.status = m.spinner.View() + " Thinking…"
	m.messages = append(m.messages, Msg{Role: "assistant", Content: ""})
	msgs := m.buildLLMMessages()
	ctx, cancel := context.WithCancel(context.Background())
	m.cancel = cancel
	client := m.client
	out := make(chan string, 64)
	done := make(chan error, 1)
	m.streamSrc = out
	go func() { client.Stream(ctx, msgs, out, done) }()
	go func() { <-done }()
	return m.pollStream()
}

func (m Model) buildLLMMessages() []llm.Message {
	md := mode.Get(m.modeName)
	base := m.cfg.Agent.SystemPrompt + md.SystemAppend
	msgs := []llm.Message{{Role: "system", Content: base}}
	for _, mm := range m.messages {
		if mm.Role == "system" && (strings.HasPrefix(mm.Content, "🧰") || strings.HasPrefix(mm.Content, "⏸️") || strings.HasPrefix(mm.Content, "✅") || strings.HasPrefix(mm.Content, "⏭️")) {
			continue
		}
		if mm.Role == "tool" {
			msgs = append(msgs, llm.Message{Role: "user", Content: "[tool result]\n" + mm.Content})
			continue
		}
		if mm.Role == "assistant" && mm.Content == "" {
			continue
		}
		if mm.Role == "system" {
			msgs = append(msgs, llm.Message{Role: "user", Content: mm.Content})
			continue
		}
		msgs = append(msgs, llm.Message{Role: mm.Role, Content: mm.Content})
	}
	return msgs
}

func (m Model) pollStream() tea.Cmd {
	src := m.streamSrc
	return func() tea.Msg {
		chunk, ok := <-src
		if !ok {
			return doneMsg{err: nil}
		}
		return streamMsg{chunk: chunk}
	}
}

func (m *Model) syncViewport() {
	// viewport already sized; rendering happens in View via SetContent+GotoBottom on copy
	// so this is a no-op anchor to avoid nil panics before ready
	if !m.ready {
		return
	}
}

func (m Model) streamWithToolResult() tea.Cmd {
	m.streaming = true
	m.streamBuf = ""
	m.status = m.spinner.View() + " Processing tool result…"
	m.messages = append(m.messages, Msg{Role: "assistant", Content: ""})
	msgs := m.buildLLMMessages()
	ctx, cancel := context.WithCancel(context.Background())
	m.cancel = cancel
	client := m.client
	out := make(chan string, 64)
	done := make(chan error, 1)
	m.streamSrc = out
	go func() { client.Stream(ctx, msgs, out, done) }()
	go func() { <-done }()
	return m.pollStream()
}

func helpText() string {
	return "## ⌨️ BITTU AGENT ULTRA — Commands\n\n" +
		"- **Enter** send  •  **Shift+Enter** newline  •  **Ctrl+Q** quit  •  **!cmd** shell fast-path\n" +
		"- **Ctrl+K** palette  •  **Ctrl+P** files  •  **Ctrl+A** mode  •  **Ctrl+J** sidebar\n" +
		"- **Ctrl+S** save  •  **Ctrl+O** sessions  •  **Ctrl+R** reasoning  •  **Ctrl+M** model  •  **Ctrl+T** theme\n" +
		"- **@file** attach • **Ctrl+L** clear • **Ctrl+D** cancel • **Ctrl+Shift+C** compact\n\n" +
		"**Slash** `/help /clear /model /provider /mode /sessions /save /resume /export /compact /context /diff /review /fmt /test /run /tree /search /add /undo /status /tools /config /developer`\n\n" +
		"**Modes** build 🛠️ plan 🗺️ ask 💬 research 🔬 debug 🐛 review 🔭\n" +
		"**Tools** shell read_file write_file edit_file list_dir tree grep web_fetch git_status git_diff go_fmt go_test\n" +
		"Format: ```tool\\n shell: dir\\n```\n\n" +
		"Developer by **Bittu Chauhan** — MiniMax-M3.1-Flash • 1M context • 16k max output • ULTRA v2."
}

func mask(s string) string {
	if len(s) <= 8 {
		return "••••"
	}
	return s[:4] + "••••" + s[len(s)-4:]
}

func (m Model) View() string {
	if !m.ready {
		return "initializing BITTU AGENT ULTRA…"
	}
	w := m.width
	th := m.theme

	var b strings.Builder

	// ── Header — Devin style: clean, single line ──
	title := lipgloss.NewStyle().Foreground(lipgloss.Color("#FFFFFF")).Bold(true).Render("BITTU AGENT")
	credit := lipgloss.NewStyle().Foreground(th.Muted).Render(" · Developer by Bittu Chauhan")
	modeTxt := lipgloss.NewStyle().Foreground(th.Muted).Render(mode.Get(m.modeName).Icon + " " + m.modeName)
	modelTxt := lipgloss.NewStyle().Foreground(th.Muted).Render(m.client.Model)
	statusDot := lipgloss.NewStyle().Foreground(colorForStatus(m.status)).Bold(true).Render(m.status)

	left := title + credit
	right := modelTxt + " · " + modeTxt + " · " + statusDot
	// push right to edge
	pad := max(1, w-2-lipgloss.Width(left)-lipgloss.Width(right))
	header := left + strings.Repeat(" ", pad) + right
	b.WriteString(header + "\n")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Border).Render(strings.Repeat("─", max(1, w-2))) + "\n")

	// ── Body: sidebar + messages ──
	body := m.renderMessages()
	m.viewport.SetContent(body)
	m.viewport.GotoBottom()
	mainView := m.viewport.View()

	if m.showSidebar {
		side := m.renderSidebar()
		combined := lipgloss.JoinHorizontal(lipgloss.Top, side, "  ", mainView)
		b.WriteString(combined + "\n")
	} else {
		b.WriteString(mainView + "\n")
	}

	// ── Overlays ──
	if m.showThemes {
		names := theme.Names()
		var row []string
		for _, n := range names {
			style := lipgloss.NewStyle().Foreground(th.Muted)
			if n == m.themeName {
				style = lipgloss.NewStyle().Bold(true).Foreground(th.Primary)
			}
			row = append(row, style.Render("["+n+"]"))
		}
		b.WriteString(lipgloss.NewStyle().Foreground(th.Muted).Render(" 🎨 ") + strings.Join(row, "  ") + "   /theme <name>\n")
	}
	if m.showPalette {
		b.WriteString(m.renderPalette() + "\n")
	}
	if m.showFiles {
		b.WriteString(m.renderFiles() + "\n")
	}
	if m.pendingAsk {
		b.WriteString(lipgloss.NewStyle().Foreground(th.Accent).Bold(true).Render("⏸️ approval: type y/n + Enter") + "\n")
	}

	// ── Double-line prompt — Devin/Claude style ──
	b.WriteString(m.renderPrompt() + "\n")

	// ── Footer — single minimal line ──
	used := m.promptTokens + m.completionTokens
	ctw := m.cfg.ContextWindow()
	pct := 0
	if ctw > 0 {
		pct = used * 100 / ctw
		if pct > 100 {
			pct = 100
		}
	}
	bar := contextBar(pct, 12, th)
	parts := []string{
		fmt.Sprintf("%s %d in · %d out", bar, m.promptTokens, m.completionTokens),
		fmt.Sprintf("%d msgs", len(m.messages)),
	}
	if m.cfg.TUI.ShowGit && m.gitBranch != "" {
		parts = append(parts, "⎇ "+m.gitBranch)
	}
	if m.cfg.TUI.ShowFPS {
		parts = append(parts, fmt.Sprintf("%dfps %.1ft/s", m.fps, m.tokensPerSec))
	}
	foot := "  " + strings.Join(parts, "  ·  ")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Muted).Render(foot))
	if m.statusErr != "" {
		b.WriteString("\n" + lipgloss.NewStyle().Foreground(lipgloss.Color("#FF5A5A")).Render("  ⚠ "+m.statusErr))
	}
	if len(m.attachedFiles) > 0 {
		b.WriteString("\n" + lipgloss.NewStyle().Foreground(th.Accent).Render("  📎 @"+strings.Join(m.attachedFiles, ", ")))
	}

	return b.String()
}

func colorForStatus(s string) lipgloss.Color {
	if strings.Contains(s, "Ready") {
		return lipgloss.Color("#34D399")
	}
	if strings.Contains(s, "Think") || strings.Contains(s, "Stream") || strings.Contains(s, "Tool") {
		return lipgloss.Color("#FBBF24")
	}
	if strings.Contains(s, "Approval") {
		return lipgloss.Color("#FB923C")
	}
	if strings.Contains(s, "Error") || strings.Contains(s, "⚠") {
		return lipgloss.Color("#F87171")
	}
	return lipgloss.Color("#8B9DFF")
}

func animatedLine(w, frame int, th theme.Theme) string {
	if w <= 4 {
		return ""
	}
	return lipgloss.NewStyle().Foreground(th.Border).Render(strings.Repeat("─", w-2))
}

func contextBar(pct int, width int, th theme.Theme) string {
	filled := pct * width / 100
	empty := width - filled
	if filled < 0 {
		filled = 0
	}
	f := lipgloss.NewStyle().Foreground(th.Accent).Bold(true).Render(strings.Repeat("▰", filled))
	e := lipgloss.NewStyle().Foreground(th.Muted).Render(strings.Repeat("▱", empty))
	return f + e + fmt.Sprintf(" %d%% ctx", pct)
}

func (m Model) renderMessages() string {
	var parts []string
	for _, mm := range m.messages {
		parts = append(parts, m.renderMsg(mm))
	}
	return strings.Join(parts, "\n\n")
}

func (m Model) renderMsg(mm Msg) string {
	th := m.theme
	w := max(1, m.width-4-boolToInt(m.showSidebar)*(m.cfg.TUI.SidebarWidth+4))
	switch mm.Role {
	case "user":
		inner := wrapText(mm.Content, w-4)
		label := lipgloss.NewStyle().Foreground(th.Muted).Render("You")
		body := lipgloss.NewStyle().Foreground(lipgloss.Color("#FFFFFF")).Render(inner)
		return lipgloss.NewStyle().
			Background(lipgloss.Color("#14141A")).
			Padding(0, 1).
			Width(w).
			Render(label + "\n" + body)
	case "tool":
		return lipgloss.NewStyle().Foreground(th.Muted).
			PaddingLeft(1).
			Render("⚙ " + truncate(mm.Content, 1200))
	case "system":
		return lipgloss.NewStyle().Foreground(th.Muted).PaddingLeft(1).Render(mm.Content)
	default: // assistant
		md, err := renderMarkdown(mm.Content, m.width)
		if err != nil {
			md = mm.Content
		}
		curs := ""
		if m.streaming && mm.Content == m.streamBuf {
			if m.frame%2 == 0 {
				curs = lipgloss.NewStyle().Foreground(th.Accent).Render(" ▍")
			}
		}
		reason := ""
		if m.showReason && mm.Content == m.streamBuf && m.reasoningBuf != "" {
			reason = lipgloss.NewStyle().Foreground(th.Muted).Italic(true).Render("💭 " + truncate(m.reasoningBuf, 500) + "\n")
		}
		label := lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render("Bittu")
		body := lipgloss.NewStyle().Foreground(lipgloss.Color("#FFFFFF")).Render(md + curs)
		return lipgloss.NewStyle().PaddingLeft(1).Render(label + "  " + reason + body)
	}
}

func renderMarkdown(s string, width int) (string, error) {
	if width < 20 {
		width = 80
	}
	mw := width - 10
	if mw < 20 {
		mw = 68
	}
	r, err := glamour.NewTermRenderer(
		glamour.WithStandardStyle("dark"),
		glamour.WithWordWrap(mw),
		glamour.WithStylesFromJSONBytes([]byte(`{"document":{"margin":0},"text":{"color":"#FFFFFF"}}`)),
	)
	if err != nil {
		r, err = glamour.NewTermRenderer(glamour.WithStandardStyle("dark"), glamour.WithWordWrap(mw))
		if err != nil {
			return s, err
		}
	}
	return r.Render(s)
}

func wrapText(s string, width int) string {
	if width <= 0 {
		return s
	}
	var out []string
	for _, line := range strings.Split(s, "\n") {
		words := strings.Fields(line)
		if len(words) == 0 {
			out = append(out, "")
			continue
		}
		cur := ""
		for _, wd := range words {
			if len(cur)+len(wd)+1 > width {
				out = append(out, cur)
				cur = wd
			} else {
				if cur == "" {
					cur = wd
				} else {
					cur += " " + wd
				}
			}
		}
		out = append(out, cur)
	}
	return strings.Join(out, "\n")
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "\n…[truncated]"
}

func (m Model) renderSidebar() string {
	th := m.theme
	sw := m.cfg.TUI.SidebarWidth
	if sw < 20 {
		sw = 26
	}
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render("┌─ SESSION") + strings.Repeat("─", sw-10) + "\n")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Muted).Render(fmt.Sprintf("│ %s\n", m.sessionID)) + "│ " + lipgloss.NewStyle().Foreground(th.Secondary).Render(m.client.Model) + "\n")
	b.WriteString("│ " + lipgloss.NewStyle().Foreground(th.Accent).Render(mode.Get(m.modeName).Icon+" "+m.modeName) + "\n")
	b.WriteString("│ Provider: " + lipgloss.NewStyle().Foreground(th.Primary).Render(m.provider) + "\n")
	b.WriteString("│ Branch: " + lipgloss.NewStyle().Foreground(th.Muted).Render(m.gitBranch) + "\n")
	b.WriteString("├" + strings.Repeat("─", sw-2) + "\n")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render("│ CONTEXT") + "\n")
	b.WriteString("│ in: " + fmt.Sprintf("%d", m.promptTokens) + " out: " + fmt.Sprintf("%d", m.completionTokens) + "\n")
	b.WriteString("│ ctx: " + contextBar(m.contextPct(), 10, th) + "\n")
	b.WriteString("│ msgs: " + fmt.Sprintf("%d", len(m.messages)) + " tools: " + fmt.Sprintf("%d", m.toolCalls) + "\n")
	if len(m.attachedFiles) > 0 {
		b.WriteString(lipgloss.NewStyle().Foreground(th.Accent).Render("│ 📎 @files") + "\n")
		for _, f := range m.attachedFiles {
			b.WriteString("│  " + truncate(filepath.Base(f), sw-6) + "\n")
		}
	} else {
		b.WriteString("│  (no @files — Ctrl+P)\n")
	}
	b.WriteString("├" + strings.Repeat("─", sw-2) + "\n")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render("│ MODES (Ctrl+A)") + "\n")
	for _, mm := range mode.All {
		mark := " "
		if mm.Name == m.modeName {
			mark = "●"
		}
		b.WriteString(fmt.Sprintf("│ %s %s %s\n", mark, mm.Name, mm.Icon))
	}
	b.WriteString("├" + strings.Repeat("─", sw-2) + "\n")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render("│ PERF") + "\n")
	b.WriteString(fmt.Sprintf("│ %dfps %.0fms %.1ft/s\n", m.fps, float64(m.latencyMs), m.tokensPerSec))
	b.WriteString("│ Ctrl+J toggle\n")
	b.WriteString(lipgloss.NewStyle().Foreground(th.Muted).Render("└" + strings.Repeat("─", sw-2)))
	return lipgloss.NewStyle().Width(sw).Render(b.String())
}

func (m Model) renderPalette() string {
	th := m.theme
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render("⌕ Command Palette — " + m.paletteQuery) + "\n")
	limit := 10
	if len(m.paletteItems) < limit {
		limit = len(m.paletteItems)
	}
	for i := 0; i < limit; i++ {
		it := m.paletteItems[i]
		mark := "  "
		style := lipgloss.NewStyle().Foreground(th.Muted)
		if i == m.paletteSel {
			mark = "▸ "
			style = lipgloss.NewStyle().Foreground(th.Primary).Bold(true)
		}
		b.WriteString(style.Render(mark + it.Title + "  ·  " + it.Hint) + "\n")
	}
	b.WriteString(lipgloss.NewStyle().Foreground(th.Muted).Render("↑↓ navigate • Enter run • Esc close"))
	return lipgloss.NewStyle().Border(lipgloss.NormalBorder()).BorderForeground(th.Primary).Padding(0, 1).Render(b.String())
}

func (m Model) renderFiles() string {
	th := m.theme
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Foreground(th.Secondary).Bold(true).Render("⌕ Files — ↑↓ Enter to attach") + "\n")
	limit := 10
	if len(m.fileItems) < limit {
		limit = len(m.fileItems)
	}
	for i := 0; i < limit; i++ {
		f := m.fileItems[i]
		mark := "  "
		style := lipgloss.NewStyle().Foreground(th.Muted)
		if i == m.fileSel {
			mark = "▸ "
			style = lipgloss.NewStyle().Foreground(lipgloss.Color("#FFFFFF")).Bold(true)
		}
		b.WriteString(style.Render(mark + f) + "\n")
	}
	b.WriteString(lipgloss.NewStyle().Foreground(th.Muted).Render("Enter attach @file • Esc close"))
	return lipgloss.NewStyle().Border(lipgloss.NormalBorder()).BorderForeground(th.Secondary).Padding(0, 1).Render(b.String())
}

func (m Model) renderPrompt() string {
	th := m.theme
	w := max(1, m.width-4)

	// Devin/Claude style: subtle top info + true DoubleBorder box
	topInfo := lipgloss.NewStyle().Foreground(th.Muted).Render(
		fmt.Sprintf("  %s %s  ·  %s  ·  %s", mode.Get(m.modeName).Icon, m.modeName, m.client.Model, m.provider),
	)
	if m.streaming {
		topInfo = lipgloss.NewStyle().Foreground(th.Accent).Render("  " + m.spinner.View() + " " + m.status)
	}

	borderColor := th.Border
	if m.streaming {
		borderColor = th.Accent
	}

	// Inside: textarea + thin divider + hint bar (Devin bottom bar)
	inputView := m.input.View()
	divider := lipgloss.NewStyle().Foreground(th.Border).Render(strings.Repeat("─", max(1, w-10)))
	hintsLeft := lipgloss.NewStyle().Foreground(th.Primary).Render("build") + lipgloss.NewStyle().Foreground(th.Muted).Render("  ·  ")
	// mode pill left
	modePill := lipgloss.NewStyle().Foreground(th.Primary).Bold(true).Render(mode.Get(m.modeName).Icon + " " + m.modeName)
	hintsRight := lipgloss.NewStyle().Foreground(th.Muted).Render("Enter ↵ send · Shift+Enter newline · Ctrl+K palette · Ctrl+P files · ! shell")
	_ = hintsLeft // kept for future
	bottom := modePill + lipgloss.NewStyle().Foreground(th.Muted).Render("   │   ") + hintsRight

	inner := inputView + "\n" + divider + "\n" + bottom

	box := lipgloss.NewStyle().
		Border(lipgloss.DoubleBorder()).
		BorderForeground(borderColor).
		Padding(0, 1).
		Width(w - 4).
		Background(lipgloss.Color("#111116")).
		Render(inner)

	return topInfo + "\n" + box
}

func animatedSubLine(w, frame int, th theme.Theme) string {
	return ""
}
