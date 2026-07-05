"""The agent core: the reasoning + tool-execution loop.

The agent sends the conversation to the LLM, executes any requested tools, feeds
results back, and repeats until the model produces a final answer or the
iteration budget is exhausted.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .cancellation import CancellationToken
from .config import Config
from .guardrails import Guardrails
from .memory import Conversation
from .providers import LLMProvider, LLMResponse
from .providers.base import ToolCall
from .tools import ToolRegistry


from .cancellation import StopStreaming as _StopStreaming


class ToolApprovalDenied(Exception):
    pass


class Agent:
    def __init__(
        self,
        config: Config,
        provider: LLMProvider,
        registry: ToolRegistry,
        conversation: Conversation,
    ) -> None:
        self.config = config
        self.provider = provider
        self.registry = registry
        self.conversation = conversation
        self.guardrails = Guardrails(max_calls_per_turn=config.max_tool_iterations * 3)
        self._partial = ""  # text streamed so far in the current round

    def _wrap_delta(self, on_delta, cancel_token):
        """Wrap the delta callback so it tracks partial text and honors ESC."""
        if on_delta is None and cancel_token is None:
            return None

        def _cb(chunk: str) -> None:
            self._partial += chunk
            if cancel_token is not None and cancel_token.cancelled:
                # Signal the provider stream to stop as soon as possible.
                raise _StopStreaming()
            if on_delta is not None:
                on_delta(chunk)

        return _cb

    def _tool_schemas(self) -> list[dict[str, Any]] | None:
        if not self.config.enable_tools:
            return None
        provider = self.config.provider.lower()
        if provider == "anthropic":
            return self.registry.anthropic_schemas()
        return self.registry.openai_schemas()

    def send(
        self,
        user_input: str,
        on_delta: Callable[[str], None] | None = None,
        on_tool_start: Callable[[ToolCall], bool] | None = None,
        on_tool_result: Callable[[ToolCall, str, bool], None] | None = None,
        on_thinking: Callable[[int], None] | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        """Process one user turn, running the full tool loop.

        Callbacks:
            on_delta: streamed text chunks for live rendering.
            on_tool_start: return False to deny a tool; return True to allow.
            on_tool_result: notified after each tool executes.
            on_thinking: notified before each LLM round with the iteration index.
            cancel_token: cooperative cancellation flag (set by pressing ESC).
                When tripped, streaming stops and the partial answer is kept.
        """
        self.conversation.add_user(user_input)
        self.guardrails.start_turn()
        tools = self._tool_schemas()
        final_text = ""

        # Wrap on_delta so a cancelled turn stops emitting/streaming tokens.
        wrapped_delta = self._wrap_delta(on_delta, cancel_token)

        for iteration in range(self.config.max_tool_iterations):
            if cancel_token is not None and cancel_token.cancelled:
                final_text = self._partial or final_text
                self.conversation.add_assistant(final_text)
                break
            if on_thinking:
                on_thinking(iteration)

            self._partial = ""
            response: LLMResponse = self.provider.chat(
                self.conversation.messages,
                tools=tools,
                on_delta=wrapped_delta,
            )

            # Stopped mid-stream by the user: keep whatever we have.
            if cancel_token is not None and cancel_token.cancelled:
                final_text = response.content or self._partial
                self.conversation.add_assistant(final_text)
                break

            if not response.wants_tools:
                self.conversation.add_assistant(response.content)
                final_text = response.content
                break

            # Record the assistant's tool-call turn.
            self._record_assistant_tool_calls(response)

            # Execute each requested tool.
            cancelled_mid_tools = False
            for tc in response.tool_calls:
                # Honor ESC between tools so a long multi-tool round stops
                # promptly instead of running every remaining tool first.
                if cancel_token is not None and cancel_token.cancelled:
                    cancelled_mid_tools = True
                    break
                allowed = True
                tool = self.registry.get(tc.name)

                # Independent guardrail layer (budget + dangerous command scan).
                decision = self.guardrails.check(tc.name, tc.arguments)
                if not decision.allow:
                    msg = f"Blocked by guardrails: {decision.reason}"
                    self._add_tool_output(tc, msg, success=False)
                    if on_tool_result:
                        on_tool_result(tc, msg, False)
                    continue

                needs_approval = (
                    (tool is not None and tool.dangerous) or decision.force_confirm
                ) and not self.config.auto_approve_tools
                if needs_approval and on_tool_start is not None:
                    allowed = on_tool_start(tc)
                elif on_tool_start is not None:
                    on_tool_start(tc)

                if not allowed:
                    self._add_tool_output(tc, "Tool execution denied by user.", success=False)
                    if on_tool_result:
                        on_tool_result(tc, "Denied by user.", False)
                    continue

                result = self.registry.execute(tc.name, tc.arguments)
                self._add_tool_output(tc, result.as_message(), result.success)
                if on_tool_result:
                    on_tool_result(tc, result.output, result.success)

            # ESC pressed during/after tool execution: stop before the next
            # LLM round so the turn ends promptly.
            if cancelled_mid_tools or (cancel_token is not None and cancel_token.cancelled):
                final_text = self._partial or final_text or "(stopped)"
                self.conversation.add_assistant(final_text)
                break
        else:
            final_text = "(Reached max tool iterations without a final answer.)"

        self.conversation.save()
        return final_text

    # ------------------------------------------------------------------
    def _record_assistant_tool_calls(self, response: LLMResponse) -> None:
        provider = self.config.provider.lower()
        if provider == "anthropic":
            content_blocks: list[dict[str, Any]] = []
            if response.content:
                content_blocks.append({"type": "text", "text": response.content})
            for tc in response.tool_calls:
                content_blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            self.conversation.messages.append({"role": "assistant", "content": content_blocks})
        else:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ]
            self.conversation.add_assistant(response.content, tool_calls=tool_calls)

    def _add_tool_output(self, tc: ToolCall, output: str, success: bool) -> None:
        provider = self.config.provider.lower()
        if provider == "anthropic":
            self.conversation.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": output,
                            "is_error": not success,
                        }
                    ],
                }
            )
        else:
            self.conversation.add_tool_result(tc.id, tc.name, output)
