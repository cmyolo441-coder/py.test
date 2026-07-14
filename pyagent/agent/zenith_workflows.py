"""Zenith workflow simulation."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class Step:
    id:str; title:str; duration:int=1; deps:list[str]=field(default_factory=list)
@dataclass
class Workflow:
    name:str; steps:list[Step]
def simulate_workflow(w:Workflow)->dict[str,Any]:
    done={}; order=[]
    while len(done)<len(w.steps):
        progressed=False
        for s in w.steps:
            if s.id in done: continue
            if all(d in done for d in s.deps): done[s.id]=max([done.get(d,0) for d in s.deps] or [0])+s.duration; order.append(s.id); progressed=True
        if not progressed: return {"valid":False,"error":"cycle","order":order}
    return {"valid":True,"order":order,"critical_duration":max(done.values() or [0]),"finish_times":done}
def build_standard_workflow(commands:list[str])->Workflow:
    steps=[Step('inspect','Inspect context',1),Step('plan','Plan patch',1,['inspect']),Step('edit','Apply edits',2,['plan']),Step('review','Review changes',1,['edit'])]
    prev='review'
    for i,c in enumerate(commands[:6],1): steps.append(Step(f'v{i}',f'Verify {c}',1,[prev])); prev=f'v{i}'
    return Workflow('standard',steps)
def workflow_text(w:Workflow)->str: return '\n'.join(f'- {s.id}: {s.title} deps={s.deps}' for s in w.steps)
