"""Quantum tracer: intent-to-code trace maps."""
from __future__ import annotations
import ast, re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
_SKIP={'.git','__pycache__','.venv','venv','node_modules','.pytest_cache','dist','build'}
@dataclass
class TraceNode:
    path:str; symbol:str; line:int; kind:str; score:int=0
@dataclass
class TraceReport:
    nodes:list[TraceNode]=field(default_factory=list); edges:dict[str,list[str]]=field(default_factory=dict); intent_terms:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"nodes":len(self.nodes),"edges":sum(len(v) for v in self.edges.values()),"top":[n.__dict__ for n in sorted(self.nodes,key=lambda x:-x.score)[:15]],"terms":self.intent_terms}
def _rel(p:Path,r:Path)->str:
    try:return p.relative_to(r).as_posix()
    except ValueError:return p.as_posix()
def _terms(text:str)->list[str]: return [t.lower() for t in re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', text)]
def _call_name(n:ast.AST)->str:
    if isinstance(n,ast.Name): return n.id
    if isinstance(n,ast.Attribute):
        b=_call_name(n.value); return f'{b}.{n.attr}' if b else n.attr
    return ''
def build_trace(root:str|Path='.', intent:str='terminal agent', max_files:int=900)->TraceReport:
    rp=Path(root).resolve(); terms=_terms(intent); nodes=[]; edges=defaultdict(list); by_name=defaultdict(list)
    for p in rp.rglob('*.py'):
        if len(nodes)>max_files*8 or any(x in p.parts for x in _SKIP): continue
        rel=_rel(p,rp)
        try: src=p.read_text(encoding='utf-8',errors='replace'); tree=ast.parse(src)
        except Exception: continue
        file_score=sum(t in rel.lower() or t in src[:3000].lower() for t in terms)
        for n in ast.walk(tree):
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                kind='class' if isinstance(n,ast.ClassDef) else 'function'; name=n.name; score=file_score*3+sum(t in name.lower() for t in terms)*10
                node=TraceNode(rel,name,n.lineno,kind,score); nodes.append(node); by_name[name].append(f'{rel}:{name}')
                if not isinstance(n,ast.ClassDef):
                    for c in ast.walk(n):
                        if isinstance(c,ast.Call):
                            call=_call_name(c.func).split('.')[-1]
                            if call: edges[f'{rel}:{name}'].append(call)
    resolved={}
    for src,calls in edges.items():
        resolved[src]=sorted({target for c in calls for target in by_name.get(c,[])})[:50]
    return TraceReport(nodes, resolved, terms)
def trace_context(report:TraceReport,limit:int=12)->str:
    rows=['quantum trace: '+str(report.stats())]
    for n in sorted(report.nodes,key=lambda x:-x.score)[:limit]: rows.append(f'- {n.score} {n.kind} {n.symbol} @ {n.path}:{n.line}')
    return '\n'.join(rows)
def trace_paths(report:TraceReport)->list[str]: return list(dict.fromkeys(n.path for n in sorted(report.nodes,key=lambda x:-x.score)))[:30]
def trace_closure(report:TraceReport,start:str,depth:int=2)->list[str]:
    q=deque([(start,0)]); seen={start}; out=[]
    while q:
        cur,d=q.popleft()
        if d>=depth: continue
        for nxt in report.edges.get(cur,[]):
            if nxt not in seen: seen.add(nxt); out.append(nxt); q.append((nxt,d+1))
    return out
