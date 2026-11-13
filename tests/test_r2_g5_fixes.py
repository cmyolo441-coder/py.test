import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path

from fullagent.cancelguard import sleep_cancellable
from fullagent.enforcex.cipherlab import decode
from fullagent.kernel import EventLog
from fullagent.loopdetect import LoopDetector, normalize_error
from fullagent.sentinel import WireSentinel, build_payload, injection_plan_for
from fullagent.webfetch import _extract


def test_kernel_fast_load_keeps_branches_with_escaped_names(tmp_path):
    p = Path(tmp_path) / "l.jsonl"
    log = EventLog(p, max_events=10)
    log.append("user.message", {"text": "x"})
    log.fork(name='a"b')
    log.fork(name='a"c')
    for _ in range(50):
        log.append("tool.call", {})
    log.close()
    log2 = EventLog(p, max_events=10)
    assert sorted(log2.branches()) == sorted(log.branches())
    assert log2.head('a"b') == log.head('a"b')
    log2.close()


def test_cipherlab_decode_infinite_depth_does_not_raise():
    rep = decode("hello", max_depth=float("inf"))
    assert rep.original == "hello"


def test_webfetch_entities_not_double_unescaped():
    title, text = _extract("<title>A &amp;amp; B</title>"
                           "<p>use &amp;lt;b&amp;gt; tags</p>")
    assert title == "A &amp; B"
    assert text == "use &lt;b&gt; tags"


def test_sentinel_prefix_idempotent_for_plain_contract_head():
    p = build_payload("HEAD RULES", "LEDGER", "n", "tag")
    s = WireSentinel(p, injection_plan_for("x", p.fingerprint))
    m = [{"role": "system", "content": "BASE"},
         {"role": "user", "content": "hi"}]
    once = s.apply(m)
    twice = s.apply(once)
    assert twice[0]["content"] == once[0]["content"]
    assert twice[0]["content"].count("HEAD RULES") == 1


def test_sleep_cancellable_nan_returns():
    sleep_cancellable(float("nan"), None)  # used to loop forever


def test_normalize_error_leading_newline_keeps_signature():
    assert normalize_error("\nPermission denied") != \
        normalize_error("\nFile not found")
    assert normalize_error("\nPermission denied") == "Permission denied"


def test_loopdetector_non_str_signature():
    d = LoopDetector(repeat_limit=2)
    d.note("t", {}, None)
    assert d.note("t", {}, None) is not None
