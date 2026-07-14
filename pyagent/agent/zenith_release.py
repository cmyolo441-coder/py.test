"""Zenith release/readiness planning."""
from __future__ import annotations
import re, tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class ReleasePlan:
    version:str='0.0.0'; checklist:list[str]=field(default_factory=list); artifacts:list[str]=field(default_factory=list); blockers:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"version":self.version,"checklist":len(self.checklist),"artifacts":self.artifacts,"blockers":self.blockers}
def detect_version(root:str|Path='.') -> str:
    rp=Path(root); pp=rp/'pyproject.toml'
    if pp.exists():
        try: return str(tomllib.loads(pp.read_text()).get('project',{}).get('version','0.0.0'))
        except Exception: pass
    init=rp/'agent'/'__init__.py'
    if init.exists():
        m=re.search(r"__version__\s*=\s*['\"]([^'\"]+)", init.read_text(errors='replace'))
        if m: return m.group(1)
    return '0.0.0'
def build_release_plan(root:str|Path='.', commands:list[str]|None=None)->ReleasePlan:
    rp=Path(root); commands=commands or ['python -m pytest -q','python scripts/healthcheck.py']
    artifacts=[]; blockers=[]
    if (rp/'Dockerfile').exists(): artifacts.append('docker image')
    if (rp/'pyproject.toml').exists(): artifacts.append('python package')
    if not (rp/'README.md').exists(): blockers.append('README missing')
    checklist=['Review changelog','Run verification: '+', '.join(commands[:3]),'Check docs quickstart','Confirm package metadata','Create tag after tests pass']
    return ReleasePlan(detect_version(rp), checklist, artifacts, blockers)
def release_context(plan:ReleasePlan)->str: return f"release v{plan.version} artifacts={plan.artifacts} blockers={plan.blockers} checklist={plan.checklist[:3]}"
