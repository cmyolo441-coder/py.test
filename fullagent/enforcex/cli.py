"""cli — Command-line interface for the enforcex toolkit.

Subcommands:

  extract <promptfile>                     print extracted rules + digest
  check <responsefile> [--prompt FILE]     run the gate, print report
         [--json] [--html out.html]
  scan <textfile>                          injection / threat scan
  serve --prompt FILE [--port 8765]        tiny HTTP API on 127.0.0.1
  selftest                                 run all sibling _self_test()s

Exit codes: 0 pass, 1 fail (enforcement), 2 usage error.
Stdlib only; all sibling imports are defensive so the CLI works even
while sibling modules are being developed in parallel.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["main"]

# ── defensive sibling imports ────────────────────────────────────────────

try:
    from . import gate as _gate
except Exception:                                        # pragma: no cover
    _gate = None

try:
    from . import report as _report
except Exception:                                        # pragma: no cover
    _report = None

try:
    from . import scoring as _scoring
except Exception:                                        # pragma: no cover
    _scoring = None

try:
    from . import ruleextract as _ruleextract
except Exception:                                        # pragma: no cover
    _ruleextract = None

try:
    from . import injection as _injection
except Exception:                                        # pragma: no cover
    _injection = None

try:
    from . import detect as _detect
except Exception:                                        # pragma: no cover
    _detect = None


EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


# ── small helpers ────────────────────────────────────────────────────────

def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def _die(msg: str, code: int = EXIT_USAGE) -> int:
    _eprint(f"error: {msg}")
    return code


def _extract_rules(prompt_text: str) -> Any:
    """Defensively extract rules from a system prompt. Returns None when
    no extractor is available."""
    if _ruleextract is None:
        return None
    for fn_name in ("extract_rules", "extract", "parse_rules"):
        fn = getattr(_ruleextract, fn_name, None)
        if callable(fn):
            try:
                return fn(prompt_text)
            except TypeError:
                try:
                    return fn(prompt_text)  # keep signature simple
                except Exception:
                    return None
            except Exception:
                return None
    return None


def _rules_digest(rules: Any) -> str:
    """Short digest string of an extracted rules object."""
    if rules is None:
        return "(no extractor available — gate will run with defaults)"
    try:
        if isinstance(rules, dict):
            if "digest" in rules:
                return str(rules["digest"])
            items = rules
        else:
            items = list(rules)
        ids = []
        for it in items:
            if isinstance(it, dict):
                ids.append(str(it.get("rule_id") or it.get("id") or "?"))
            else:
                ids.append(str(it)[:12])
        count = len(ids)
        tail = ",".join(ids[:6]) + ("..." if count > 6 else "")
        return f"{count} rules [{tail}]"
    except Exception:
        return "(rules extracted; digest unavailable)"


def _print_rules_table(rules: Any) -> None:
    if rules is None:
        return
    try:
        items = list(rules.values()) if isinstance(rules, dict) else list(rules)
    except Exception:
        return
    print()
    print(f"{'RULE':<14} {'CAT':<16} {'SEV':<10} TEXT")
    print("-" * 78)
    for it in items:
        if isinstance(it, dict):
            rid = str(it.get("rule_id") or it.get("id") or "?")[:13]
            cat = str(it.get("category") or "?")[:15]
            sev = str(it.get("severity") or it.get("weight") or "?")[:9]
            text = str(it.get("text") or it.get("rule") or "")[:60]
        else:
            rid, cat, sev, text = str(it)[:13], "-", "-", str(it)[:60]
        print(f"{rid:<14} {cat:<16} {sev:<10} {text}")
    print()


# ── command implementations ──────────────────────────────────────────────

def cmd_extract(args: argparse.Namespace) -> int:
    try:
        prompt_text = _read_file(args.promptfile)
    except OSError as exc:
        return _die(f"cannot read {args.promptfile}: {exc}")
    rules = _extract_rules(prompt_text)
    if rules is None:
        _eprint("note: ruleextract module unavailable; showing prompt digest only")
        print(f"prompt: {len(prompt_text)} chars, "
              f"{len(prompt_text.splitlines())} lines")
        return EXIT_PASS
    print("Extracted rules")
    print("===============")
    _print_rules_table(rules)
    print(f"digest: {_rules_digest(rules)}")
    return EXIT_PASS


def cmd_check(args: argparse.Namespace) -> int:
    try:
        response_text = _read_file(args.responsefile)
    except OSError as exc:
        return _die(f"cannot read {args.responsefile}: {exc}")
    prompt_text = ""
    if args.prompt:
        try:
            prompt_text = _read_file(args.prompt)
        except OSError as exc:
            return _die(f"cannot read {args.prompt}: {exc}")

    rules = _extract_rules(prompt_text) if prompt_text else None

    if _gate is None:
        return _die("gate module unavailable; cannot run enforcement",
                    EXIT_FAIL)

    try:
        cfg = _gate.GateConfig.from_env()
        g = _gate.Gate(cfg)
        decision = g.evaluate(response_text, rules=rules,
                              system_text=prompt_text,
                              session_id=getattr(args, "session", None) or "cli")
    except Exception as exc:
        return _die(f"gate evaluation failed: {exc}", EXIT_FAIL)

    if args.html:
        if _report is None:
            _eprint("warning: report module unavailable; skipping --html")
        else:
            try:
                _report.write_report(decision, args.html, fmt="html")
                print(f"html report written: {args.html}")
            except OSError as exc:
                _eprint(f"warning: could not write html report: {exc}")

    if args.json:
        if _report is not None:
            print(_report.to_json(decision))
        else:
            print(json.dumps(decision.to_dict(), indent=2, default=str))
    else:
        if _report is not None:
            print(_report.to_markdown(decision))
        else:
            d = decision.to_dict() if hasattr(decision, "to_dict") else {}
            print(f"action: {d.get('action', '?')}")
            s = d.get("score") or {}
            print(f"score:  {s.get('total', '?')} ({s.get('grade', '?')})")
            print(f"notes:  {'; '.join(d.get('notes', []))}")

    total = float(getattr(getattr(decision, "score", None), "total", 0.0) or 0.0)
    action = str(getattr(decision, "action", "block"))
    if action in ("allow", "rewrite"):
        return EXIT_PASS
    if action == "warn" and total >= 60.0:
        return EXIT_PASS
    return EXIT_FAIL


def cmd_scan(args: argparse.Namespace) -> int:
    try:
        text = _read_file(args.textfile)
    except OSError as exc:
        return _die(f"cannot read {args.textfile}: {exc}")

    findings: List[Dict[str, Any]] = []
    if _injection is not None:
        try:
            res = _injection.scan(text)
            if isinstance(res, dict):
                findings.extend(res.get("violations") or res.get("matches") or [])
            elif isinstance(res, list):
                findings.extend(res)
        except Exception as exc:
            _eprint(f"warning: injection.scan failed: {exc}")
    if _detect is not None:
        try:
            res = _detect.run_all(text)
            if isinstance(res, dict):
                findings.extend(res.get("violations") or res.get("findings") or [])
            elif isinstance(res, list):
                findings.extend(res)
        except Exception as exc:
            _eprint(f"warning: detect.run_all failed: {exc}")
    if not findings:
        # last-resort keyword fallback mirrors gate.py
        low = text.lower()
        for kw in ("ignore previous instructions", "reveal your system prompt",
                   "you are now", "developer mode", "jailbreak"):
            if kw in low:
                findings.append({"category": "injection", "severity": "critical",
                                 "evidence": kw, "message": "keyword match"})
                break

    if not findings:
        print("scan: clean — no injection or threat patterns found")
        return EXIT_PASS
    print(f"scan: {len(findings)} finding(s)")
    for i, f in enumerate(findings, 1):
        if isinstance(f, dict):
            print(f"  {i}. [{f.get('severity', '?'):>8}] "
                  f"[{f.get('category', '?')}] {f.get('evidence', '')[:80]}")
        else:
            print(f"  {i}. {str(f)[:100]}")
    return EXIT_FAIL


def cmd_serve(args: argparse.Namespace) -> int:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    try:
        prompt_text = _read_file(args.prompt) if args.prompt else ""
    except OSError as exc:
        return _die(f"cannot read {args.prompt}: {exc}")
    rules = _extract_rules(prompt_text) if prompt_text else None

    if _gate is None:
        return _die("gate module unavailable; cannot serve", EXIT_FAIL)
    gate_obj = _gate.Gate(_gate.GateConfig.from_env())
    if _report is None:
        return _die("report module unavailable; cannot serve", EXIT_FAIL)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: str, ctype: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, '{"status": "ok"}', "application/json")
            else:
                self._send(404, '{"error": "not found"}', "application/json")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/check":
                self._send(404, '{"error": "not found"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                req = json.loads(raw.decode("utf-8") or "{}")
                response_text = str(req.get("response", ""))
                session_id = str(req.get("session_id", "default"))
                decision = gate_obj.evaluate(
                    response_text, rules=rules, system_text=prompt_text,
                    session_id=session_id)
                self._send(200, _report.to_json(decision), "application/json")
            except (ValueError, json.JSONDecodeError):
                self._send(400, '{"error": "invalid JSON"}', "application/json")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}),
                           "application/json")

        def log_message(self, fmt: str, *log_args: Any) -> None:
            pass  # keep stdout clean

    server = HTTPServer((args.bind, args.port), Handler)
    print(f"enforcex serving on http://{args.bind}:{args.port} "
          f"(POST /check, GET /health) — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return EXIT_PASS


def cmd_selftest(_args: argparse.Namespace) -> int:
    modules: List[str] = [
        "ruleextract", "rulestore", "scoring", "gate", "report",
        "responseanaly", "detect", "injection", "ledger", "seal",
        "canon", "policy", "attestation", "repair", "fuzz", "mutate",
        "probes", "redteam", "shadow", "semantic", "semantics",
        "metaguard", "critic", "cognify", "drift", "stream",
        "consensus", "orchestrator", "warroom", "telemetry",
        "dashboard", "forge", "sandbox", "cipherlab", "multiturn", "trust",
        "foresight", "shield",
    ]
    failed: List[str] = []
    ran: List[str] = []
    for name in modules:
        try:
            mod = __import__(f"fullagent.enforcex.{name}",
                             fromlist=["_self_test"])
        except Exception as exc:
            print(f"  {name:<16} SKIP (import failed: {type(exc).__name__})")
            continue
        fn = getattr(mod, "_self_test", None)
        if not callable(fn):
            print(f"  {name:<16} SKIP (no _self_test)")
            continue
        try:
            fn()
            ran.append(name)
            print(f"  {name:<16} OK")
        except AssertionError as exc:
            failed.append(name)
            print(f"  {name:<16} FAIL (assertion: {exc})")
        except Exception as exc:
            failed.append(name)
            print(f"  {name:<16} FAIL ({type(exc).__name__}: {exc})")
    print()
    print(f"selftest: {len(ran)} passed, {len(failed)} failed "
          f"({len(modules) - len(ran) - len(failed)} skipped)")
    return EXIT_FAIL if failed else EXIT_PASS


# ── argument parsing ─────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enforcex",
        description="System-prompt compliance enforcement toolkit",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("extract", help="extract rules from a system prompt file")
    p.add_argument("promptfile")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("check", help="run the enforcement gate on a response")
    p.add_argument("responsefile")
    p.add_argument("--prompt", default=None, help="system prompt file")
    p.add_argument("--session", default="cli", help="session id for retry budget")
    p.add_argument("--json", action="store_true", help="emit JSON report")
    p.add_argument("--html", default=None, metavar="OUT.html",
                   help="also write an HTML report")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("scan", help="injection / threat scan of a text file")
    p.add_argument("textfile")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("serve", help="serve the gate over HTTP")
    p.add_argument("--prompt", default=None, help="system prompt file")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--bind", default="127.0.0.1")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("selftest", help="run all module self-tests")
    p.set_defaults(func=cmd_selftest)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except SystemExit as exc:  # argparse may raise for bad sub-use
        code = exc.code
        return code if isinstance(code, int) else EXIT_USAGE
    except KeyboardInterrupt:
        _eprint("interrupted")
        return EXIT_USAGE


# ── self-test ────────────────────────────────────────────────────────────

def _self_test() -> None:  # pragma: no cover
    parser = build_parser()
    argv = ["check", "resp.txt", "--prompt", "p.txt", "--json",
            "--html", "out.html"]
    ns = parser.parse_args(argv)
    assert ns.command == "check" and ns.responsefile == "resp.txt"
    assert ns.prompt == "p.txt" and ns.json and ns.html == "out.html"

    ns2 = parser.parse_args(["extract", "prompt.md"])
    assert ns2.command == "extract" and ns2.promptfile == "prompt.md"

    ns3 = parser.parse_args(["serve", "--port", "9000"])
    assert ns3.command == "serve" and ns3.port == 9000
    assert ns3.bind == "127.0.0.1"

    ns4 = parser.parse_args(["selftest"])
    assert ns4.command == "selftest"

    # missing command -> usage exit
    assert main([]) == EXIT_USAGE
    # missing file -> usage error exit
    assert main(["check", "/nonexistent/file/xyz.txt"]) == EXIT_USAGE

    # end-to-end check against a real temp response file
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        rpath = os.path.join(tmp, "resp.txt")
        with open(rpath, "w", encoding="utf-8") as fh:
            fh.write("All good, here is the summary.\n1. one\n2. two\n")
        rc = main(["check", rpath, "--json"])
        assert rc == EXIT_PASS, rc

    # extract with no extractor available must not crash
    rc = main(["extract", os.path.abspath(__file__)])
    assert rc in (EXIT_PASS, EXIT_FAIL), rc

    print("cli._self_test: OK")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
