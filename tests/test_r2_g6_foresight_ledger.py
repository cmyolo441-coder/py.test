import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent.enforcex.foresight import normalise_event, OK_STATE  # noqa: E402
from fullagent.enforcex.ledger import LedgerEntry  # noqa: E402


def test_ledger_entry_payload_violation_is_used():
    le = LedgerEntry(kind="violation",
                     payload={"severity": "high", "kind": "leak_tag"})
    assert normalise_event(le) == "high|leak_tag"


def test_ledger_entry_dict_shape_payload_kind_is_used():
    le = LedgerEntry(kind="detection",
                     payload={"severity": "critical",
                              "kind": "forbidden_content"})
    assert normalise_event(le.to_dict()) == "critical|forbidden_content"


def test_top_level_violation_kind_still_wins():
    ev = {"severity": "low", "kind": "tone_style_drift",
          "payload": {"kind": "leak_tag"}}
    assert normalise_event(ev) == "low|tone_style_drift"
    assert normalise_event({"severity": "critical", "kind": "leak_tag",
                            "payload": {}}) == "critical|leak_tag"
    assert normalise_event(None) == OK_STATE
