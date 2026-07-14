"""Quantum static benchmark estimator."""
from __future__ import annotations
import ast, time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any
_SKIP={'.git','__pycache__','.venv','venv','node_modules','.pytest_cache','dist','build'}
@dataclass
class ParseMetric:
    path:str; bytes:int; parse_ms:float; nodes:int
@dataclass
class BenchmarkReport:
    metrics:list[ParseMetric]=field(default_factory=list)
    def stats(self)->dict[str,Any]:
        return {"files":len(self.metrics),"avg_parse_ms":round(mean([m.parse_ms for m in self.metrics]) if self.metrics else 0,3),"total_nodes":sum(m.nodes for m in self.metrics),"slowest":[m.__dict__ for m in sorted(self.metrics,key=lambda x:-x.parse_ms)[:15]]}
def _rel(p:Path,r:Path)->str:
    try:return p.relative_to(r).as_posix()
    except ValueError:return p.as_posix()
def benchmark_parse(root:str|Path='.',max_files:int=900)->BenchmarkReport:
    rp=Path(root).resolve(); out=[]
    for p in rp.rglob('*.py'):
        if len(out)>=max_files or any(x in p.parts for x in _SKIP): continue
        try: raw=p.read_bytes(); t=time.perf_counter(); tree=ast.parse(raw.decode('utf-8',errors='replace')); ms=(time.perf_counter()-t)*1000
        except Exception: continue
        out.append(ParseMetric(_rel(p,rp),len(raw),round(ms,3),sum(1 for _ in ast.walk(tree))))
    return BenchmarkReport(out)
def benchmark_context(rep:BenchmarkReport)->str: return 'benchmark '+str(rep.stats())[:1200]
def hotspot_files(rep:BenchmarkReport,limit:int=12)->list[str]: return [m.path for m in sorted(rep.metrics,key=lambda x:(-x.parse_ms,-x.nodes))[:limit]]
