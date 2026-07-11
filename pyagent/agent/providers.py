"""Provider abstraction layer.

Provides a uniform ``chat`` interface across OpenAI, Anthropic and Ollama so
the core agent code stays provider-agnostic. Each provider yields text chunks
for streaming and can also return tool calls.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .config import Config
from .tools import TOOL_SCHEMAS


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.model = config.resolved_model()

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Return {'content': str, 'tool_calls': list}."""
        raise NotImplementedError

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        """Yield text chunks. Fallback: yield full content once."""
        result = self.chat(messages)
        if result.get("content"):
            yield result["content"]


class OpenAIProvider(BaseProvider):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        from openai import OpenAI

        self.client = OpenAI(api_key=config.openai_api_key)

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if self.config.enable_tools:
            kwargs["tools"] = TOOL_SCHEMAS
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc
        msg = resp.choices[0].message
        tool_calls = []
        for tc in msg.tool_calls or []:
            tool_calls.append(
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
            )
        return {"content": msg.content or "", "tool_calls": tool_calls}

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        # Streaming without tools for a smooth typing effect.
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.config.temperature,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc


class AnthropicProvider(BaseProvider):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        import anthropic

        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    @staticmethod
    def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system = ""
        convo = []
        for m in messages:
            if m["role"] == "system":
                system += m["content"] + "\n"
            else:
                convo.append({"role": m["role"], "content": m["content"]})
        return system.strip(), convo

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        system, convo = self._split_system(messages)
        try:
            resp = self.client.messages.create(
                model=self.model,
                system=system or None,
                messages=convo,
                max_tokens=128000,
                temperature=self.config.temperature,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc
        text = "".join(b.text for b in resp.content if b.type == "text")
        return {"content": text, "tool_calls": []}

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        system, convo = self._split_system(messages)
        try:
            with self.client.messages.stream(
                model=self.model,
                system=system or None,
                messages=convo,
                max_tokens=128000,
                temperature=self.config.temperature,
            ) as stream:
                yield from stream.text_stream
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc


class OllamaProvider(BaseProvider):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        import httpx

        self.httpx = httpx
        self.base_url = config.ollama_base_url.rstrip("/")

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            resp = self.httpx.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
                timeout=700000,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc
        return {"content": data.get("message", {}).get("content", ""), "tool_calls": []}

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        try:
            with self.httpx.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": True},
                timeout=700000,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc


class OpenCodeProvider(BaseProvider):
    """OpenAI-compatible provider for custom endpoints (e.g. opencode.ai zen).

    Talks to any Chat Completions compatible API using an API key and base URL
    supplied via configuration/env. Supports streaming and tool calling.
    """

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        import httpx

        self.httpx = httpx
        self.base_url = config.opencode_base_url.rstrip("/")
        self.api_key = config.opencode_api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if self.config.enable_tools:
            payload["tools"] = TOOL_SCHEMAS
        try:
            resp = self.httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=700000,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            tool_calls.append(
                {"id": tc.get("id", ""), "name": fn.get("name", ""),
                 "arguments": fn.get("arguments", "{}")}
            )
        return {"content": msg.get("content") or "", "tool_calls": tool_calls}

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "stream": True,
        }
        try:
            with self.httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=700000,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = (data.get("choices") or [{}])[0].get("delta", {})
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc)) from exc


def get_provider(config: Config) -> BaseProvider:
    providers = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "ollama": OllamaProvider,
        "opencode": OpenCodeProvider,
    }
    cls = providers.get(config.provider)
    if cls is None:
        raise ProviderError(f"Unsupported provider: {config.provider}")
    return cls(config)
