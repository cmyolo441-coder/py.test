"""Quantum reliability risk model (non-destructive static heuristics)."""
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
_SKIP={'.git','__pycache__','.venv','venv','node_modules','.pytest_cache','dist','build'}
@dataclass
class RiskItem:
    path:str; line:int; kind:str; score:int; detail:str
@dataclass
class RiskReport:
    items:list[RiskItem]=field(default_factory=list); files:int=0
    def stats(self)->dict[str,Any]: return {"files":self.files,"items":len(self.items),"top":[i.__dict__ for i in sorted(self.items,key=lambda x:-x.score)[:20]]}
def _rel(p:Path,r:Path)->str:
    try:return p.relative_to(r).as_posix()
    except ValueError:return p.as_posix()
def analyze_risk(root:str|Path='.',max_files:int=900)->RiskReport:
    rp=Path(root).resolve(); rep=RiskReport()
    for p in rp.rglob('*.py'):
        if rep.files>=max_files or any(x in p.parts for x in _SKIP): continue
        rep.files+=1; rel=_rel(p,rp)
        try: src=p.read_text(encoding='utf-8',errors='replace'); tree=ast.parse(src)
        except Exception: continue
        lines=src.splitlines()
        if len(lines)>900: rep.items.append(RiskItem(rel,1,'large-file',min(99,len(lines)//20),f'{len(lines)} lines'))
        for n in ast.walk(tree):
            if isinstance(n,(ast.Try,)) and not n.handlers: rep.items.append(RiskItem(rel,n.lineno,'empty-try',20,'try without handlers'))
            if isinstance(n,ast.ExceptHandler) and n.type is None: rep.items.append(RiskItem(rel,n.lineno,'broad-except',18,'bare except'))
            if isinstance(n,ast.Raise) and n.exc is None: rep.items.append(RiskItem(rel,n.lineno,'reraising',8,'bare raise'))
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
                returns=sum(isinstance(c,ast.Return) for c in ast.walk(n)); branches=sum(isinstance(c,(ast.If,ast.For,ast.While,ast.Try,ast.Match)) for c in ast.walk(n))
                if returns>6: rep.items.append(RiskItem(rel,n.lineno,'many-returns',returns*3,f'{n.name} has {returns} returns'))
                if branches>12: rep.items.append(RiskItem(rel,n.lineno,'branchy-function',branches*2,f'{n.name} has {branches} branches'))
    return rep
def risk_context(rep:RiskReport)->str: return 'risk items='+str(len(rep.items))+'; '+ '; '.join(f'{i.kind}@{i.path}:{i.line}' for i in sorted(rep.items,key=lambda x:-x.score)[:12])
def risk_backlog(rep:RiskReport,limit:int=10)->list[str]: return [f'{i.kind}: {i.path}:{i.line} — {i.detail}' for i in sorted(rep.items,key=lambda x:-x.score)[:limit]]
