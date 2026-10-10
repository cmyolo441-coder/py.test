"""Validation of the crew's _chat wrapper — PROOF, not promises.

User report: subagents failing with
    AttributeError: 'str' object has no attribute ...
Root cause: crew._run_loop called self._chat(...) and immediately used
result.usage / result.tool_calls / result.content. A chat callable that
returned None, a plain string, a dict, or a StreamResult with
tool_calls=None crashed the loop with a cryptic AttributeError.

The fix (fullagent/crew.py, Crew._checked_chat): the reply is validated
BEFORE the loop touches it; every shape violation becomes a CrewError
that names the offending type, and the agent lands in 'error' with that
clear message instead of AttributeError.

This test PROVES it:
  1. unit: _checked_chat raises CrewError (not AttributeError) for
     None / "string" / {"dict": 1} / StreamResult(tool_calls=None) /
     objects missing attributes, and passes a valid reply through
     unchanged;
  2. integration: _run_loop with a garbage chat lands the agent in
     state 'error' whose message names the type — never AttributeError;
  3. regression: should_cancel is still threaded through to chat
     callables that accept it, and old callables without the kwarg
     still work.

Run:  python3 -m pytest tests/test_crew_chat_validation.py -q
   or: python3 -m unittest tests.test_crew_chat_validation -v
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fullagent.client import StreamResult
from fullagent.crew import Crew, CrewAgent, CrewError
from fullagent.kernel import EventLog


def _make_crew(chat, max_agents=2):
    log = EventLog(Path(tempfile.mkdtemp()) / "k.jsonl")
    provider = SimpleNamespace(key="t", name="T", base_url="http://t",
                               api_key="sk-fake", color="#fff")
    model = SimpleNamespace(id="stub", provider="t", label="Stub",
                            supports_tools=False, supports_reasoning=False)
    effort = SimpleNamespace(key="low", label="LOW", color="#fff",
                             max_tokens=100, temperature=0.0,
                             reasoning_effort=None)
    return Crew(log, provider, model, effort, chat=chat,
                max_agents=max_agents)


def _make_agent():
    agent = CrewAgent(id="crew-1", nickname="nova", role="coder",
                      task="unit test task")
    agent.messages = [{"role": "user", "content": "YOUR TASK: do it"}]
    agent.generation = 1
    agent.state = "running"
    return agent


def _run_loop_sync(crew, agent, max_steps=3):
    """Drive the worker loop synchronously (no pool threads) so the
    verdict is deterministic."""
    crew._run_loop(agent, read_only=False, max_steps=max_steps, gen=1)
    crew.shutdown(wait=False)
    return agent


class ChatValidationUnitTest(unittest.TestCase):
    """_checked_chat raises CrewError with a clear message on garbage."""

    def _crew(self, ret):
        def chat(provider, model, effort, messages, schemas, timeout):
            return ret
        return _make_crew(chat)

    def test_none_rejected(self):
        crew = self._crew(None)
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("returned None", str(cm.exception))
        crew.shutdown(wait=False)

    def test_string_rejected(self):
        crew = self._crew("just a string reply")
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("returned a str", str(cm.exception))
        self.assertNotIn("AttributeError", str(cm.exception))
        crew.shutdown(wait=False)

    def test_dict_rejected(self):
        crew = self._crew({"dict": 1})
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("returned a dict", str(cm.exception))
        crew.shutdown(wait=False)

    def test_streamresult_with_none_tool_calls_rejected(self):
        # NOTE: the real StreamResult normalizes tool_calls=None -> []
        # in __post_init__, so the strict boundary rejection is
        # exercised with a duck-typed result (a custom chat callable
        # that does not normalize) — exactly the shape that used to
        # crash the loop with AttributeError.
        bad = SimpleNamespace(content="x", reasoning="", tool_calls=None,
                              finish_reason="stop", usage=None, model="s")
        crew = self._crew(bad)
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("tool_calls", str(cm.exception))
        crew.shutdown(wait=False)

    def test_missing_attributes_rejected(self):
        crew = self._crew(SimpleNamespace(content="x", tool_calls=[]))
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("missing", str(cm.exception))
        self.assertIn("usage", str(cm.exception))
        crew.shutdown(wait=False)

    def test_bad_tool_calls_type_rejected(self):
        crew = self._crew(SimpleNamespace(content="x", tool_calls="nope",
                                          usage=None))
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("tool_calls", str(cm.exception))
        crew.shutdown(wait=False)

    def test_bad_content_type_rejected(self):
        crew = self._crew(SimpleNamespace(content={"not": "a string"},
                                          tool_calls=[], usage=None))
        with self.assertRaises(CrewError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertIn("content", str(cm.exception))
        crew.shutdown(wait=False)

    def test_valid_streamresult_passes_through(self):
        ok = StreamResult(content="STATUS: DONE\nSUMMARY: ok",
                          tool_calls=[], usage={"prompt_tokens": 1})
        crew = self._crew(ok)
        got = crew._checked_chat(None, None, [], None, 1.0)
        self.assertIs(got, ok)  # unchanged, not wrapped/copied
        crew.shutdown(wait=False)

    def test_duck_typed_stub_passes_through(self):
        # the crew's own self-test stubs are SimpleNamespaces, not
        # StreamResults — they must keep working
        ok = SimpleNamespace(content="x", reasoning="", tool_calls=[],
                             finish_reason="stop", usage=None)
        crew = self._crew(ok)
        self.assertIs(crew._checked_chat(None, None, [], None, 1.0), ok)
        crew.shutdown(wait=False)

    def test_chat_own_errors_untouched(self):
        """Provider errors raised BY the chat callable propagate
        unchanged — the wrapper must not mask retry/cancel semantics."""
        def boom(provider, model, effort, messages, schemas, timeout):
            raise RuntimeError("provider exploded")
        crew = _make_crew(boom)
        with self.assertRaises(RuntimeError) as cm:
            crew._checked_chat(None, None, [], None, 1.0)
        self.assertEqual(str(cm.exception), "provider exploded")
        crew.shutdown(wait=False)


class ChatValidationLoopTest(unittest.TestCase):
    """Integration: a garbage chat lands the agent in 'error' with a
    clear CrewError message — never an AttributeError."""

    def _assert_clear_error(self, ret, needle):
        def chat(provider, model, effort, messages, schemas, timeout):
            return ret
        crew = _make_crew(chat)
        agent = _run_loop_sync(crew, _make_agent())
        self.assertEqual(agent.state, "error", agent.to_dict())
        self.assertIn("CrewError", agent.error, agent.error)
        self.assertIn(needle, agent.error, agent.error)
        self.assertNotIn("AttributeError", agent.error, agent.error)
        self.assertNotIn("has no attribute", agent.error, agent.error)
        return agent.error

    def test_none_chat_lands_clear_error(self):
        err = self._assert_clear_error(None, "returned None")
        print(f"\nNone -> agent.error: {err}")

    def test_string_chat_lands_clear_error(self):
        # the exact user-reported failure: 'str' object has no attribute
        err = self._assert_clear_error("garbage string", "returned a str")
        print(f"\nstr  -> agent.error: {err}")

    def test_dict_chat_lands_clear_error(self):
        err = self._assert_clear_error({"dict": 1}, "returned a dict")
        print(f"\ndict -> agent.error: {err}")

    def test_none_tool_calls_lands_clear_error(self):
        # NOTE: real StreamResult normalizes tool_calls=None -> [] in
        # __post_init__; use a duck-typed result so the boundary
        # rejection (not the dataclass coercion) is what lands the
        # agent in 'error' with a clear message.
        bad = SimpleNamespace(content="x", reasoning="", tool_calls=None,
                              finish_reason="stop", usage=None, model="s")
        err = self._assert_clear_error(bad, "tool_calls")
        print(f"\ntool_calls=None -> agent.error: {err}")

    def test_valid_chat_still_completes(self):
        def chat(provider, model, effort, messages, schemas, timeout):
            return SimpleNamespace(
                content="STATUS: DONE\nSUMMARY: all good", reasoning="",
                tool_calls=[], finish_reason="stop", usage=None)
        crew = _make_crew(chat)
        agent = _run_loop_sync(crew, _make_agent())
        self.assertEqual(agent.state, "done", agent.to_dict())
        self.assertIn("all good", agent.summary)


class ShouldCancelThreadingTest(unittest.TestCase):
    """Regression: the validation wrapper preserves the cancel path."""

    def test_should_cancel_reaches_cancel_aware_chat(self):
        seen = {}

        def chat(provider, model, effort, messages, schemas, timeout,
                 should_cancel=None):
            seen["should_cancel"] = should_cancel
            return SimpleNamespace(content="STATUS: DONE\nSUMMARY: ok",
                                   reasoning="", tool_calls=[],
                                   finish_reason="stop", usage=None)
        crew = _make_crew(chat)
        self.assertTrue(crew._chat_takes_cancel)
        agent = _run_loop_sync(crew, _make_agent())
        self.assertEqual(agent.state, "done")
        self.assertTrue(callable(seen["should_cancel"]),
                        "should_cancel must be threaded into the chat call")

    def test_chat_without_cancel_kwarg_still_works(self):
        def chat(provider, model, effort, messages, schemas, timeout):
            return SimpleNamespace(content="STATUS: DONE\nSUMMARY: ok",
                                   reasoning="", tool_calls=[],
                                   finish_reason="stop", usage=None)
        crew = _make_crew(chat)
        self.assertFalse(crew._chat_takes_cancel)
        agent = _run_loop_sync(crew, _make_agent())
        self.assertEqual(agent.state, "done", agent.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
