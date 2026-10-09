"""Browser automation: real Playwright-driven browser tools, gracefully degraded.

Main entry point::

    register(agent) -> None

Registers four tools on the agent:

- ``BrowserOpen(url)``      — load a URL in a real headless Chromium page;
                              returns the page title and final URL.
- ``BrowserSnapshot()``     — real accessibility-tree snapshot of the
                              current page (truncated), so the model can see
                              what is on screen.
- ``BrowserClick(selector)`` — click a CSS selector on the current page.
- ``BrowserClose()``        — shut the browser down.

One persistent browser context is kept per agent: it is created lazily on
the first use and torn down by ``BrowserClose``. Every Playwright operation
uses a 30-second timeout.

Graceful degradation: the module only ever imports ``playwright`` inside
function calls. If the package is not installed, every tool returns the
clear message ``"playwright not installed (pip install playwright)"`` —
nothing is faked or simulated. Never imports ``.agent``.
"""

from __future__ import annotations

import threading

__all__ = ["register", "browser_available", "NOT_INSTALLED_MSG",
           "DEFAULT_TIMEOUT_MS", "_BrowserSession"]

DEFAULT_TIMEOUT_MS = 30_000  # every Playwright operation: 30s
SNAPSHOT_CAP = 8_000         # max chars of the accessibility snapshot

NOT_INSTALLED_MSG = ("playwright not installed (pip install playwright) "
                     "— browser tools are unavailable until it is installed.")


# ---------------------------------------------------------------------------
# Optional playwright import (never raises)
# ---------------------------------------------------------------------------

def _import_playwright():
    """Return playwright's ``sync_api`` module, or None if unavailable."""
    try:
        from playwright import sync_api
        return sync_api
    except Exception:
        return None


def browser_available() -> bool:
    """True when the real playwright package can be imported."""
    return _import_playwright() is not None


# ---------------------------------------------------------------------------
# Persistent browser session (one per agent, lazy init)
# ---------------------------------------------------------------------------

class _BrowserSession:
    """Lazy headless-Chromium session: playwright -> browser -> page.

    Thread-safe enough for the agent's serialized tool calls. Never raises
    from public methods; failures come back as ``"ERROR: ..."`` strings.
    """

    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        self._timeout = timeout_ms
        self._lock = threading.RLock()
        self._pw = None       # playwright instance
        self._browser = None  # Browser
        self._page = None     # Page

    # -- lifecycle ------------------------------------------------------
    def _launch(self) -> str:
        """Start playwright/browser/page. Returns '' on success, else error."""
        sync_api = _import_playwright()
        if sync_api is None:
            return NOT_INSTALLED_MSG
        pw = browser = None
        try:
            pw = sync_api.sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page()
            page.set_default_timeout(self._timeout)
        except Exception as e:  # noqa: BLE001 — real launch failure
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            return (f"ERROR: could not launch chromium: "
                    f"{type(e).__name__}: {e}")
        self._pw, self._browser, self._page = pw, browser, page
        return ""

    def _ensure(self):
        """Return (error, page); launches lazily. Never raises."""
        with self._lock:
            if self._page is not None:
                return "", self._page
            err = self._launch()
            if err:
                return err, None
            return "", self._page

    def close(self) -> str:
        """Shut down browser + playwright. Safe to call when not open."""
        with self._lock:
            if self._page is None and self._browser is None:
                if _import_playwright() is None:
                    return NOT_INSTALLED_MSG
                return "browser already closed"
            errs: list[str] = []
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{type(e).__name__}: {e}")
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{type(e).__name__}: {e}")
            self._page = self._browser = self._pw = None
            if errs:
                return "ERROR: " + "; ".join(errs)
            return "browser closed"

    # -- operations ------------------------------------------------------
    def open_url(self, url: str) -> str:
        """Real page load. Returns title + final url."""
        if not isinstance(url, str) or not url.strip():
            return "ERROR: url must be a non-empty string"
        err, page = self._ensure()
        if err:
            return err
        try:
            page.goto(url.strip(), timeout=self._timeout,
                      wait_until="domcontentloaded")
            return f"title: {page.title()}\nurl: {page.url}"
        except Exception as e:  # noqa: BLE001 — real nav failure
            return f"ERROR: {type(e).__name__}: {e}"

    def snapshot(self) -> str:
        """Real accessibility-tree snapshot of the current page."""
        err, page = self._ensure()
        if err:
            return err
        try:
            tree = page.accessibility.snapshot()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"
        if not tree:
            # Empty AX tree (e.g. canvas-only page): fall back to raw text.
            try:
                text = (page.locator("body").inner_text()
                        if page.locator("body").count() else "")
            except Exception:
                text = ""
            text = " ".join(text.split())
            return text[:SNAPSHOT_CAP] or "(empty page)"
        lines: list[str] = []

        def _walk(node: dict, depth: int) -> None:
            role = str(node.get("role", "?"))
            name = str(node.get("name", "") or "").strip()
            value = str(node.get("value", "") or "").strip()
            detail = " ".join(p for p in (name, value) if p)
            line = f"{'  ' * depth}[{role}]" + (f" {detail}" if detail else "")
            lines.append(line)
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    _walk(child, depth + 1)

        try:
            _walk(tree, 0)
        except Exception:
            pass
        out = "\n".join(lines).strip()
        if len(out) > SNAPSHOT_CAP:
            out = out[:SNAPSHOT_CAP].rsplit("\n", 1)[0] + \
                f"\n… [{len(out) - SNAPSHOT_CAP} chars truncated]"
        return out or "(empty accessibility tree)"

    def click(self, selector: str) -> str:
        """Real click on a CSS selector on the current page."""
        if not isinstance(selector, str) or not selector.strip():
            return "ERROR: selector must be a non-empty string"
        err, page = self._ensure()
        if err:
            return err
        try:
            page.click(selector.strip(), timeout=self._timeout)
            return f"clicked: {selector.strip()}"
        except Exception as e:  # noqa: BLE001 — real click failure
            return f"ERROR: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------

