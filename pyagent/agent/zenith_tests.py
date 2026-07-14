"""Zenith test prioritisation and mutation heuristics."""
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class MutationCandidate:
    path:str; line:int; kind:str; symbol:str
@dataclass
class TestPlan:
    commands:list[str]=field(default_factory=list); targets:list[str]=field(default_factory=list); mutations:list[MutationCandidate]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"commands":self.commands,"targets":self.targets[:20],"mutations":len(self.mutations)}
def mutation_candidates(root:str|Path='.', limit:int=120)->list[MutationCandidate]:
    rp=Path(root); out=[]
    for p in rp.rglob('*.py'):
        if len(out)>=limit or 'tests' in p.parts: continue
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        rel=p.relative_to(rp).as_posix() if p.is_relative_to(rp) else str(p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare): out.append(MutationCandidate(rel,n.lineno,'comparison-boundary','compare'))
            elif isinstance(n, ast.If): out.append(MutationCandidate(rel,n.lineno,'branch-negation','if'))
            elif isinstance(n, ast.BinOp): out.append(MutationCandidate(rel,n.lineno,'operator-swap','binop'))
            if len(out)>=limit: break
    return out
def prioritize_tests(root:str|Path='.', hot_files:list[str]|None=None)->TestPlan:
    rp=Path(root); hot_files=hot_files or []; tests=[p.relative_to(rp).as_posix() for p in rp.rglob('test_*.py') if 'tests' in p.parts or p.name.startswith('test_')]
    targets=[]
    for hf in hot_files:
        stem=Path(hf).stem
        targets += [t for t in tests if stem in Path(t).stem]
    if not targets: targets=tests[:20]
    cmds=['python -m pytest -q'] + ([f'python -m pytest -q {" ".join(targets[:5])}'] if targets else [])
    return TestPlan(cmds, list(dict.fromkeys(targets))[:20], mutation_candidates(rp))
def test_plan_context(plan:TestPlan)->str: return f"test plan commands={plan.commands[:3]} targets={plan.targets[:10]} mutations={len(plan.mutations)}"
