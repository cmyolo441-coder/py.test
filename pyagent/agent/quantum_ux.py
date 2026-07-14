"""Quantum UI/command discoverability intelligence."""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
@dataclass
class CommandInsight:
    name:str; aliases:list[str]; help:str; group:str
@dataclass
class UxReport:
    commands:list[CommandInsight]=field(default_factory=list); groups:dict[str,list[str]]=field(default_factory=dict)
    def stats(self)->dict[str,Any]: return {"commands":len(self.commands),"groups":{k:len(v) for k,v in self.groups.items()},"aliases":sum(len(c.aliases) for c in self.commands)}
def _group(name:str, help_text:str)->str:
    text=(name+' '+help_text).lower()
    for g,keys in {'model':['model','provider','route'],'memory':['memory','rag','kg','index'],'security':['sast','audit','scan','sbom'],'devops':['docker','ci','cloud','prometheus'],'ui':['theme','spinner','matrix','keys'],'goal':['goal','schedule'],'tools':['tool','run','grep','tree','cat']}.items():
        if any(k in text for k in keys): return g
    return 'general'
def analyze_commands(commands:Any)->UxReport:
    all_cmds=commands.all() if hasattr(commands,'all') else []
    rows=[]; groups=defaultdict(list)
    for c in all_cmds:
        name=getattr(c,'name',''); aliases=list(getattr(c,'aliases',()) or ()); help_text=getattr(c,'help','') or ''; g=_group(name,help_text)
        rows.append(CommandInsight(name,aliases,help_text,g)); groups[g].append(name)
    return UxReport(rows,dict(groups))
def suggest_shortcuts(rep:UxReport,limit:int=12)->list[str]:
    return [f'{g}: {", ".join(names[:5])}' for g,names in sorted(rep.groups.items(),key=lambda x:-len(x[1]))[:limit]]
def ux_context(rep:UxReport)->str: return 'commands='+str(rep.stats())+'\n'+'\n'.join(suggest_shortcuts(rep))
