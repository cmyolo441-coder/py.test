"""Quantum scaffold health analyzer."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class ScaffoldReport:
    present:list[str]=field(default_factory=list); missing:list[str]=field(default_factory=list); recommendations:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"present":self.present,"missing":self.missing,"recommendations":self.recommendations}
def analyze_scaffold(root:str|Path='.') -> ScaffoldReport:
    rp=Path(root); expected=['README.md','pyproject.toml','requirements.txt','tests','docs','scripts','Makefile','Dockerfile']
    present=[]; missing=[]
    for e in expected: (present if (rp/e).exists() else missing).append(e)
    rec=[]
    if 'tests' in missing: rec.append('Create tests/ with pytest coverage for core flows.')
    if 'docs' in missing: rec.append('Create docs/ for architecture and operations.')
    if 'Dockerfile' in missing: rec.append('Add Dockerfile for reproducible runtime packaging.')
    if 'Makefile' in missing: rec.append('Add Makefile targets for test/health/run.')
    return ScaffoldReport(present,missing,rec)
def scaffold_context(rep:ScaffoldReport)->str: return f"scaffold present={rep.present} missing={rep.missing} recs={rep.recommendations}"
