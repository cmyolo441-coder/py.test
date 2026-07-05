from agent.metrics import MetricsRegistry, Timer


def test_counter():
    m = MetricsRegistry()
    m.incr("requests")
    m.incr("requests", 2)
    assert m.snapshot()["counters"]["requests"] == 3


def test_histogram():
    m = MetricsRegistry()
    m.observe("latency", 1.0)
    m.observe("latency", 3.0)
    hist = m.snapshot()["histograms"]["latency"]
    assert hist["count"] == 2
    assert hist["avg"] == 2.0


def test_timer():
    m = MetricsRegistry()
    with Timer(m, "op"):
        pass
    assert m.snapshot()["histograms"]["op"]["count"] == 1
