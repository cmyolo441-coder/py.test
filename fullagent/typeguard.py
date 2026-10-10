"""Type guards for provider/data boundaries (deep-reliability audit, worker 13/20).

Why: subagents were failing with ``AttributeError: 'str' object has no
attribute 'get'`` (and siblings) whenever a provider returned an
unexpected shape — a usage string instead of a dict, a tool_calls list
containing strings, an SSE event that parsed to a JSON array instead of
an object, content blocks where a plain string was assumed.

These four tiny functions are the systemic fix: apply them at every
place where untrusted provider data enters our code (client.py's SSE
loop and JSON parser, crew.py's turn loop, ...).  They never raise;
they never try to be clever (``ensure_dict`` on a string returns the
default — it deliberately does NOT attempt JSON parsing, that is a
different function's job); they just hand back a value of the promised
type so downstream ``.get`` / iteration / ``int()`` calls cannot blow
up.

Only stdlib. Safe to import at module level (no heavy deps, no cycles:
this module imports nothing from fullagent).
"""

from __future__ import annotations

from typing import Any, TypeVar

__all__ = ["ensure_dict", "ensure_list", "ensure_str", "ensure_int"]

T = TypeVar("T")


def ensure_dict(v: Any, default: dict | None = None) -> dict:
    """Return ``v`` if it is a dict, else a safe dict default.

    A string is NEVER JSON-parsed here — use ``parse_tool_arguments``
    (tools.py) when you actually want string -> dict conversion.
    """
    if isinstance(v, dict):
        return v
    if isinstance(default, dict):
        return default
    return {}


def ensure_list(v: Any, default: list | None = None) -> list:
    """Return ``v`` if it is a list, else a safe list default.

    Tuples/sets are NOT coerced (callers iterating with index access
    would break on a silently-coerced tuple in subtle ways); pass an
    explicit list if you need one.
    """
    if isinstance(v, list):
        return v
    if isinstance(default, list):
        return default
    return []


def ensure_str(v: Any, default: str = "") -> str:
    """Return ``v`` if it is a str, else a safe str default.

    ``bytes``/``bytearray`` are decoded (UTF-8, errors replaced) rather
    than rejected — provider wire data often arrives as bytes.
    """
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — never let decoding crash a turn
            return default if isinstance(default, str) else ""
    if isinstance(default, str):
        return default
    return ""


def ensure_int(v: Any, default: int = 0) -> int:
    """Return ``v`` as an int, else a safe int default.

    Accepts ints (incl. bools), floats (truncated), and numeric strings
    (``"42"``, ``"3.7"``, ``" 12 "``).  Anything else — None, garbage
    strings, inf/nan, containers — yields ``default``.  Never raises.
    """
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        try:
            return int(v)
        except (OverflowError, ValueError):
            pass
    elif isinstance(v, str):
        s = v.strip()
        if s:
            try:
                return int(s)
            except ValueError:
                try:
                    return int(float(s))
                except (ValueError, OverflowError):
                    pass
    return default if isinstance(default, int) and not isinstance(default, bool) else 0


