"""Quantum lightweight data-flow analyzer."""
from __future__ import annotations
import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
_SKIP={'.git','__pycache__','.venv','venv','node_modules','.pytest_cache','dist','build'}
@dataclass
class DataFlowReport:
    assignments:dict[str,list[str]]=field(default_factory=dict); returns:dict[str,int]=field(default_factory=dict); params:dict[str,list[str]]=field(default_factory=dict)
    def stats(self)->dict[str,Any]: return {"functions":len(self.returns),"assignments":sum(len(v) for v in self.assignments.values()),"top_returns":sorted(self.returns.items(),key=lambda x:-x[1])[:15]}
def _rel(p:Path,r:Path)->str:
    try:return p.relative_to(r).as_posix()
    except ValueError:return p.as_posix()
def analyze_dataflow(root:str|Path='.',max_files:int=900)->DataFlowReport:
    rp=Path(root).resolve(); rep=DataFlowReport()
    for p in rp.rglob('*.py'):
        if len(rep.returns)>max_files*10 or any(x in p.parts for x in _SKIP): continue
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        rel=_rel(p,rp)
        for n in ast.walk(tree):
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
                q=f'{rel}:{n.name}'; rep.params[q]=[a.arg for a in n.args.args]
                assigns=[]; ret=0
                for c in ast.walk(n):
                    if isinstance(c,ast.Assign):
                        for t in c.targets:
                            if isinstance(t,ast.Name): assigns.append(t.id)
                    elif isinstance(c,ast.AnnAssign) and isinstance(c.target,ast.Name): assigns.append(c.target.id)
                    elif isinstance(c,ast.Return): ret+=1
                rep.assignments[q]=assigns[:80]; rep.returns[q]=ret
    return rep
def dataflow_context(rep:DataFlowReport)->str: return 'dataflow '+str(rep.stats())[:1000]
def high_state_functions(rep:DataFlowReport,limit:int=12)->list[str]: return [f'{k} assigns={len(v)} returns={rep.returns.get(k,0)}' for k,v in sorted(rep.assignments.items(),key=lambda x:-len(x[1]))[:limit]]
