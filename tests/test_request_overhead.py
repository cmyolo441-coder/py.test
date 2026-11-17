"""Per-request overhead with a ~49k-char system prompt: no re-estimation,
no sleeps on the success path, one pooled keep-alive connection, and a
fast build_payload even when the agent hands over fresh message dicts
every iteration. Offline (loopback fake server)."""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import statistics
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fullagent import client
from fullagent.config import EFFORTS, Model, Provider
from fullagent.streamthrottle import StreamThrottle

SYSTEM = ("Follow every rule. " * 40 + "\n") * 65   # ~49k chars
assert 48_000 < len(SYSTEM) < 51_000


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bodies: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        type(self).bodies.append(json.loads(body))
        ev = {"choices": [{"delta": {"content": "hi"}}]}
        out = (b"data: " + json.dumps(ev).encode() + b"\n\n"
               b"data: [DONE]\n\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    accepted = 0

    def get_request(self):
        r = super().get_request()
        type(self).accepted += 1
        return r


def _msgs():
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "hello"}]


class RequestOverhead(unittest.TestCase):
    def setUp(self):
        _Handler.bodies = []
        _Server.accepted = 0
        self.srv = _Server(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.prov = Provider("fake", "fake",
                             f"http://127.0.0.1:{self.srv.server_address[1]}/v1",
                             "k", "white")
        self.model = Model("m", "fake", "m", supports_tools=True,
                           context_window=262_144)
        client._SESSION = None          # fresh pool for the connection count

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        client._SESSION = None

    def test_keepalive_one_connection_and_no_sleep_on_success(self):
        def boom(*a, **k):
            raise AssertionError("success path must never sleep/back off")
        times = []
        with mock.patch.object(client, "sleep_cancellable", boom):
            for _ in range(6):
                t = time.perf_counter()
                r = client.chat_stream(self.prov, self.model, EFFORTS[2],
                                       _msgs(), None)
                times.append(time.perf_counter() - t)
                self.assertEqual(r.content, "hi")
        self.assertEqual(_Server.accepted, 1, "connection not reused")
        self.assertEqual(len(_Handler.bodies), 6)
        for b in _Handler.bodies:
            self.assertEqual(b["messages"][0]["content"], SYSTEM)
        # total client+loopback time per 49k-prompt call stays tiny
        self.assertLess(statistics.median(times[1:]), 0.25)

    def test_payload_build_fast_with_fresh_dicts_and_cached_estimate(self):
        base = _msgs()
        client.build_payload(self.model, EFFORTS[2], [dict(m) for m in base], None)
        with mock.patch.object(client, "_heuristic_raw",
                               side_effect=AssertionError("re-tokenised 49k")), \
                mock.patch.object(client, "_tiktoken_count",
                                  side_effect=AssertionError("re-tokenised 49k")):
            ts = []
            for _ in range(50):
                v = [dict(m) for m in base]            # agent's pruned view
                v[0] = {"role": "system", "content": "".join([SYSTEM])}
                t = time.perf_counter()
                client.build_payload(self.model, EFFORTS[2], v, None)
                ts.append(time.perf_counter() - t)
        self.assertLess(statistics.median(ts), 0.02,
                        f"build_payload median {statistics.median(ts)*1000:.1f}ms")

    def test_estimate_cache_hit_is_cheap(self):
        m = _msgs()
        client.estimate_tokens(m, "m")
        t = time.perf_counter()
        for _ in range(200):
            client.estimate_tokens(m, "m")
        self.assertLess((time.perf_counter() - t) / 200, 0.005)

    def test_first_token_not_delayed_by_throttle(self):
        th = StreamThrottle(interval=5.0)
        th.update("first")
        self.assertEqual(th.maybe_flush(), "first")

    def test_default_effort_request_is_not_reasoning_for_non_reasoning_models(self):
        p = client.build_payload(self.model, EFFORTS[2], _msgs(), None)
        self.assertNotIn("reasoning_effort", p)   # supports_reasoning False


if __name__ == "__main__":
    unittest.main()
