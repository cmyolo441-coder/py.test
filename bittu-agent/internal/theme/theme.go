// Package theme — BITTU AGENT multi-theme engine
// Developer by Bittu Chauhan
package theme

import (
	"github.com/charmbracelet/lipgloss"
)

type Theme struct {
	Name         string
	Primary      lipgloss.Color
	Secondary    lipgloss.Color
	Accent       lipgloss.Color
	Muted        lipgloss.Color
	Border       lipgloss.Color
	UserBorder   lipgloss.Color
	AiBorder     lipgloss.Color
	Gradient     []lipgloss.Color
	Bg           lipgloss.Color
}

var Themes = map[string]Theme{
	"devin": {
		Name: "Devin Slate", Primary: "#8B9DFF", Secondary: "#A78BFA", Accent: "#34D399",
		Muted: "#8A93A6", Border: "#3A3A42", UserBorder: "#8B9DFF", AiBorder: "#A78BFA",
		Gradient: []lipgloss.Color{"#8B9DFF", "#A78BFA", "#34D399"}, Bg: "#0A0A0C",
	},
	"neon": {
		Name: "Neon Pulse", Primary: "#00F5FF", Secondary: "#FF00E5", Accent: "#A6FF00",
		Muted: "#8A8D93", Border: "#00F5FF", UserBorder: "#00F5FF", AiBorder: "#FF00E5",
		Gradient: []lipgloss.Color{"#00F5FF", "#7C3AED", "#FF00E5", "#00FF9D"}, Bg: "#0B0B12",
	},
	"matrix": {
		Name: "Matrix", Primary: "#00FF41", Secondary: "#00E5FF", Accent: "#BFFF00",
		Muted: "#3DFF7A", Border: "#00FF41", UserBorder: "#00FF41", AiBorder: "#00E5FF",
		Gradient: []lipgloss.Color{"#00FF41", "#00E5FF", "#BFFF00"}, Bg: "#020A06",
	},
	"sunset": {
		Name: "Sunset Glow", Primary: "#FF6B35", Secondary: "#FF2E63", Accent: "#FFD23F",
		Muted: "#C9A8A8", Border: "#FF6B35", UserBorder: "#FF6B35", AiBorder: "#FF2E63",
		Gradient: []lipgloss.Color{"#FF6B35", "#FF2E63", "#FFD23F"}, Bg: "#150A0F",
	},
	"ocean": {
		Name: "Deep Ocean", Primary: "#00B4FF", Secondary: "#00FFD1", Accent: "#7C4DFF",
		Muted: "#7A9BB8", Border: "#00B4FF", UserBorder: "#00B4FF", AiBorder: "#00FFD1",
		Gradient: []lipgloss.Color{"#00B4FF", "#00FFD1", "#7C4DFF"}, Bg: "#040B14",
	},
	"midnight": {
		Name: "Midnight Violet", Primary: "#9D4EDD", Secondary: "#FF5DA2", Accent: "#00E5FF",
		Muted: "#9E9EB8", Border: "#9D4EDD", UserBorder: "#9D4EDD", AiBorder: "#FF5DA2",
		Gradient: []lipgloss.Color{"#9D4EDD", "#FF5DA2", "#00E5FF"}, Bg: "#0D0A1A",
	},
}

func Names() []string {
	return []string{"devin", "neon", "matrix", "sunset", "ocean", "midnight"}
}

func Get(name string) Theme {
	if t, ok := Themes[name]; ok {
		return t
	}
	return Themes["devin"]
}

// GradientText animates a string with flowing gradient colors.
func GradientText(s string, frame int, grad []lipgloss.Color) string {
	if len(grad) == 0 {
		return lipgloss.NewStyle().Foreground(lipgloss.Color("#00F5FF")).Bold(true).Render(s)
	}
	out := ""
	chars := []rune(s)
	for i, ch := range chars {
		idx := (i*2 + frame) % (len(grad) * 2)
		c := grad[idx/2]
		if idx%2 == 1 {
			c = grad[(idx/2+1)%len(grad)]
		}
		out += lipgloss.NewStyle().Foreground(c).Bold(true).Render(string(ch))
	}
	return out
}
