"""weave - final wire layout. THE PROMPT IS SACRED: first, verbatim,
byte-for-byte. Attention layers ride strictly AFTER it. Nothing is
inserted before or inside the prompt."""
from __future__ import annotations

NL = chr(10)
DIGEST_HEAD = "## Your prompt, distilled (the full text above remains authoritative)"
ANCHOR_HEAD = "## Active for this turn"


def compose_wire(prompt: str, digest_text: str = "", anchor_text: str = "") -> str:
    if not prompt:
        return ""
    parts = [prompt]
    if digest_text:
        parts.append(NL + NL + DIGEST_HEAD + NL + digest_text)
    if anchor_text:
        parts.append(NL + NL + ANCHOR_HEAD + NL + anchor_text)
    return "".join(parts)


def _self_test() -> None:
    p = "You are X." + NL + "Always be brief."
    out = compose_wire(p, "Core identity:" + NL + "- You are X.", "still in force:" + NL + "- Always be brief.")
    assert out.startswith(p)
    assert out.count("You are X.") == 2
    assert out.index(DIGEST_HEAD) < out.index(ANCHOR_HEAD)
    assert compose_wire(p) == p
    assert compose_wire("") == ""
    print("weave ok")


if __name__ == "__main__":
    _self_test()
