"""Zenith adaptive context packer."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class ZenithSection:
    name:str; content:str; priority:int=50
@dataclass
class ZenithContext:
    goal:str; budget:int=7000; sections:list[ZenithSection]=field(default_factory=list)
    def render(self)->str:
        rem=self.budget; out=['[ZENITH CONTEXT]', 'goal: '+self.goal]
        for s in sorted(self.sections,key=lambda x:-x.priority):
            chunk=f"\n## {s.name}\n{dedupe(s.content)}"
            if len(chunk)>rem: chunk=chunk[:max(0,rem-20)]+'\n…[truncated]'
            out.append(chunk); rem-=len(chunk)
            if rem<120: break
        out.append('[/ZENITH CONTEXT]'); return '\n'.join(out)
def dedupe(text:str)->str:
    seen=set(); rows=[]
    for line in text.splitlines():
        k=' '.join(line.lower().split())
        if k and k not in seen: seen.add(k); rows.append(line)
    return '\n'.join(rows)
def compose_zenith_context(goal:str, sources:dict[str,Any], budget:int=7000)->ZenithContext:
    pri={'plan':100,'impact':95,'symbols':90,'tests':85,'quality':80,'runtime':75,'docs':65,'release':60,'cache':40}
    ctx=ZenithContext(goal,budget)
    for k,v in sources.items(): ctx.sections.append(ZenithSection(k,str(v),pri.get(k,50)))
    return ctx
def context_stats(ctx:ZenithContext)->dict[str,Any]: return {"sections":len(ctx.sections),"budget":ctx.budget,"rendered":len(ctx.render())}
