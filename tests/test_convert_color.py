"""Tests for conversion and color tools."""

from __future__ import annotations

from agent.tools.convert_tools import convert_length, convert_temp, convert_data
from agent.tools.color_tools import hex_to_rgb, rgb_to_hex, gradient_palette


def test_convert_length():
    assert convert_length(1000, "m", "km").output == "1000 m = 1 km"


def test_convert_length_invalid():
    assert convert_length(1, "m", "xyz").success is False


def test_convert_temp_c_to_f():
    assert convert_temp(100, "C", "F").output == "100C = 212F"


def test_convert_temp_k():
    assert convert_temp(0, "C", "K").output == "0C = 273.15K"


def test_convert_data():
    assert convert_data(1, "kb", "b").output == "1 kb = 1024 b"


def test_hex_to_rgb():
    assert hex_to_rgb("#ff0000").output == "rgb(255, 0, 0)"


def test_hex_to_rgb_short():
    assert hex_to_rgb("#f00").output == "rgb(255, 0, 0)"


def test_rgb_to_hex():
    assert rgb_to_hex(255, 0, 0).output == "#ff0000"


def test_rgb_to_hex_clamps():
    assert rgb_to_hex(300, -5, 0).output == "#ff0000"


def test_gradient_palette_steps():
    res = gradient_palette("#000000", "#ffffff", 3)
    colors = res.output.split()
    assert len(colors) == 3
    assert colors[0] == "#000000"
    assert colors[-1] == "#ffffff"
