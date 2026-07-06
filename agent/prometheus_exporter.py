"""Prometheus exporter — exposes metrics on a scrape endpoint.

Runs a tiny HTTP server (stdlib only) that serves /metrics in Prometheus
text format. Integrates with the existing MetricsRegistry so any metric
recorded via metrics.py is auto-exported.

Start with: start_exporter(port=9090)
Scrape with: curl http://localhost:9090/metrics
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .metrics import metrics
from .token_counter import get_token_counter
from .profiler import get_profiler


_PROMETHEUS_FORMAT = """# HELP {name} {help}
# TYPE {type} {name}
{metrics}
"""

_exporter_thread: threading.Thread | None = None
_exporter_server: HTTPServer | None = None
_exporter_port: int = 9090


def _format_metrics() -> str:
    """Render all metrics in Prometheus text format."""
    parts: list[str] = []
    snap = metrics.snapshot()
    # Counters.
    parts.append("# HELP agent_counters_total Counters tracked by the agent")
    parts.append("# TYPE agent_counters counter")
    for name, value in snap.get("counters", {}).items():
        parts.append(f'agent_counters{{metric="{name}"}} {value}')
    # Gauges.
    parts.append("# HELP agent_gagues Current gauge values")
    parts.append("# TYPE agent_gauges gauge")
    for name, value in snap.get("gauges", {}).items():
        parts.append(f'agent_gauges{{metric="{name}"}} {value}')
    # Histograms.
    parts.append("# HELP agent_histograms Histogram observations")
    parts.append("# TYPE agent_histograms summary")
    for name, h in snap.get("histograms", {}).items():
        parts.append(f'agent_histograms{{metric="{name}",stat="count"}} {h["count"]}')
        parts.append(f'agent_histograms{{metric="{name}",stat="avg"}} {h["avg"]}')
        parts.append(f'agent_histograms{{metric="{name}",stat="min"}} {h["min"]}')
        parts.append(f'agent_histograms{{metric="{name}",stat="max"}} {h["max"]}')
    # Token usage (from the token counter singleton).
    try:
        tc = get_token_counter()
        tsnap = tc.snapshot()
        parts.append("# HELP agent_tokens_total Token usage")
        parts.append("# TYPE agent_tokens counter")
        parts.append(f'agent_tokens{{type="session_input"}} {tsnap["session_input"]}')
        parts.append(f'agent_tokens{{type="session_output"}} {tsnap["session_output"]}')
        parts.append(f'agent_tokens{{type="session_total"}} {tsnap["session_total"]}')
        parts.append(f'agent_tokens{{type="all_time_total"}} {tsnap["all_time_total"]}')
        parts.append("# HELP agent_cost_usd Cost in USD")
        parts.append("# TYPE agent_cost_usd gauge")
        parts.append(f'agent_cost_usd{{scope="session"}} {tsnap["session_cost_usd"]}')
        parts.append(f'agent_cost_usd{{scope="all_time"}} {tsnap["all_time_cost_usd"]}')
    except Exception:  # noqa: BLE001
        pass
    # Profiler aggregates.
    try:
        prof = get_profiler()
        if prof._totals:
            parts.append("# HELP agent_op_duration_seconds Operation durations")
            parts.append("# TYPE agent_op_duration_seconds summary")
            for name, total in prof._totals.items():
                count = prof._counts.get(name, 0)
                avg = total / count if count else 0
                parts.append(f'agent_op_duration_seconds{{op="{name}",stat="total"}} {total}')
                parts.append(f'agent_op_duration_seconds{{op="{name}",stat="avg"}} {avg}')
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(parts) + "\n"


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/metrics":
            body = _format_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            body = b'<html><body><h1>Terminal Agent Prometheus Exporter</h1><a href="/metrics">/metrics</a></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # suppress default logging


def start_exporter(port: int = 9090, host: str = "0.0.0.0") -> str:
    """Start the Prometheus exporter in a background thread. Returns the URL."""
    global _exporter_thread, _exporter_server, _exporter_port
    _exporter_port = port
    if _exporter_server is not None:
        return f"http://localhost:{_exporter_port}/metrics (already running)"
    _exporter_server = HTTPServer((host, port), _MetricsHandler)
    _exporter_thread = threading.Thread(target=_exporter_server.serve_forever, daemon=True)
    _exporter_thread.start()
    return f"http://localhost:{port}/metrics"


def stop_exporter() -> None:
    global _exporter_server, _exporter_thread
    if _exporter_server is not None:
        _exporter_server.shutdown()
        _exporter_server = None
    if _exporter_thread is not None:
        _exporter_thread.join(timeout=2)
        _exporter_thread = None


def exporter_status() -> str:
    if _exporter_server is None:
        return "Prometheus exporter: stopped"
    return f"Prometheus exporter: running on http://localhost:{_exporter_port}/metrics"


def scrape_once() -> str:
    """Return the current metrics output (no HTTP needed)."""
    return _format_metrics()
