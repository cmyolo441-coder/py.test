"""LiveDiffStreamer must stay linear over a long streamed edit.

Before the fix, detect_path() and the old_string lookup re-searched and
re-unescaped the whole accumulated JSON on every chunk (O(n^2) on the UI
thread): a ~200KB streamed edit_file took over a minute and froze the TUI.
"""

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="ld_home_"))
_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from fullagent.diffpreview import LiveDiffStreamer


def _feed_all(tool, payload, step):
    st = LiveDiffStreamer(tool)
    out = []
    for i in range(0, len(payload), step):
        out.extend(st.feed(payload[i:i + step]))
    return st, out


def test_long_old_string_stream_is_linear():
    body = "".join(f"line {i} with \"quoted\" text\\n\n" for i in range(8000))
    payload = json.dumps({"path": "x.py", "old_string": body,
                          "new_string": "y\n"})
    t = time.perf_counter()
    st, _ = _feed_all("edit_file", payload, 40)
    assert time.perf_counter() - t < 3.0
    assert st._field_value("old_string") == body


def test_path_after_content_found():
    body = "a\n" * 5000
    payload = json.dumps({"content": body, "path": "late/path.txt"})
    t = time.perf_counter()
    st, _ = _feed_all("write_file", payload, 37)
    assert time.perf_counter() - t < 3.0
    assert st.detect_path() == "late/path.txt"


def test_incremental_matches_reference_on_random_chunking():
    rnd = random.Random(3)
    vals = ["plain", "with \"quotes\" and \\ backslash", "uni é中 \U0001F600",
            "tab\tnew\nline", ""]
    for _ in range(60):
        args = {"path": rnd.choice(vals) or "p", "old_string": rnd.choice(vals),
                "new_string": rnd.choice(vals)}
        payload = json.dumps(args, ensure_ascii=rnd.random() < 0.5)
        st = LiveDiffStreamer("edit_file")
        i = 0
        while i < len(payload):
            n = rnd.randint(1, 7)
            st.feed(payload[i:i + n])
            i += n
            for k in ("path", "old_string"):
                assert st._field_value(k) == \
                    LiveDiffStreamer.extract_complete_field(st.buf, k)
