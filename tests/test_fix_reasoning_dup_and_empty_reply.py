"""Bugs from the v4.1.2 install log.

1. Mistral Large 4 (Kios) failed every follow-up turn with
   'duplicate field `reasoning_content`': assistant messages carried the
   same text under both ``reasoning_content`` and ``reasoning``, which a
   serde gateway treats as one field.
2. Gemini 4 Argon returned an empty reply and the turn ended silently
   with "✦ done" and nothing on screen.
"""
import os
import tempfile
import unittest

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="fix_reasoning_dup_"))

from fullagent import client  # noqa: E402
from fullagent.config import EFFORTS, MODELS  # noqa: E402


def _model(mid):
    return next(m for m in MODELS if m.id == mid)


class ReasoningKeyDedupe(unittest.TestCase):
    def test_assistant_message_sets_one_key(self):
        msg = client.assistant_message("hi", [], "thought")
        self.assertEqual(msg["reasoning_content"], "thought")
        self.assertNotIn("reasoning", msg)

    def test_payload_never_has_both_keys(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi!",
             "reasoning_content": "greet", "reasoning": "greet"},
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": "x", "reasoning": "legacy"},
            {"role": "user", "content": "next"},
        ]
        effort = next(iter(EFFORTS.values())) if isinstance(EFFORTS, dict) \
            else EFFORTS[0]
        payload = client.build_payload(_model("mistral-large-4"), effort,
                                       messages, None)
        for m in payload["messages"]:
            self.assertFalse("reasoning" in m and "reasoning_content" in m, m)
            self.assertNotIn("reasoning", m)
        # a lone legacy key keeps its thinking under the canonical name
        self.assertTrue(any(m.get("reasoning_content") == "legacy"
                            for m in payload["messages"]))

    def test_duplicate_error_is_healable(self):
        err = client.APIError(
            "bad_response_status_code: Failed to deserialize the JSON body "
            "into the target type: duplicate field `reasoning_content` at "
            "line 1 column 21625")
        self.assertTrue(client._is_reasoning_content_error(err))


if __name__ == "__main__":
    unittest.main()
