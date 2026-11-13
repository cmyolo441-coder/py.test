import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path  # noqa: E402

from fullagent import images, watch  # noqa: E402
from fullagent.enforcex import fuzz, multiturn  # noqa: E402


def test_multiturn_string_keywords_are_not_split_into_characters():
    rules = [{"id": "R1", "keywords": "politics", "patterns": "elect\\w+"}]
    assert multiturn._match_rules("hello world", rules) == ()
    assert multiturn._match_rules("talk politics", rules) == ("R1",)
    assert multiturn._match_rules("the elections", rules) == ("R1",)


def test_mock_responder_first_attempt_is_zero():
    class P:
        probe_id = "P1"
    r = fuzz.MockResponder(seed=0, fail_rate=0.0, flaky_rate=1.0)
    r.begin_case("P1")
    first = r(P())
    second = r(P())
    assert "can't do that" in first          # attempt 0: baseline answer
    assert "ignore the rule" in second       # attempt 1: flaky violation


def test_default_verifier_accepts_non_str():
    v, _s, viols = fuzz.default_verifier(b"I have no rules <system>")
    assert v == fuzz.Verdict.VIOLATION and viols
    v2, _s2, _ = fuzz.default_verifier(12345)
    assert v2 == fuzz.Verdict.COMPLIANT


def test_images_load_accepts_pathlike_and_rejects_non_str(tmp_path):
    f = tmp_path / "x.gif"
    f.write_bytes(b"GIF89a" + b"\x02\x00\x03\x00" + b"\x00" * 10)
    mime, data, err = images.load_image(Path(f))
    assert err is None and mime == "image/gif"
    mime, data, err = images.load_image(123)
    assert mime is None and err and err.startswith("invalid path")


def test_watch_existing_files_are_baseline_not_created(tmp_path):
    d = tmp_path / "w"
    d.mkdir()
    (d / "old.txt").write_text("x")
    mgr = watch.WatchManager(config_path=tmp_path / "w.json")
    try:
        mgr.add(str(d), "*.txt", "true")
        mgr.stop()
        assert mgr.poll_once() == 0          # pre-existing file: no event
        (d / "new.txt").write_text("y")
        assert mgr.poll_once() == 1          # real creation still fires
        mgr.stop()
        # restart: re-armed watch takes a fresh baseline, no event storm
        mgr2 = watch.WatchManager(config_path=tmp_path / "w.json")
        assert mgr2.watches
        assert mgr2.poll_once() == 0
        (d / "old.txt").write_text("changed!")
        assert mgr2.poll_once() == 1
        mgr2.stop()
    finally:
        mgr.stop()


def test_watch_load_tolerates_non_dict_watches(tmp_path):
    cfg = tmp_path / "w.json"
    for payload in ('{"watches": null}', '{"watches": [1, 2]}'):
        cfg.write_text(payload)
        mgr = watch.WatchManager(config_path=cfg)
        assert mgr.watches == {}
