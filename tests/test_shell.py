from agent.shell import ShellExecutor


def test_basic_command():
    ex = ShellExecutor()
    result = ex.run("echo hello")
    assert "hello" in result.as_text()
    assert result.returncode == 0


def test_dangerous_blocked():
    ex = ShellExecutor(allow_dangerous=False)
    result = ex.run("rm -rf /")
    assert result.blocked
    assert "BLOCKED" in result.as_text()


def test_dangerous_approved():
    ex = ShellExecutor(approval_callback=lambda cmd: True)
    assert ex.is_dangerous("rm -rf tmp")


def test_audit_records():
    ex = ShellExecutor()
    ex.run("echo 1")
    ex.run("echo 2")
    assert len(ex.audit) == 2
