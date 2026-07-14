"""Zenith patch planning and safe diff preview."""
from __future__ import annotations
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class PatchPlan:
    likely_files:list[str]=field(default_factory=list); risk:str='low'; rationale:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"files":len(self.likely_files),"risk":self.risk,"rationale":self.rationale}
def plan_patch(task:str, ranked_files:list[str]|None=None, hot_files:list[str]|None=None)->PatchPlan:
    files=list(dict.fromkeys((ranked_files or [])[:8] + (hot_files or [])[:8]))[:10]
    risk='high' if len(files)>6 else 'medium' if len(files)>3 else 'low'
    rationale=[f'task mentions {w}' for w in ['fix','test','refactor','docs'] if w in task.lower()]
    if files: rationale.append('ranked by local code intelligence')
    return PatchPlan(files,risk,rationale)
def diff_preview(path:str, old:str, new:str, context:int=3)->str:
    return ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=path, tofile=path, n=context))
def patch_context(plan:PatchPlan)->str: return f"patch risk={plan.risk} files={plan.likely_files[:8]} rationale={plan.rationale}"
