"""Zenith static performance-smell analyzer."""
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class PerfFinding:
    path:str; line:int; kind:str; message:str; score:int=1
@dataclass
class PerfReport:
    findings:list[PerfFinding]=field(default_factory=list); files:int=0
    def stats(self)->dict[str,Any]: return {"files":self.files,"findings":len(self.findings),"top":[f.__dict__ for f in sorted(self.findings,key=lambda x:-x.score)[:15]]}
def analyze_performance(root:str|Path='.', max_files:int=700)->PerfReport:
    rp=Path(root); rep=PerfReport()
    for p in rp.rglob('*.py'):
        if rep.files>=max_files or any(x in p.parts for x in ['.git','__pycache__','venv','.venv']): continue
        rep.files+=1
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        rel=p.relative_to(rp).as_posix() if p.is_relative_to(rp) else str(p)
        for n in ast.walk(tree):
            if isinstance(n,(ast.For,ast.While)):
                nested=sum(isinstance(c,(ast.For,ast.While)) for c in ast.walk(n))-1
                if nested>0: rep.findings.append(PerfFinding(rel,n.lineno,'nested-loop',f'{nested+1} loop depth',5+nested))
            if isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add) and isinstance(n.target, ast.Name):
                rep.findings.append(PerfFinding(rel,n.lineno,'repeated-concat','possible repeated += in loop/context',3))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {'list','dict','set'}:
                rep.findings.append(PerfFinding(rel,n.lineno,'container-copy',f'{n.func.id}() copy/allocation',1))
    return rep
def perf_context(rep:PerfReport)->str: return f"performance findings={len(rep.findings)} top=" + '; '.join(f'{f.kind}@{f.path}:{f.line}' for f in sorted(rep.findings,key=lambda x:-x.score)[:10])
