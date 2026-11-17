"""python -m fullagent.enforcex [show|rules|check TEXT]"""
import sys

from . import compile_rules, check, find_user_prompt
from .follow import describe


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else "show"
    text, path = find_user_prompt()
    if not text:
        print("SYSTEM_PROMPT slot in fullagent/systemprompt.py is empty "
              "(built-in default prompt in use).")
        return 1
    rules = compile_rules(text)
    if cmd == "show":
        print(f"loaded {len(text):,} chars from {path}\n"
              f"{len(rules)} checkable rule(s):\n{describe(rules)}")
        return 0
    if cmd == "rules":
        print(describe(rules))
        return 0
    if cmd == "check":
        reply = " ".join(a[1:]) or sys.stdin.read()
        vs = check(reply, rules)
        for v in vs:
            print(f"VIOLATION {v.rule.id} {v.rule.kind}: {v.detail}")
        print("OK" if not vs else f"{len(vs)} violation(s)")
        return 1 if vs else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
