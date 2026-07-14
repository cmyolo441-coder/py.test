"""Zenith local multi-specialist routing (no model calls)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class Specialist:
    name:str; focus:str; triggers:list[str]; tools:list[str]=field(default_factory=list)
SPECIALISTS=[Specialist('architect','architecture and boundaries',['architecture','design','refactor'],['knowledge graph','metrics']),Specialist('tester','tests and verification',['test','pytest','fail','bug'],['pytest','coverage']),Specialist('documenter','docs and onboarding',['doc','readme','explain'],['markdown','api']),Specialist('runtime','release and runtime',['docker','release','deploy','ci'],['runtime','release']),Specialist('performance','static performance review',['slow','performance','optimize'],['ast','loops']),Specialist('maintainer','backlog and quality',['quality','cleanup','maintain'],['quality','refactor'])]
def route_specialists(task:str, limit:int=4)->list[Specialist]:
    t=task.lower(); scored=[]
    for s in SPECIALISTS:
        score=sum(1 for trig in s.triggers if trig in t)
        if score: scored.append((score,s))
    if not scored: scored=[(1,SPECIALISTS[0]),(1,SPECIALISTS[1])]
    return [s for _score,s in sorted(scored,key=lambda x:-x[0])[:limit]]
def specialist_plan(task:str)->dict[str,Any]:
    specs=route_specialists(task); return {"task":task,"specialists":[s.__dict__ for s in specs],"sequence":[s.name for s in specs]}
def agents_context(plan:dict[str,Any])->str: return 'specialists: '+', '.join(plan.get('sequence',[]))
