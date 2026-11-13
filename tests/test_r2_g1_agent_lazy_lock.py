import os
import tempfile
import threading
import time

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import agent as agent_mod  # noqa: E402


def test_lazy_subsystem_built_once_under_concurrency(monkeypatch):
    """Agent.__getattr__ used `with threading.Lock():` (a fresh lock per
    call), so concurrent first accesses each ran the factory."""
    calls = []

    def slow_factory(self):
        calls.append(1)
        time.sleep(0.05)
        return object()

    factories = dict(agent_mod.Agent._SUBSYSTEM_FACTORIES)
    factories["_r2_probe"] = slow_factory
    monkeypatch.setattr(agent_mod.Agent, "_SUBSYSTEM_FACTORIES", factories)
    monkeypatch.setattr(agent_mod, "_ensure_submodules", lambda: None)

    inst = object.__new__(agent_mod.Agent)
    results = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        results.append(inst._r2_probe)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(r is results[0] for r in results)


def test_lazy_factory_may_touch_other_lazy_subsystems(monkeypatch):
    factories = dict(agent_mod.Agent._SUBSYSTEM_FACTORIES)
    factories["_r2_inner"] = lambda self: "inner"
    factories["_r2_outer"] = lambda self: "outer+" + self._r2_inner
    monkeypatch.setattr(agent_mod.Agent, "_SUBSYSTEM_FACTORIES", factories)
    monkeypatch.setattr(agent_mod, "_ensure_submodules", lambda: None)
    inst = object.__new__(agent_mod.Agent)
    assert inst._r2_outer == "outer+inner"
