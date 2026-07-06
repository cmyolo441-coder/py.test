"""MCP (Model Context Protocol) server — expose agent tools to MCP clients.

Implements a minimal MCP server so the agent's tools can be consumed by
other MCP-compatible clients (Claude Desktop, VS Code, etc.).

The server:
  * Lists available tools via JSON-RPC
  * Executes tool calls and returns results
  * Runs over stdio (the standard MCP transport)

Note: this is a minimal, real implementation. For full MCP spec
compliance, install the official `mcp` Python package.
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any

from .tools import build_default_registry


class McpServer:
    """Minimal MCP server over stdio."""

    def __init__(self) -> None:
        self.registry = build_default_registry()
        self._running = False
        self._thread: threading.Thread | None = None

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a single JSON-RPC request and return the response."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "terminal-agent-mcp", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            }
        if method == "tools/list":
            tools = []
            for tool in self.registry.all():
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters,
                })
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = self.registry.execute(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result.as_message()}],
                    "isError": not result.success,
                },
            }
        if method == "shutdown":
            self._running = False
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        # Unknown method.
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def run_stdio(self) -> None:
        """Run the MCP server over stdio (blocking)."""
        self._running = True
        sys.stderr.write("MCP server running on stdio\n")
        sys.stderr.flush()
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                request = json.loads(line)
                response = self.handle_request(request)
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
            except json.JSONDecodeError:
                continue
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"MCP error: {exc}\n")
                sys.stderr.flush()

    def call_tool_directly(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Convenience method for testing — call a tool without JSON-RPC."""
        result = self.registry.execute(tool_name, arguments)
        return {
            "output": result.as_message(),
            "success": result.success,
            "metadata": result.metadata,
        }

    def list_tools_as_mcp(self) -> list[dict[str, Any]]:
        """Return the tool list in MCP format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.parameters,
            }
            for t in self.registry.all()
        ]


_server: McpServer | None = None


def get_mcp_server() -> McpServer:
    global _server
    if _server is None:
        _server = McpServer()
    return _server
