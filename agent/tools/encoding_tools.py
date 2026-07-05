"""Encoding / hashing tools: base64, hex, url, hashing."""

from __future__ import annotations

import base64
import hashlib
import urllib.parse

from .base import Tool, ToolResult


def b64_encode(text: str) -> ToolResult:
    return ToolResult(output=base64.b64encode(text.encode("utf-8")).decode("ascii"))


def b64_decode(text: str) -> ToolResult:
    try:
        return ToolResult(output=base64.b64decode(text).decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(output=f"Decode error: {exc}", success=False)


def hex_encode(text: str) -> ToolResult:
    return ToolResult(output=text.encode("utf-8").hex())


def url_encode(text: str) -> ToolResult:
    return ToolResult(output=urllib.parse.quote(text))


def url_decode(text: str) -> ToolResult:
    return ToolResult(output=urllib.parse.unquote(text))


def hash_text(text: str, algorithm: str = "sha256") -> ToolResult:
    algorithm = algorithm.lower()
    if algorithm not in hashlib.algorithms_available:
        return ToolResult(output=f"Unknown algorithm: {algorithm}", success=False)
    digest = hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
    return ToolResult(output=digest, metadata={"algorithm": algorithm})


def get_encoding_tools() -> list[Tool]:
    return [
        Tool("b64_encode", "Base64-encode a UTF-8 string.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, b64_encode),
        Tool("b64_decode", "Base64-decode a string.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, b64_decode),
        Tool("hex_encode", "Hex-encode a UTF-8 string.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, hex_encode),
        Tool("url_encode", "URL-encode a string.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, url_encode),
        Tool("url_decode", "URL-decode a string.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, url_decode),
        Tool("hash_text", "Compute a cryptographic hash (md5/sha1/sha256/sha512).",
             {"type": "object", "properties": {"text": {"type": "string"}, "algorithm": {"type": "string", "default": "sha256"}}, "required": ["text"]}, hash_text),
    ]
