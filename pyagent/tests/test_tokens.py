from agent.tokens import UsageTracker, count_tokens, estimate_cost


def test_count_tokens_nonempty():
    assert count_tokens("hello world") > 0


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_estimate_cost_known_model():
    cost = estimate_cost("gpt-4o", 1000, 1000)
    assert cost == 0.005 + 0.015


def test_estimate_cost_unknown_model():
    assert estimate_cost("nonexistent", 1000, 1000) == 0.0


def test_usage_tracker_accumulates():
    tracker = UsageTracker(model="gpt-4o")
    tracker.record(1000, 500)
    tracker.record(1000, 500)
    assert tracker.total_input == 2000
    assert tracker.total_output == 1000
    assert tracker.turns == 2
    assert tracker.total_cost > 0
