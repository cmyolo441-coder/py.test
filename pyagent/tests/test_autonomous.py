from agent.effort import get_effort, list_efforts
from agent.fake_detector import is_real, scan_text


def test_effort_levels():
    assert "ultrahype" in list_efforts()
    assert get_effort("ultrahype").max_execution_rounds == 40
    assert get_effort("unknown").name == "normal"
    assert get_effort("ultracombo").verification_passes == 2


def test_detect_fake():
    fake = "def foo():\n    raise NotImplementedError  # TODO\n"
    findings = scan_text(fake)
    assert findings
    assert not is_real(fake)


def test_detect_real():
    real = "def add(a, b):\n    return a + b\n"
    assert is_real(real)


def test_placeholder_words():
    assert scan_text("# placeholder for later")
    assert scan_text("return 'example'")


def test_test_file_allows_mock():
    text = "mock_client = create_mock()\n"
    assert scan_text(text, is_test_file=True) == []
