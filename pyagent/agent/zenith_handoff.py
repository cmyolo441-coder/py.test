"""Zenith handoff report generation."""
from __future__ import annotations
import json, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class HandoffReport:
    title:str; sections:dict[str,str]=field(default_factory=dict); created_at:float=field(default_factory=time.time)
    def markdown(self)->str:
        out=[f'# {self.title}', '', f'Created: {self.created_at}', '']
        for k,v in self.sections.items(): out += [f'## {k}', str(v), '']
        return '\n'.join(out)
def build_handoff(title:str, snapshot:dict[str,Any])->HandoffReport:
    sections={k: json.dumps(v,indent=2,default=str)[:3000] for k,v in snapshot.items()}
    return HandoffReport(title, sections)
def save_handoff(report:HandoffReport, path:str|Path)->Path:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(report.markdown(),encoding='utf-8'); return p
def handoff_context(report:HandoffReport)->str: return report.markdown()[:2500]
