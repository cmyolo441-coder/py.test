"""Zenith documentation generation helpers."""
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class ApiItem:
    kind:str; name:str; path:str; line:int; doc:str=''
@dataclass
class DocsPlan:
    api:list[ApiItem]=field(default_factory=list); missing:list[str]=field(default_factory=list); skeleton:str=''
    def stats(self)->dict[str,Any]: return {"api_items":len(self.api),"missing":len(self.missing)}
def extract_api(root:str|Path='.', limit:int=300)->list[ApiItem]:
    rp=Path(root); out=[]
    for p in rp.rglob('*.py'):
        if len(out)>=limit or any(x in p.parts for x in ['.git','__pycache__','venv','.venv']): continue
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        rel=p.relative_to(rp).as_posix() if p.is_relative_to(rp) else str(p)
        for n in ast.walk(tree):
            if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and not n.name.startswith('_'):
                out.append(ApiItem(type(n).__name__.replace('Def','').lower(), n.name, rel, n.lineno, ast.get_docstring(n) or ''))
    return out[:limit]
def build_docs_plan(root:str|Path='.') -> DocsPlan:
    api=extract_api(root); missing=[f'{i.path}:{i.line}:{i.name}' for i in api if not i.doc][:80]
    skeleton='\n'.join(['# Project Guide','','## Quickstart','- Install dependencies','- Run tests','', '## Architecture','Describe major packages and data flow.','', '## API Surface', *[f'- `{i.name}` ({i.kind}) — {i.path}:{i.line}' for i in api[:25]], '', '## Verification','Document test/health commands.'])
    return DocsPlan(api, missing, skeleton)
def docs_plan_context(plan:DocsPlan)->str: return f"docs api_items={len(plan.api)} missing_docstrings={len(plan.missing)}\n" + '\n'.join(plan.missing[:10])
