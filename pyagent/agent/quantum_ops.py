"""Quantum operations runbook generator."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class Runbook:
    title:str; steps:list[str]=field(default_factory=list); checks:list[str]=field(default_factory=list); rollback:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"steps":len(self.steps),"checks":len(self.checks),"rollback":len(self.rollback)}
def build_runbook(commands:list[str]|None=None, title:str='Terminal Agent Operations')->Runbook:
    commands=commands or ['python -m pytest -q','python scripts/healthcheck.py']
    return Runbook(title,['Review startup autostart summary','Inspect changed files','Apply minimal scoped patch','Run verification gates','Record outcome in handoff notes'],commands[:8],['Keep git checkpoint before edits','Revert changed files if verification fails','Re-run healthcheck after rollback'])
def runbook_context(r:Runbook)->str: return f"runbook {r.title}: steps={r.steps}; checks={r.checks}; rollback={r.rollback}"
def incident_template(issue:str)->str: return f"Incident: {issue}\n1. Capture error\n2. Identify impacted files\n3. Apply minimal fix\n4. Run tests\n5. Document root cause"