# ---------------------------------------------------------------------------
# Self-test (offline: no API key needed).  Run: python -m fullagent.typeguard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _passed = 0
    _failed: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        global _passed
        if got == want and type(got) is type(want):
            _passed += 1
        else:
            _failed.append(f"{name}: got {got!r} ({type(got).__name__}), "
                           f"want {want!r} ({type(want).__name__})")

    # ---- ensure_dict: 14 shapes ----
    check("dict/ok", ensure_dict({"a": 1}), {"a": 1})
    check("dict/empty", ensure_dict({}), {})
    check("dict/None", ensure_dict(None), {})
    check("dict/str-never-parsed", ensure_dict('{"a": 1}'), {})
    check("dict/str-empty", ensure_dict(""), {})
    check("dict/list", ensure_dict([1, 2]), {})
    check("dict/tuple", ensure_dict((1, 2)), {})
    check("dict/int", ensure_dict(5), {})
    check("dict/bool", ensure_dict(True), {})
    check("dict/bytes", ensure_dict(b"x"), {})
    check("dict/nested-garbage", ensure_dict({1: [2, {3}]}), {1: [2, {3}]})
    check("dict/object", ensure_dict(object()), {})
    check("dict/custom-default", ensure_dict(None, {"d": 1}), {"d": 1})
    check("dict/bad-default", ensure_dict("x", "notadict"), {})  # type: ignore[arg-type]

    # ---- ensure_list: 14 shapes ----
    check("list/ok", ensure_list([1, "a"]), [1, "a"])
    check("list/empty", ensure_list([]), [])
    check("list/None", ensure_list(None), [])
    check("list/str", ensure_list("abc"), [])
    check("list/tuple-not-coerced", ensure_list((1, 2)), [])
    check("list/set-not-coerced", ensure_list({1, 2}), [])
    check("list/dict", ensure_list({"a": 1}), [])
    check("list/int", ensure_list(7), [])
    check("list/bool", ensure_list(False), [])
    check("list/bytes", ensure_list(b"ab"), [])
    check("list/nested-garbage", ensure_list(["s", 1, None, {"k": "v"}]),
          ["s", 1, None, {"k": "v"}])
    check("list/object", ensure_list(object()), [])
    check("list/custom-default", ensure_list(None, [9]), [9])
    check("list/bad-default", ensure_list(1, "nope"), [])  # type: ignore[arg-type]

    # ---- ensure_str: 14 shapes ----
    check("str/ok", ensure_str("hello"), "hello")
    check("str/empty", ensure_str(""), "")
    check("str/None", ensure_str(None), "")
    check("str/int", ensure_str(42), "")
    check("str/float", ensure_str(3.7), "")
    check("str/bool", ensure_str(True), "")
    check("str/list", ensure_str(["a"]), "")
    check("str/dict", ensure_str({"a": 1}), "")
    check("str/bytes", ensure_str(b"hi"), "hi")
    check("str/bytes-bad", ensure_str(b"\xff\xfe"), "\ufffd\ufffd")
    check("str/bytearray", ensure_str(bytearray(b"ok")), "ok")
    check("str/object", ensure_str(object()), "")
    check("str/custom-default", ensure_str(None, "dflt"), "dflt")
    check("str/bad-default", ensure_str(1, 5), "")  # type: ignore[arg-type]

    # ---- ensure_int: 15 shapes ----
    check("int/ok", ensure_int(42), 42)
    check("int/neg", ensure_int(-7), -7)
    check("int/zero", ensure_int(0), 0)
    check("int/None", ensure_int(None), 0)
    check("int/bool-true", ensure_int(True), 1)
    check("int/bool-false", ensure_int(False), 0)
    check("int/float-trunc", ensure_int(3.99), 3)
    check("int/float-neg", ensure_int(-2.5), -2)
    check("int/str-num", ensure_int("42"), 42)
    check("int/str-padded", ensure_int("  12  "), 12)
    check("int/str-float", ensure_int("3.7"), 3)
    check("int/str-garbage", ensure_int("abc"), 0)
    check("int/str-empty", ensure_int(""), 0)
    check("int/inf", ensure_int(float("inf")), 0)
    check("int/nan", ensure_int(float("nan")), 0)
    check("int/list", ensure_int([1]), 0)
    check("int/dict", ensure_int({"a": 1}), 0)
    check("int/custom-default", ensure_int("xx", -1), -1)
    check("int/bad-default", ensure_int(None, "nope"), 0)  # type: ignore[arg-type]

    # ---- the reported crash shapes: provider-shaped garbage ----
    sse_event = ["not", "a", "dict"]                      # JSON array event
    check("crash/event-array", ensure_dict(sse_event).get("x"), None)
    check("crash/usage-str", ensure_dict("123 tokens").get("prompt_tokens"), None)
    check("crash/tc-str", ensure_dict("call_1").get("index", 0), 0)
    check("crash/content-list", ensure_str([{"type": "text"}]), "")
    check("crash/index-str", ensure_int("abc", 0), 0)

    print(f"typeguard self-test: {_passed} passed, {len(_failed)} failed")
    for f in _failed:
        print("  FAIL:", f)
    raise SystemExit(1 if _failed else 0)
