"""Headless mode — run the agent without TUI for CI/CD and scripting.

Inspired by gsd-pi's headless orchestrator, this module provides:
  - Non-interactive agent execution
  - JSON/text output modes
  - Session resume capability
  - Timeout handling
  - Progress streaming to stderr

Usage:
    from agent.headless import run_headless

    result = run_headless(
        prompt="Build a REST API",
        provider="openai",
        model="gpt-4o",
        timeout=300,
        output_format="json",
    )
"""
from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class HeadlessResult:
    """Result of a headless agent run."""
    success: bool
    output: str
    duration_s: float
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "duration_s": self.duration_s,
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "error": self.error,
            "session_id": self.session_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class HeadlessOptions:
    """Options for headless execution."""
    timeout: int = 300  # seconds
    output_format: str = "text"  # "text" | "json" | "stream-json"
    model: str | None = None
    provider: str | None = None
    verbose: bool = False
    resume_session: str | None = None
    context: str | None = None  # file path or inline text
    auto_compact: bool = True


def _emit_json(event: dict[str, Any], file: Any = None) -> None:
    """Emit a JSON event to stdout or a file."""
    target = file or sys.stdout
    target.write(json.dumps(event) + "\n")
    target.flush()


def _emit_progress(message: str, file: Any = None) -> None:
    """Emit a progress message to stderr."""
    target = file or sys.stderr
    target.write(f"[headless] {message}\n")
    target.flush()


def run_headless(
    prompt: str,
    options: HeadlessOptions | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_tool_start: Callable[[str], bool] | None = None,
    on_tool_result: Callable[[str, str, bool], None] | None = None,
) -> HeadlessResult:
    """Run the agent in headless mode (no TUI).

    Args:
        prompt: The user prompt to process.
        options: Headless execution options.
        on_delta: Callback for streaming text chunks.
        on_tool_start: Callback when a tool starts (return False to deny).
        on_tool_result: Callback after tool execution.

    Returns:
        HeadlessResult with the agent's output and metadata.
    """
    from .app import App

    if options is None:
        options = HeadlessOptions()

    start_time = time.time()
    output_file = sys.stderr if options.output_format == "stream-json" else None

    _emit_progress(f"Starting headless run with timeout={options.timeout}s", output_file)

    # Create app instance
    app = App()

    # Apply options
    if options.provider:
        app.config.provider = options.provider
    if options.model:
        app.config.model = options.model
    app.config.auto_compact = options.auto_compact

    # Build agent
    if not app.build_agent():
        return HeadlessResult(
            success=False,
            output="",
            duration_s=time.time() - start_time,
            error="Failed to initialize agent — check provider credentials",
        )

    # Resume session if requested
    if options.resume_session:
        _emit_progress(f"Resuming session: {options.resume_session}", output_file)
        # TODO: Implement session resume from store

    # Add context if provided
    if options.context:
        if options.context == "-":
            # Read from stdin
            context_text = sys.stdin.read()
        else:
            # Read from file
            try:
                from pathlib import Path
                context_text = Path(options.context).read_text(encoding="utf-8")
            except Exception as e:
                return HeadlessResult(
                    success=False,
                    output="",
                    duration_s=time.time() - start_time,
                    error=f"Failed to read context file: {e}",
                )

        # Add context to conversation
        app.conversation.add_user(f"Context:\n{context_text}")
        app.conversation.add_assistant("Context received. Ready to process your request.")

    # Stream JSON mode
    if options.output_format == "stream-json":
        _emit_json({"type": "start", "prompt": prompt[:100]}, output_file)

    # Run the turn
    tool_call_count = 0

    def count_tool_start(tc: Any) -> bool:
        nonlocal tool_call_count
        tool_call_count += 1
        if options.verbose:
            _emit_progress(f"Tool call #{tool_call_count}: {tc.name}", output_file)
        if on_tool_start:
            return on_tool_start(tc.name)
        return True

    def count_tool_result(tc: Any, result: str, success: bool) -> None:
        if options.verbose:
            status = "✓" if success else "✗"
            _emit_progress(f"Tool {tc.name}: {status}", output_file)
        if on_tool_result:
            on_tool_result(tc.name, result, success)

    try:
        # Set a timeout signal
        import signal

        def timeout_handler(signum: int, frame: Any) -> None:
            raise TimeoutError(f"Headless run timed out after {options.timeout}s")

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(options.timeout)

        try:
            final = app.agent.send(
                prompt,
                on_delta=on_delta,
                on_tool_start=count_tool_start,
                on_tool_result=count_tool_result,
            )
        finally:
            signal.alarm(0)  # Cancel alarm
            signal.signal(signal.SIGALRM, old_handler)

        duration = time.time() - start_time
        _emit_progress(f"Completed in {duration:.2f}s ({tool_call_count} tool calls)", output_file)

        # Stream JSON completion
        if options.output_format == "stream-json":
            _emit_json({
                "type": "complete",
                "output": final,
                "duration_s": duration,
                "tool_calls": tool_call_count,
            }, output_file)

        # Save session
        app.conversation.save()

        return HeadlessResult(
            success=True,
            output=final,
            duration_s=duration,
            tool_calls=tool_call_count,
            session_id=getattr(app.conversation, "session_id", None),
        )

    except TimeoutError as e:
        duration = time.time() - start_time
        _emit_progress(f"Timed out after {duration:.2f}s", output_file)

        if options.output_format == "stream-json":
            _emit_json({
                "type": "error",
                "error": str(e),
                "duration_s": duration,
            }, output_file)

        return HeadlessResult(
            success=False,
            output="",
            duration_s=duration,
            error=str(e),
        )

    except Exception as e:
        duration = time.time() - start_time
        _emit_progress(f"Error: {type(e).__name__}: {e}", output_file)

        if options.output_format == "stream-json":
            _emit_json({
                "type": "error",
                "error": f"{type(e).__name__}: {e}",
                "duration_s": duration,
            }, output_file)

        return HeadlessResult(
            success=False,
            output="",
            duration_s=duration,
            error=f"{type(e).__name__}: {e}",
        )


