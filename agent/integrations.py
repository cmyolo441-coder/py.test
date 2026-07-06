"""External integrations — Slack, Discord, Microsoft Teams, email, webhooks.

Real, working integrations that send notifications/messages via:
  * Slack incoming webhooks
  * Discord webhooks
  * MS Teams incoming webhooks
  * SMTP email
  * Generic HTTP webhooks

Webhook URLs/SMTP config come from environment variables or Config.
"""
from __future__ import annotations

import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


def send_slack(message: str, webhook_url: str | None = None, channel: str | None = None) -> tuple[bool, str]:
    """Send a message to Slack via incoming webhook."""
    webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False, "SLACK_WEBHOOK_URL not set. Get one from https://api.slack.com/messaging/webhooks"
    payload: dict[str, Any] = {"text": message}
    if channel:
        payload["channel"] = channel
    try:
        if _HAS_HTTPX:
            resp = httpx.post(webhook_url, json=payload, timeout=10)
        else:
            import urllib.request
            req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            return True, "sent"
        return resp.is_success, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_discord(message: str, webhook_url: str | None = None, username: str = "Terminal Agent") -> tuple[bool, str]:
    """Send a message to Discord via webhook."""
    webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return False, "DISCORD_WEBHOOK_URL not set. Get one from Discord channel settings → Integrations → Webhooks"
    payload = {"content": message[:2000], "username": username}  # Discord 2000-char limit
    try:
        if _HAS_HTTPX:
            resp = httpx.post(webhook_url, json=payload, timeout=10)
            return resp.is_success, f"HTTP {resp.status_code}"
        else:
            import urllib.request
            req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            return True, "sent"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_teams(message: str, title: str = "Agent Notification", webhook_url: str | None = None) -> tuple[bool, str]:
    """Send a message to Microsoft Teams via incoming webhook."""
    webhook_url = webhook_url or os.getenv("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        return False, "TEAMS_WEBHOOK_URL not set. Get one from Teams channel → Connectors → Incoming Webhook"
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0072C6",
        "title": title,
        "text": message,
    }
    try:
        if _HAS_HTTPX:
            resp = httpx.post(webhook_url, json=payload, timeout=10)
            return resp.is_success, f"HTTP {resp.status_code}"
        else:
            import urllib.request
            req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            return True, "sent"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_email(
    subject: str,
    body: str,
    to_addrs: list[str] | str,
    from_addr: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int = 587,
    username: str | None = None,
    password: str | None = None,
    html: bool = False,
) -> tuple[bool, str]:
    """Send an email via SMTP."""
    smtp_host = smtp_host or os.getenv("SMTP_HOST")
    username = username or os.getenv("SMTP_USERNAME")
    password = password or os.getenv("SMTP_PASSWORD")
    from_addr = from_addr or username or "agent@localhost"
    if not smtp_host:
        return False, "SMTP_HOST not set. Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD env vars."
    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(body, "html" if html else "plain", "utf-8"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        return True, f"sent to {len(to_addrs)} recipient(s)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_webhook(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[bool, str]:
    """Send a generic JSON payload to any HTTP webhook."""
    try:
        if _HAS_HTTPX:
            resp = httpx.post(url, json=payload, headers=headers or {"Content-Type": "application/json"}, timeout=10)
            return resp.is_success, f"HTTP {resp.status_code}: {resp.text[:200]}"
        else:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers=headers or {"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True, "sent"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def notify_all(message: str, channels: list[str] | None = None) -> dict[str, tuple[bool, str]]:
    """Send a message to all configured channels. Returns {channel: (ok, msg)}."""
    channels = channels or ["slack", "discord", "teams", "email"]
    results: dict[str, tuple[bool, str]] = {}
    for ch in channels:
        if ch == "slack":
            results["slack"] = send_slack(message)
        elif ch == "discord":
            results["discord"] = send_discord(message)
        elif ch == "teams":
            results["teams"] = send_teams(message)
        elif ch == "email":
            # Email needs a recipient — skip if not configured.
            results["email"] = (False, "email requires explicit recipient — use send_email() directly")
    return results


def integration_status() -> str:
    """Show which integrations are configured."""
    lines = ["Integration status:"]
    checks = [
        ("Slack", "SLACK_WEBHOOK_URL"),
        ("Discord", "DISCORD_WEBHOOK_URL"),
        ("MS Teams", "TEAMS_WEBHOOK_URL"),
        ("Email (SMTP)", "SMTP_HOST"),
    ]
    for name, env_var in checks:
        configured = bool(os.getenv(env_var))
        icon = "✓" if configured else "✗"
        lines.append(f"  {icon} {name:<15} ({env_var}={'set' if configured else 'unset'})")
    return "\n".join(lines)
