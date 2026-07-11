from agent.sandbox import run_python


def test_run_python_output():
    assert "4" in run_python("print(2 + 2)")


def test_run_python_blocks_open():
    out = run_python("open('/etc/passwd')")
    assert "Error" in out or "not allowed" in out or "NoneType" in out


def test_run_python_blocks_import():
    out = run_python("import os")
    assert "not allowed" in out or "Error" in out
