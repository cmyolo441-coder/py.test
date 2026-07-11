from agent.health import CheckResult, HealthMonitor, disk_space_check


def test_healthy():
    mon = HealthMonitor()
    mon.register("ok", lambda: CheckResult("ok", True))
    result = mon.run()
    assert result["status"] == "healthy"


def test_unhealthy():
    mon = HealthMonitor()
    mon.register("bad", lambda: CheckResult("bad", False, "down"))
    result = mon.run()
    assert result["status"] == "unhealthy"


def test_disk_check():
    result = disk_space_check(min_free_mb=0)
    assert result.healthy
