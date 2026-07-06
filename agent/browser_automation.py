"""Browser automation via Playwright — headless web interaction.

Real, working browser automation:
  * Navigate to URLs
  * Click elements
  * Fill forms
  * Extract text/HTML
  * Take screenshots
  * Execute JavaScript

Requires Playwright to be installed: pip install playwright && playwright install
Falls back gracefully when not available.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BrowserResult:
    success: bool
    url: str = ""
    title: str = ""
    text: str = ""
    html: str = ""
    screenshot_path: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _check_playwright() -> bool:
    """Check if Playwright is available."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def navigate(url: str, wait: int = 3, take_screenshot: bool = False) -> BrowserResult:
    """Navigate to a URL and return the page content."""
    if not _check_playwright():
        return _fallback_fetch(url)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(wait * 1000)
            result = BrowserResult(
                success=True,
                url=url,
                title=page.title(),
                text=page.inner_text("body")[:10000],
                html=page.content()[:50000],
            )
            if take_screenshot:
                shot_path = str(Path(tempfile.gettempdir()) / f"screenshot_{page.title()[:30]}.png")
                page.screenshot(path=shot_path, full_page=True)
                result.screenshot_path = shot_path
            browser.close()
            return result
    except Exception as exc:  # noqa: BLE001
        return BrowserResult(success=False, url=url, error=str(exc))


def _fallback_fetch(url: str) -> BrowserResult:
    """Fallback: use httpx to fetch the page (no JS rendering)."""
    try:
        import httpx
        import re
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        html = resp.text
        # Extract title.
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""
        # Strip HTML for text.
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()[:10000]
        return BrowserResult(
            success=True,
            url=url,
            title=title,
            text=text,
            html=html[:50000],
            metadata={"method": "httpx_fallback"},
        )
    except Exception as exc:  # noqa: BLE001
        return BrowserResult(success=False, url=url, error=str(exc))


def click_element(url: str, selector: str, wait: int = 2) -> BrowserResult:
    """Navigate to a URL and click an element by CSS selector."""
    if not _check_playwright():
        return BrowserResult(success=False, url=url, error="Playwright not installed. Run: pip install playwright && playwright install")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.click(selector, timeout=10000)
            page.wait_for_timeout(wait * 1000)
            result = BrowserResult(
                success=True,
                url=page.url,
                title=page.title(),
                text=page.inner_text("body")[:10000],
            )
            browser.close()
            return result
    except Exception as exc:  # noqa: BLE001
        return BrowserResult(success=False, url=url, error=str(exc))


def fill_form(url: str, fields: dict[str, str], submit_selector: str | None = None, wait: int = 2) -> BrowserResult:
    """Fill a form on a page and optionally submit it."""
    if not _check_playwright():
        return BrowserResult(success=False, url=url, error="Playwright not installed")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for selector, value in fields.items():
                page.fill(selector, value, timeout=5000)
            if submit_selector:
                page.click(submit_selector, timeout=10000)
                page.wait_for_timeout(wait * 1000)
            result = BrowserResult(
                success=True,
                url=page.url,
                title=page.title(),
                text=page.inner_text("body")[:10000],
            )
            browser.close()
            return result
    except Exception as exc:  # noqa: BLE001
        return BrowserResult(success=False, url=url, error=str(exc))


def execute_js(url: str, script: str) -> BrowserResult:
    """Execute JavaScript on a page and return the result."""
    if not _check_playwright():
        return BrowserResult(success=False, url=url, error="Playwright not installed")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            result_value = page.evaluate(script)
            browser.close()
            return BrowserResult(
                success=True,
                url=url,
                title=page.title(),
                text=str(result_value)[:10000],
                metadata={"js_result": result_value},
            )
    except Exception as exc:  # noqa: BLE001
        return BrowserResult(success=False, url=url, error=str(exc))


def browser_status() -> str:
    if _check_playwright():
        return "Playwright: installed ✓ (full browser automation available)"
    return "Playwright: NOT installed. Install with: pip install playwright && playwright install chromium"