def register(agent) -> None:
    """Register BrowserOpen/BrowserSnapshot/BrowserClick/BrowserClose tools.

    The browser session is stored on ``agent.browserauto_session`` so one
    persistent context lives per agent; it is created lazily and torn down
    by ``BrowserClose``. Click needs user approval (RISK_CONFIRM); the rest
    are read-only-ish reads (RISK_SAFE). Duck-typed: never imports .agent.
    """
    from .tools import Tool, RISK_SAFE, RISK_CONFIRM  # local: no cycles

    session = getattr(agent, "browserauto_session", None)
    if not isinstance(session, _BrowserSession):
        session = _BrowserSession()
        agent.browserauto_session = session

    agent.tools["BrowserOpen"] = Tool(
        "BrowserOpen",
        "Open a URL in a real headless Chromium browser and return the "
        "page title and final URL. Use this for pages that need JavaScript.",
        {"type": "object",
         "properties": {"url": {"type": "string",
                               "description": "http(s) URL to load"}},
         "required": ["url"]},
        session.open_url, risk=RISK_SAFE)

    agent.tools["BrowserSnapshot"] = Tool(
        "BrowserSnapshot",
        "Snapshot the current browser page as an accessibility tree "
        "(roles + visible names/values) so you can see what is on screen.",
        {"type": "object", "properties": {}},
        lambda: session.snapshot(), risk=RISK_SAFE)

    agent.tools["BrowserClick"] = Tool(
        "BrowserClick",
        "Click an element on the current browser page by CSS selector "
        "(e.g. 'button.submit', '#login', 'a[href=\"/docs\"]').",
        {"type": "object",
         "properties": {"selector": {"type": "string",
                                    "description": "CSS selector to click"}},
         "required": ["selector"]},
        session.click, risk=RISK_CONFIRM)

    agent.tools["BrowserClose"] = Tool(
        "BrowserClose",
        "Close the headless Chromium browser and free its resources.",
        {"type": "object", "properties": {}},
        session.close, risk=RISK_SAFE)


