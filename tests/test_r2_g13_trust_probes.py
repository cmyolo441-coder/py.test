import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent.enforcex.probes import generate
from fullagent.enforcex.trust import TrustEvent, TrustLedger


def test_clean_event_never_lowers_score_after_upward_adjust():
    led = TrustLedger()
    for _ in range(2):
        led.record("a", TrustEvent(kind="violation", severity="critical"))
    st = led.record("a", TrustEvent(kind="adjust", delta=100))
    assert st.score == 100.0
    st2 = led.record("a", TrustEvent(kind="clean"))
    assert st2.score >= st.score


def test_new_session_min_score_is_initial_score():
    led = TrustLedger(initial_score=20, recovery_cap=10)
    st = None
    for _ in range(30):
        st = led.record("b", {"kind": "clean"})
    # recovery cap applies from the very first interaction, same as
    # after an explicit reset
    assert st.score <= 30.0
    assert led.state("b").min_score == 20.0


def test_probes_malformed_keywords_do_not_raise():
    probes = generate([{"id": "R1", "text": "never do x", "keywords": 5}])
    assert probes
    assert all(p.rule_id == "R1" for p in probes)
