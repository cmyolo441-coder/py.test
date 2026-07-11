"""Color tools: hex/rgb/hsl conversion and palette generation."""

from __future__ import annotations

import colorsys

from .base import Tool, ToolResult


def _clamp(v: int) -> int:
    return max(0, min(255, v))


def hex_to_rgb(hex_color: str) -> ToolResult:
    h = hex_color.lstrip("#")
    if len(h) not in (3, 6):
        return ToolResult(output="Invalid hex color", success=False)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return ToolResult(output="Invalid hex color", success=False)
    return ToolResult(output=f"rgb({r}, {g}, {b})")


def rgb_to_hex(r: int, g: int, b: int) -> ToolResult:
    return ToolResult(output=f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}")


def rgb_to_hsl(r: int, g: int, b: int) -> ToolResult:
    h, l_val, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return ToolResult(output=f"hsl({h*360:.0f}, {s*100:.0f}%, {l_val*100:.0f}%)")


def gradient_palette(start: str, end: str, steps: int = 5) -> ToolResult:
    steps = max(2, min(steps, 32))
    s = start.lstrip("#")
    e = end.lstrip("#")
    try:
        sr, sg, sb = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        er, eg, eb = int(e[0:2], 16), int(e[2:4], 16), int(e[4:6], 16)
    except ValueError:
        return ToolResult(output="Invalid hex color(s)", success=False)
    out = []
    for i in range(steps):
        t = i / (steps - 1)
        r = int(sr + (er - sr) * t)
        g = int(sg + (eg - sg) * t)
        b = int(sb + (eb - sb) * t)
        out.append(f"#{r:02x}{g:02x}{b:02x}")
    return ToolResult(output=" ".join(out))


def get_color_tools() -> list[Tool]:
    rgb_schema = {
        "type": "object",
        "properties": {"r": {"type": "integer"}, "g": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["r", "g", "b"],
    }
    return [
        Tool("hex_to_rgb", "Convert a hex color (#rrggbb) to rgb().",
             {"type": "object", "properties": {"hex_color": {"type": "string"}}, "required": ["hex_color"]}, hex_to_rgb),
        Tool("rgb_to_hex", "Convert r,g,b integers to a hex color.", rgb_schema, rgb_to_hex),
        Tool("rgb_to_hsl", "Convert r,g,b integers to hsl().", rgb_schema, rgb_to_hsl),
        Tool("gradient_palette", "Generate N hex colors between two hex endpoints.",
             {"type": "object", "properties": {
                 "start": {"type": "string"}, "end": {"type": "string"},
                 "steps": {"type": "integer", "default": 5}}, "required": ["start", "end"]}, gradient_palette),
    ]