# ---------------------------------------------------------------------------
# Self-test: python3 -m fullagent.browserauto  →  PASS
#
# With playwright installed: opens real https://example.com, checks the
# title, snapshots the page, closes. Without it: every tool returns the
# clear not-installed message instead of raising. Either path passes.
# ---------------------------------------------------------------------------

def _selftest() -> None:
    fails = 0

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            fails += 1

    have_pw = browser_available()
    print("playwright present:", have_pw)

    if not have_pw:
        # --- graceful degradation: tools return the message, never raise ---
        session = _BrowserSession()
        for name, call in (
            ("BrowserOpen", lambda: session.open_url("https://example.com")),
            ("BrowserSnapshot", session.snapshot),
            ("BrowserClick", lambda: session.click("button")),
            ("BrowserClose", session.close),
        ):
            try:
                out = call()
            except Exception as e:  # noqa: BLE001
                check(f"{name} does not raise when playwright missing",
                      False)
                print("   raised:", e)
                continue
            check(f"{name} returns not-installed message when missing",
                  out == NOT_INSTALLED_MSG)
            check(f"{name} message names pip install",
                  "pip install playwright" in out)

        # register() works on a duck-typed agent and wires 4 tools
        class _FakeAgent:
            def __init__(self):
                self.tools = {}

        agent = _FakeAgent()
        try:
            register(agent)
            check("register() does not raise without playwright", True)
        except Exception as e:  # noqa: BLE001
            check("register() does not raise without playwright", False)
            print("   raised:", e)
        check("4 tools registered",
              {"BrowserOpen", "BrowserSnapshot", "BrowserClick",
               "BrowserClose"} <= set(agent.tools))
        tool_outs = []
        for t in ("BrowserOpen", "BrowserSnapshot", "BrowserClick"):
            h = agent.tools[t].handler
            try:
                tool_outs.append(h("x") if t != "BrowserSnapshot"
                                else h())
            except Exception as e:  # noqa: BLE001
                tool_outs.append(f"RAISED {e}")
        check("tool handlers return not-installed message",
              all(o == NOT_INSTALLED_MSG for o in tool_outs))
        try:
            close_out = agent.tools["BrowserClose"].handler()
        except Exception as e:  # noqa: BLE001
            close_out = f"RAISED {e}"
        check("BrowserClose returns not-installed message",
              close_out == NOT_INSTALLED_MSG)
    else:
        # --- real playwright path: live run against example.com ------------
        class _FakeAgent:
            def __init__(self):
                self.tools = {}

        agent = _FakeAgent()
        register(agent)
        check("4 tools registered",
              {"BrowserOpen", "BrowserSnapshot", "BrowserClick",
               "BrowserClose"} <= set(agent.tools))

        out = agent.tools["BrowserOpen"].handler("https://example.com")
        check("BrowserOpen returns output (no exception)", bool(out))
        print("   open:", out.replace("\n", " | ")[:120])
        check("title contains 'Example'", "Example" in out)

        snap = agent.tools["BrowserSnapshot"].handler()
        check("snapshot returns text", bool(snap and len(snap) > 20))
        print("   snapshot head:", snap.splitlines()[0][:100]
              if snap else "")
        check("snapshot contains page text",
              "Example" in snap or "example" in snap)

        # real click: the "More information..." link on example.com
        click_out = agent.tools["BrowserClick"].handler("a")
        check("BrowserClick returns (no exception)", bool(click_out))
        print("   click:", click_out[:100])

        close_out = agent.tools["BrowserClose"].handler()
        check("BrowserClose reports closed",
              close_out == "browser closed")
        print("   close:", close_out)

        # after close, lazy re-init works (session still functional)
        out2 = agent.tools["BrowserOpen"].handler("https://example.com")
        check("re-open after close works", "Example" in out2)
        final = agent.tools["BrowserClose"].handler()
        check("final close clean", final == "browser closed")

        # invalid inputs return errors, never raise
        check("empty url -> error",
              agent.tools["BrowserOpen"].handler("").startswith("ERROR"))
        check("empty selector -> error",
              agent.tools["BrowserClick"].handler("").startswith("ERROR"))

    print("PASS" if fails == 0 else "FAIL", f"- {fails} failure(s)")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    _selftest()
