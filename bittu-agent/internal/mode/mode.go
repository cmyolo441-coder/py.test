// Package mode — BITTU AGENT agent modes
// Developer by Bittu Chauhan
package mode

import "strings"

type Mode struct {
	Name         string
	Icon         string
	Description  string
	SystemAppend string
	ToolPolicy   string // auto | ask | plan-first | read-only
	Temp         float64
}

var All = []Mode{
	{
		Name: "build", Icon: "🛠️", Description: "Default — full tools, auto-apply",
		SystemAppend: "\n\nMODE=BUILD. You may edit files and run shell without asking. Prefer executing tools over guessing. Show file diffs after changes.",
		ToolPolicy: "auto", Temp: 0.7,
	},
	{
		Name: "plan", Icon: "🗺️", Description: "Plan first — checklist, no edits until approved",
		SystemAppend: "\n\nMODE=PLAN. Do NOT edit files or run mutating shell yet. Output a numbered plan with files to change, commands to run, and risks. End with 'Approve? (y/n)' and wait.",
		ToolPolicy: "plan-first", Temp: 0.3,
	},
	{
		Name: "ask", Icon: "💬", Description: "Ask only — read-only, best for questions",
		SystemAppend: "\n\nMODE=ASK. Read-only. Use read_file/list_dir/grep/web_fetch only. Never write_file or shell mutating commands. Explain clearly.",
		ToolPolicy: "read-only", Temp: 0.6,
	},
	{
		Name: "research", Icon: "🔬", Description: "Research — web + grep + summarize",
		SystemAppend: "\n\nMODE=RESEARCH. Use web_fetch + grep + read_file extensively. Synthesize findings with citations and a TL;DR.",
		ToolPolicy: "auto", Temp: 0.4,
	},
	{
		Name: "debug", Icon: "🐛", Description: "Debug — reproduce, logs, fix",
		SystemAppend: "\n\nMODE=DEBUG. Reproduce first (run commands), show logs, bisect cause, propose minimal fix, then verify with tests.",
		ToolPolicy: "auto", Temp: 0.5,
	},
	{
		Name: "review", Icon: "🔭", Description: "Review — audit code, no edits",
		SystemAppend: "\n\nMODE=REVIEW. Read-only audit. Output findings by severity (Critical/Major/Minor), with file:line and suggested fix. Do not edit.",
		ToolPolicy: "read-only", Temp: 0.4,
	},
}

func Names() []string {
	var out []string
	for _, m := range All {
		out = append(out, m.Name)
	}
	return out
}

func Get(name string) Mode {
	name = strings.ToLower(strings.TrimSpace(name))
	for _, m := range All {
		if m.Name == name {
			return m
		}
	}
	return All[0]
}

func Next(cur string) string {
	names := Names()
	for i, n := range names {
		if n == cur {
			return names[(i+1)%len(names)]
		}
	}
	return names[0]
}