def run_headless_batch(
    prompts: list[str],
    options: HeadlessOptions | None = None,
    parallel: bool = False,
    max_workers: int = 4,
) -> list[HeadlessResult]:
    """Run multiple prompts in headless mode.

    Args:
        prompts: List of prompts to process.
        options: Headless execution options (shared across all runs).
        parallel: If True, run prompts in parallel using threads.
        max_workers: Maximum number of parallel workers.

    Returns:
        List of HeadlessResult for each prompt.
    """
    if not parallel:
        return [run_headless(p, options) for p in prompts]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[HeadlessResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run_headless, p, options): p for p in prompts}

        for future in as_completed(futures):
            futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(HeadlessResult(
                    success=False,
                    output="",
                    duration_s=0,
                    error=f"{type(e).__name__}: {e}",
                ))

    return results


# CLI entry point for headless mode
def main() -> None:
    """CLI entry point for headless mode."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the agent in headless mode (no TUI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    agent-headless "What is Python?"
    agent-headless --json --timeout 60 "Build a REST API"
    cat prompt.txt | agent-headless --context -
        """,
    )
    parser.add_argument("prompt", help="The prompt to process")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--stream-json", action="store_true", help="Output as stream JSON")
    parser.add_argument("--provider", help="Override provider")
    parser.add_argument("--model", help="Override model")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show tool calls")
    parser.add_argument("--context", help="Context file path or '-' for stdin")

    args = parser.parse_args()

    options = HeadlessOptions(
        timeout=args.timeout,
        output_format="stream-json" if args.stream_json else ("json" if args.json else "text"),
        provider=args.provider,
        model=args.model,
        verbose=args.verbose,
        context=args.context,
    )

    result = run_headless(args.prompt, options)

    if args.json or args.stream_json:
        print(result.to_json())
    else:
        if result.success:
            print(result.output)
        else:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
