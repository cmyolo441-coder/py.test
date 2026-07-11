"""Cooperative cancellation for long-running model turns.

Pressing ``Esc`` while the model is generating should stop the response cleanly.
This module provides:

  * ``CancellationToken`` - a thread-safe flag the provider/agent poll while
    streaming. When set, the streaming loop stops requesting/emitting tokens and
    the turn ends gracefully (whatever was produced so far is kept).
  * ``EscListener`` - a background listener that watches stdin for the ESC key
    (byte ``0x1b``) and trips a token. It works on both POSIX (termios/tty raw
    mode) and Windows (msvcrt), and degrades to a no-op where neither is
    available (e.g. piped stdin), so it never breaks non-interactive use.

The listener is intentionally conservative: it only reacts to a *lone* ESC
press, not to ESC-prefixed escape sequences (arrow keys, etc.), so normal
terminal traffic won't accidentally cancel a turn.
"""

from __future__ import annotations

import sys
import threading
import time


class CancellationToken:
    """A thread-safe, resettable cancellation flag."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __bool__(self) -> bool:  # allow `if token:`
        return self._event.is_set()


# --------------------------------------------------------------------------- #
# ESC key listener
# --------------------------------------------------------------------------- #
class EscListener:
    """Background watcher that sets a token when the user presses ESC.

    Use as a context manager around a model turn:

        token = CancellationToken()
        with EscListener(token):
            agent.send(..., cancel_token=token)

    Cross-platform, self-disabling when stdin is not an interactive TTY.
    """

    def __init__(self, token: CancellationToken, on_cancel=None) -> None:
        self.token = token
        self._on_cancel = on_cancel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._interactive = self._stdin_is_tty()

    @staticmethod
    def _stdin_is_tty() -> bool:
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except Exception:  # noqa: BLE001
            return False

    # -- context manager ------------------------------------------------
    def __enter__(self) -> EscListener:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        if not self._interactive:
            return
        self._stop.clear()
        if sys.platform == "win32":
            target = self._run_windows
        else:
            target = self._run_posix
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
            self._thread = None

    # -- platform loops -------------------------------------------------
    def _trip(self) -> None:
        self.token.cancel()
        if self._on_cancel is not None:
            try:
                self._on_cancel()
            except Exception:  # noqa: BLE001
                pass

    def _run_windows(self) -> None:  # pragma: no cover - platform specific
        try:
            import msvcrt
        except Exception:  # noqa: BLE001
            return
        while not self._stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\x1b":  # ESC
                    self._trip()
                    return
            time.sleep(0.03)

    def _run_posix(self) -> None:  # pragma: no cover - platform specific
        try:
            import select
            import termios
            import tty
        except Exception:  # noqa: BLE001
            return

        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except Exception:  # noqa: BLE001
            return
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if not r:
                    continue
                ch = sys.stdin.read(1)
                if ch != "\x1b":
                    continue
                # Distinguish a lone ESC from an escape sequence (e.g. arrows):
                # if more bytes follow immediately, it's a sequence -> ignore.
                r2, _, _ = select.select([sys.stdin], [], [], 0.02)
                if r2:
                    # Drain the sequence body so it doesn't leak into the prompt.
                    try:
                        sys.stdin.read(1)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                self._trip()
                return
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:  # noqa: BLE001
                pass


class CancelledByUser(Exception):
    """Raised internally when a turn is cancelled by the user (ESC)."""


class StopStreaming(Exception):
    """Signal raised from a delta callback to stop a live provider stream.

    Providers that stream tokens should catch this, close the stream and return
    whatever content was accumulated so far.
    """
