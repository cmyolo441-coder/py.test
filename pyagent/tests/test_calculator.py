from agent.plugins.example_calculator import calculate


def test_basic():
    assert calculate("2 + 3") == "5"


def test_precedence():
    assert calculate("2 * (3 + 4)") == "14"


def test_power():
    assert calculate("2 ** 8") == "256"


def test_unsafe_rejected():
    assert "Error" in calculate("__import__('os')")
