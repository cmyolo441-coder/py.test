from agent.effects import gradient_text, rainbow_text, spinner_frames, SPINNERS
from agent.imagery import brightness_to_char, sparkline
from agent.vivid_themes import get_vivid, list_vivid, set_vivid


def test_gradient_length():
    t = gradient_text("hello")
    assert len(t.plain) == 5


def test_rainbow_length():
    t = rainbow_text("world")
    assert t.plain == "world"


def test_spinner_frames():
    assert len(spinner_frames("dots")) > 0
    assert "moon" in SPINNERS


def test_brightness_char():
    assert brightness_to_char(0.0) == " "
    assert brightness_to_char(1.0) == "@"


def test_sparkline():
    s = sparkline([1, 2, 3, 4])
    assert len(s) == 4
    assert sparkline([]) == ""


def test_vivid_themes():
    assert "cyberpunk" in list_vivid()
    assert set_vivid("neon")
    assert get_vivid().name == "neon"
    assert not set_vivid("nope")
