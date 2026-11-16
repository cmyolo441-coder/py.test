// Package session — BITTU AGENT persistent sessions
// Developer by Bittu Chauhan
package session

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type Entry struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
	Model     string    `json:"model"`
	Theme     string    `json:"theme"`
	Mode      string    `json:"mode"`
	Messages  []Message `json:"messages"`
	TokensIn  int       `json:"tokens_in"`
	TokensOut int       `json:"tokens_out"`
}

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

func Dir() string {
	home, _ := os.UserHomeDir()
	if home == "" {
		home = "."
	}
	return filepath.Join(home, ".bittu-agent", "sessions")
}

func EnsureDir() error {
	return os.MkdirAll(Dir(), 0o755)
}

func NewID() string {
	return time.Now().Format("20060102-150405") + "-" + fmt.Sprintf("%d", time.Now().UnixNano()%100000)
}

func Save(e *Entry) (string, error) {
	if err := EnsureDir(); err != nil {
		return "", err
	}
	if e.ID == "" {
		e.ID = NewID()
	}
	e.UpdatedAt = time.Now()
	path := filepath.Join(Dir(), e.ID+".json")
	b, err := json.MarshalIndent(e, "", "  ")
	if err != nil {
		return "", err
	}
	// also append JSONL chat log
	return path, os.WriteFile(path, b, 0o644)
}

func AppendLog(e Entry) error {
	if err := EnsureDir(); err != nil {
		return err
	}
	// writes a compact .jsonl mirroring messages for grepability
	p := filepath.Join(Dir(), e.ID+".jsonl")
	f, err := os.OpenFile(p, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	defer w.Flush()
	for _, m := range e.Messages {
		b, _ := json.Marshal(map[string]string{"role": m.Role, "content": m.Content})
		w.Write(b)
		w.WriteByte('\n')
	}
	return nil
}

func List(limit int) ([]Entry, error) {
	if err := EnsureDir(); err != nil {
		return nil, err
	}
	ents, err := os.ReadDir(Dir())
	if err != nil {
		return nil, err
	}
	var out []Entry
	for _, e := range ents {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		b, err := os.ReadFile(filepath.Join(Dir(), e.Name()))
		if err != nil {
			continue
		}
		var en Entry
		if err := json.Unmarshal(b, &en); err != nil {
			continue
		}
		out = append(out, en)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].UpdatedAt.After(out[j].UpdatedAt) })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func Load(id string) (*Entry, error) {
	b, err := os.ReadFile(filepath.Join(Dir(), id+".json"))
	if err != nil {
		return nil, err
	}
	var e Entry
	if err := json.Unmarshal(b, &e); err != nil {
		return nil, err
	}
	return &e, nil
}

func Export(id, dst string) error {
	e, err := Load(id)
	if err != nil {
		return err
	}
	f, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	defer w.Flush()
	fmt.Fprintf(w, "# BITTU AGENT session %s\n# %s | %s | %s\n# Developer by Bittu Chauhan\n\n", e.ID, e.Model, e.Theme, e.Mode)
	for _, m := range e.Messages {
		fmt.Fprintf(w, "## %s\n\n%s\n\n", strings.ToUpper(m.Role), m.Content)
	}
	return nil
}
