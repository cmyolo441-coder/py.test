"""Zenith composite metrics."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any
@dataclass
class MetricScore:
    name:str; value:float; weight:float=1.0; detail:str=''
@dataclass
class ZenithMetrics:
    scores:list[MetricScore]=field(default_factory=list)
    def total(self)->float:
        w=sum(s.weight for s in self.scores) or 1; return round(sum(s.value*s.weight for s in self.scores)/w,2)
    def stats(self)->dict[str,Any]: return {"total":self.total(),"scores":[s.__dict__ for s in self.scores]}
def compute_project_metrics(root:str|Path='.', counts:dict[str,Any]|None=None)->ZenithMetrics:
    rp=Path(root); counts=counts or {}; py=list(rp.rglob('*.py')); md=list(rp.rglob('*.md')); tests=[p for p in py if p.name.startswith('test_') or 'tests' in p.parts]
    total_lines=0
    for p in py[:800]:
        try: total_lines += len(p.read_text(encoding='utf-8',errors='replace').splitlines())
        except OSError: pass
    test_ratio=(len(tests)/max(1,len(py)))*100; docs_ratio=(len(md)/max(1,len(py)))*100
    size_score=max(20,100-min(80,total_lines/400)); test_score=min(100,test_ratio*250); docs_score=min(100,docs_ratio*400)
    return ZenithMetrics([MetricScore('size-manageability',size_score,1.2,f'{total_lines} py lines'), MetricScore('test-presence',test_score,1.5,f'{len(tests)}/{len(py)} py test files'), MetricScore('docs-presence',docs_score,1.0,f'{len(md)} markdown files'), MetricScore('feature-readiness',min(100,counts.get('features',0)/5),1.0,str(counts.get('features',0)))])
def metrics_context(m:ZenithMetrics)->str: return 'metrics total='+str(m.total())+'; '+ '; '.join(f'{s.name}={s.value:.1f}' for s in m.scores)
