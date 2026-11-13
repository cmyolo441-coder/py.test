import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

from fullagent import cronsched, smartretry  # noqa: E402
from fullagent.goal import GoalContract, GoalContractError  # noqa: E402
from fullagent.memory import _safe_int  # noqa: E402


def test_cron_leap_day_more_than_a_year_away():
    assert cronsched.next_run_after("0 0 29 2 *", datetime(2026, 11, 13)) \
        == datetime(2028, 2, 29)
    assert cronsched.next_run_after("0 0 29 2 *", datetime(2096, 3, 1)) \
        == datetime(2104, 2, 29)


def test_cron_id_and_limit_coercion_never_raise():
    assert cronsched._coerce_cron_id(float("inf")) is None
    assert cronsched._coerce_cron_id(10 ** 30) is None
    assert cronsched._coerce_cron_id("7") == 7


def test_cron_runs_inf_limit(tmp_path):
    s = cronsched.CronScheduler(tmp_path / "c.db")
    try:
        assert s.runs(1, float("inf")) == []
    finally:
        s.close()


def test_smartretry_nul_byte_and_inf_attempts():
    out = smartretry._handle_retry_run(command=["echo", "a\x00b"],
                                       max_attempts=1)
    assert "invalid command" in out
    out = smartretry._handle_retry_run(command="true",
                                       max_attempts=float("inf"))
    assert out.startswith("ERROR")


def test_memory_safe_int_inf():
    assert _safe_int(float("inf")) == 0


@pytest.mark.parametrize("w", [float("nan"), float("inf"), "nan"])
def test_goal_rejects_non_finite_weight(w):
    with pytest.raises(GoalContractError):
        GoalContract.validate(
            [{"text": "x", "weight": w, "proof": {"type": "file_exists"}}],
            [], [], "ALL")
