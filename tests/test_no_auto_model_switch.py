"""A provider error must never move a turn to a different model."""
from importlib import import_module
from types import SimpleNamespace

import pytest

from fullagent import config
from fullagent.client import APIError

agent_module = import_module("fullagent.agent")


def test_provider_outage_does_not_auto_switch_or_retry_another_model(
        monkeypatch):
    agent_module._resolve_lazy(
        "APIError", "chat_blocking", "chat_stream",
        "is_context_overflow", "schemas_tokens")

    model_ids = [model.id for model in config.MODELS]
    selected = config.DEFAULT_MODEL_ID
    assert selected in model_ids
    assert all(m.provider == "kios" for m in config.MODELS)
    alternate = "removed/provider-model"
    cfg = SimpleNamespace(
        model_id=selected,
        effort=config.DEFAULT_EFFORT,
        extra={"failover_model": alternate},
    )
    agent = agent_module.Agent.__new__(agent_module.Agent)
    agent.cfg = cfg
    agent.health = {"model_errors": {}, "failovers": 0}
    logged_events = []
    agent.log = SimpleNamespace(
        append=lambda *args, **kwargs: logged_events.append((args, kwargs)))
    agent.cassette = None
    agent._tool_schemas = lambda: []
    agent._prune_for_model = lambda reserved_tokens=0: []
    agent._overflow_shrink = lambda *args, **kwargs: None
    agent._flight_prewarmer = SimpleNamespace(launch=lambda _agent: None)
    agent._push_status = lambda _status: None
    calls = []

    def fail_with_outage(*args, **kwargs):
        calls.append((args, kwargs))
        raise APIError("provider unavailable", status=503)

    monkeypatch.setattr(agent_module, "chat_stream", fail_with_outage,
                        raising=False)

    with pytest.raises(APIError, match="provider unavailable"):
        agent._complete(lambda _text: None, lambda _text: None,
                        lambda _status: None)

    assert len(calls) == 1
    assert cfg.model_id == selected
    assert agent.health["model_errors"] == {selected: 1}
    assert agent.health["failovers"] == 0
    assert logged_events == []
