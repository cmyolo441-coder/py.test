"""Network tools: ping, DNS resolve, public IP, port check."""

from __future__ import annotations

import socket
import subprocess

import httpx

from .base import Tool, ToolResult


def dns_resolve(host: str) -> ToolResult:
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({info[4][0] for info in infos})
    except socket.gaierror as exc:
        return ToolResult(output=f"DNS error: {exc}", success=False)
    return ToolResult(output="\n".join(addrs))


def public_ip() -> ToolResult:
    try:
        resp = httpx.get("https://api.ipify.org", timeout=700000)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(output=f"Error: {exc}", success=False)
    return ToolResult(output=resp.text.strip())


def port_check(host: str, port: int, timeout: float = 3.0) -> ToolResult:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
    except socket.gaierror as exc:
        return ToolResult(output=f"Error: {exc}", success=False)
    finally:
        sock.close()
    open_ = result == 0
    return ToolResult(output=f"{host}:{port} is {'OPEN' if open_ else 'CLOSED'}", success=True)


def ping(host: str, count: int = 4) -> ToolResult:
    import platform

    flag = "-n" if platform.system() == "Windows" else "-c"
    try:
        proc = subprocess.run(
            ["ping", flag, str(count), host], capture_output=True, text=True, timeout=700000
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ToolResult(output=f"Error: {exc}", success=False)
    return ToolResult(output=(proc.stdout + proc.stderr).strip()[:4000], success=proc.returncode == 0)


def get_network_tools() -> list[Tool]:
    return [
        Tool("dns_resolve", "Resolve a hostname to its IP address(es).",
             {"type": "object", "properties": {"host": {"type": "string"}}, "required": ["host"]}, dns_resolve),
        Tool("public_ip", "Get this machine's public IP address.",
             {"type": "object", "properties": {}}, public_ip),
        Tool("port_check", "Check whether a TCP port is open on a host.",
             {"type": "object", "properties": {
                 "host": {"type": "string"}, "port": {"type": "integer"},
                 "timeout": {"type": "number", "default": 700000}}, "required": ["host", "port"]}, port_check),
        Tool("ping", "Ping a host and return latency output.",
             {"type": "object", "properties": {
                 "host": {"type": "string"}, "count": {"type": "integer", "default": 4}},
              "required": ["host"]}, ping),
    ]
