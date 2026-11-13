import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from fullagent import client as c  # noqa: E402
from fullagent.config import Effort, Model, Provider  # noqa: E402


@pytest.fixture
def isolated_cal(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "_CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(c, "_calibration_loaded", False)
    monkeypatch.setattr(c, "_correction_samples", {})
    monkeypatch.setattr(c, "_learned_windows", {})
    return tmp_path / "calibration.json"


@pytest.mark.parametrize("body", ["[]", '"x"', '{"schema": 2, "corrections": [1]}',
                                  '{"windows": [5]}'])
def test_non_dict_calibration_file_does_not_crash(isolated_cal, body):
    isolated_cal.write_text(body)
    m = Model(id="m", provider="kios", label="M", context_window=128_000)
    assert c.effective_window(m) == 128_000
    assert c._correction_factor("m") == 1.0


def test_calibration_learns_from_pruned_prompt(isolated_cal, monkeypatch):
    m = Model(id="tiny-cal", provider="kios", label="Tiny",
              context_window=8_000)
    e = Effort(key="t", label="T", color="", max_tokens=1_000,
               temperature=0.0, reasoning_effort=None, description="")
    p = Provider(key="t", name="t", base_url="http://x", api_key="k",
                 color="red")
    hist = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": f"q {i} " + "x" * 500}
        for i in range(90)]
    seen = {}

    def fake_stream(url, headers, payload, *a, **k):
        # backend reports exactly our raw estimate of what it received
        seen["raw"] = c.prompt_raw_tokens(payload["messages"],
                                          payload.get("tools"))
        return c.StreamResult(content="ok", usage={
            "prompt_tokens": int(seen["raw"])})

    monkeypatch.setattr(c, "_chat_stream_with_retries", fake_stream)
    c.chat_stream(p, m, e, hist, None)
    # correction must be ~1.0 — the old code divided by the PRE-prune
    # estimate (~4x larger) and learned a ~0.25 multiplier
    assert 0.9 <= c._correction_factor("tiny-cal") <= 1.1
