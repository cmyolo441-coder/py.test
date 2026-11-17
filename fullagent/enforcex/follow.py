"""follow — compile the user's system prompt into checkable rules and
verify replies against them, with zero extra model calls.

Pipeline (all deterministic, stdlib only, linear time):

  compile_rules(prompt)  -> [Rule]      cached by prompt hash
  check(reply, rules)    -> [Violation] pure, microseconds
  repair_note(violations)-> str         one short, concrete correction

Only HARD, mechanically checkable rules are compiled; everything else stays
the model's job (the prompt is sent verbatim). Rule kinds:

  forbid      a quoted phrase the prompt says never to say/use/write
  starts      reply must open with a quoted phrase
  ends        reply must close with a quoted phrase
  max_words / max_chars / max_lines / max_sentences
              numeric reply-length caps (only on lines about the reply)
  no_emoji    prompt bans emojis
  no_markdown prompt bans markdown formatting
  lang_latin  reply language is English/Hinglish -> no Devanagari script

False positives cost a retry, so the compiler is conservative: a rule is
emitted only when its sentence clearly addresses the reply and carries an
explicit quoted token or number. Code blocks are ignored for text rules.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

__all__ = ["Rule", "Violation", "compile_rules", "check", "repair_note",
           "describe"]

_REPLY = ("reply", "replies", "response", "responses", "answer", "answers",
          "message", "messages", "output", "outputs")
_QUOTE = r"[\"“”‘’'`«»]([^\"“”‘’'`«»\n]{2,60})[\"“”‘’'`«»]"
_FORBID = re.compile(
    r"(?:never|don'?t|do not|must not|should not|shouldn'?t|avoid|stop|"
    r"mat|kabhi nahi)\s+(?:ever\s+)?(?:say|use|write|output|include|print|"
    r"mention|type|start with|begin with|bolo|likho)?\s*[:\-]?\s*" + _QUOTE,
    re.I)
_STARTS = re.compile(
    r"(?:start|begin|open)\w*\s+(?:every |each |all |your |the )*"
    r"(?:reply|replies|response|responses|answer|answers|message|messages)?"
    r"\s*with\s*[:\-]?\s*" + _QUOTE, re.I)
_ENDS = re.compile(
    r"(?:end|close|finish|conclude)\w*\s+(?:every |each |all |your |the )*"
    r"(?:reply|replies|response|responses|answer|answers|message|messages)?"
    r"\s*with\s*[:\-]?\s*" + _QUOTE, re.I)
_LIMIT = re.compile(
    r"(?:max(?:imum)?(?: of)?|at most|no more than|not more than|under|"
    r"within|limit(?:ed)?(?: to)?|up to|upto|less than|fewer than|"
    r"only|just)\s+(\d{1,5})\s*(words?|characters?|chars?|lines?|"
    r"sentences?)", re.I)
_NO_EMOJI = re.compile(
    r"(?:\bno\b|never|don'?t|do not|avoid|without|zero|ban(?:ned)?)\s+"
    r"(?:use\s+|using\s+|any\s+)?emoji", re.I)
_NO_MD = re.compile(
    r"(?:\bno\b|never|don'?t|do not|avoid|without|plain text)\s*"
    r"(?:use\s+|using\s+|any\s+)?(?:markdown|md formatting|bold|"
    r"headers?|headings?|bullet)", re.I)
_LANG = re.compile(
    r"(?:reply|respond|answer|speak|write|talk|communicate|output)\w*"
    r"(?:\s+\w+){0,4}?\s+(?:in|using)\s+(hinglish|english|roman hindi|"
    r"roman script|latin script)", re.I)
_LANG_STRICT = re.compile(
    r"(?:only|always|strictly|exclusively|sirf|hamesha)", re.I)

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE = re.compile(r"`[^`\n]+`")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    "\U00002B00-\U00002BFF\U0000FE0F\U0001F900-\U0001F9FF]")
_MD = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S|\*\*[^*\n]+\*\*|^\s*\|.+\|\s*$")
_SENT = re.compile(r"[.!?।]+(?:\s|$)")


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    arg: str = ""
    n: int = 0
    source: str = ""


@dataclass(frozen=True)
class Violation:
    rule: Rule
    detail: str


def _addresses_reply(sentence: str) -> bool:
    low = sentence.lower()
    return any(w in low for w in _REPLY) or "you " in low or "tum " in low


def _sentences(text: str) -> List[str]:
    out: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?।])\s+", line):
            part = part.strip()
            if part:
                out.append(part[:400])
    return out


_CACHE: Dict[str, Tuple[Rule, ...]] = {}


def compile_rules(prompt: str, max_rules: int = 60) -> Tuple[Rule, ...]:
    """Compile checkable rules from prompt text. Cached by content hash."""
    if not prompt:
        return ()
    key = hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    rules: List[Rule] = []
    seen = set()

    def add(kind: str, arg: str = "", n: int = 0, src: str = "") -> None:
        k = (kind, arg.lower(), n)
        if k in seen or len(rules) >= max_rules:
            return
        seen.add(k)
        rules.append(Rule(f"F{len(rules) + 1}", kind, arg, n, src[:160]))

    for s in _sentences(prompt):
        for m in _FORBID.finditer(s):
            add("forbid", m.group(1).strip(), 0, s)
        if _addresses_reply(s):
            m = _STARTS.search(s)
            if m:
                add("starts", m.group(1).strip(), 0, s)
            m = _ENDS.search(s)
            if m:
                add("ends", m.group(1).strip(), 0, s)
            m = _LIMIT.search(s)
            if m and any(w in s.lower() for w in _REPLY):
                unit = m.group(2).lower().rstrip("s")
                kind = {"word": "max_words", "character": "max_chars",
                        "char": "max_chars", "line": "max_lines",
                        "sentence": "max_sentences"}.get(unit)
                if kind and int(m.group(1)) > 0:
                    add(kind, "", int(m.group(1)), s)
        if _NO_EMOJI.search(s):
            add("no_emoji", "", 0, s)
        if _NO_MD.search(s) and ("markdown" in s.lower()
                                 or "plain text" in s.lower()):
            add("no_markdown", "", 0, s)
        m = _LANG.search(s)
        if m and m.group(1).lower() in (
                "hinglish", "english", "roman hindi", "roman script",
                "latin script") and _LANG_STRICT.search(s):
            add("lang_latin", m.group(1).lower(), 0, s)

    out = tuple(rules)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def _prose(reply: str) -> str:
    return _INLINE.sub(" ", _FENCE.sub(" ", reply))


def check(reply: str, rules: Tuple[Rule, ...]) -> List[Violation]:
    """Verify a final reply. Returns [] for empty/tool-only replies."""
    if not reply or not reply.strip() or not rules:
        return []
    text = reply.strip()
    prose = _prose(text).strip()
    low = prose.lower()
    out: List[Violation] = []
    for r in rules:
        k = r.kind
        if k == "forbid":
            if r.arg.lower() in low:
                out.append(Violation(r, f'contains forbidden "{r.arg}"'))
        elif k == "starts":
            if not text.lower().lstrip("*_#> \t").startswith(r.arg.lower()):
                out.append(Violation(r, f'must start with "{r.arg}"'))
        elif k == "ends":
            if not text.lower().rstrip("*_ \t\n").endswith(r.arg.lower()):
                out.append(Violation(r, f'must end with "{r.arg}"'))
        elif k == "max_words":
            n = len(prose.split())
            if n > r.n:
                out.append(Violation(r, f"{n} words, limit {r.n}"))
        elif k == "max_chars":
            if len(text) > r.n:
                out.append(Violation(r, f"{len(text)} chars, limit {r.n}"))
        elif k == "max_lines":
            n = len([ln for ln in text.split("\n") if ln.strip()])
            if n > r.n:
                out.append(Violation(r, f"{n} lines, limit {r.n}"))
        elif k == "max_sentences":
            n = len(_SENT.findall(prose))
            if n > r.n:
                out.append(Violation(r, f"{n} sentences, limit {r.n}"))
        elif k == "no_emoji":
            if _EMOJI.search(prose):
                out.append(Violation(r, "contains emoji"))
        elif k == "no_markdown":
            if _MD.search(prose):
                out.append(Violation(r, "contains markdown formatting"))
        elif k == "lang_latin":
            if _DEVANAGARI.search(prose):
                out.append(Violation(
                    r, f"must be in {r.arg} (Roman script), found Devanagari"))
    return out


def repair_note(violations: List[Violation]) -> str:
    """One short corrective user-turn note naming each broken rule."""
    lines = []
    for v in violations[:6]:
        lines.append(f"- {v.detail} (rule: {v.rule.source or v.rule.kind})")
    return ("Your last reply broke rules from your system prompt:\n"
            + "\n".join(lines)
            + "\nRewrite the same answer so it follows them exactly. "
              "Do not mention this note.")


def describe(rules: Tuple[Rule, ...]) -> str:
    if not rules:
        return "no mechanically checkable rules found"
    return "\n".join(
        f"{r.id:<4}{r.kind:<14}{(r.arg or (str(r.n) if r.n else '')):<24}"
        f"<- {r.source}" for r in rules)


def _self_test() -> None:
    p = ('Never say "As an AI". Always respond in Hinglish only. '
         "Keep every reply under 30 words. Don't use emojis. "
         'Start every reply with "Boss,". End every reply with "Jai Hind".')
    rs = compile_rules(p)
    kinds = {r.kind for r in rs}
    assert {"forbid", "max_words", "no_emoji", "starts", "ends",
            "lang_latin"} <= kinds, kinds
    assert compile_rules(p) is rs
    ok = "Boss, kaam ho gaya hai. Jai Hind"
    assert check(ok, rs) == [], check(ok, rs)
    bad = "As an AI, यह काम हो गया 😀"
    got = {v.rule.kind for v in check(bad, rs)}
    assert {"forbid", "no_emoji", "lang_latin", "starts", "ends"} <= got, got
    long = "Boss, " + "word " * 40 + "Jai Hind"
    assert any(v.rule.kind == "max_words" for v in check(long, rs))
    # code is not prose
    code = "Boss, ye lo:\n```\nAs an AI 😀 यह\n```\nJai Hind"
    assert check(code, rs) == [], check(code, rs)
    assert check("", rs) == [] and check("x", ()) == []
    assert compile_rules("") == ()
    assert "Rewrite" in repair_note(check(bad, rs))
    print("follow ok")


if __name__ == "__main__":
    _self_test()
