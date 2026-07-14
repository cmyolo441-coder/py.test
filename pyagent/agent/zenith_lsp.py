"""Zenith lightweight LSP-style index."""
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SKIP={'.git','__pycache__','.venv','venv','node_modules','.pytest_cache','dist','build'}

@dataclass
class Definition:
    name: str
    kind: str
    path: str
    line: int
    col: int
    qualname: str

@dataclass
class LspIndex:
    root: str
    definitions: list[Definition]=field(default_factory=list)
    references: dict[str,list[tuple[str,int]]]=field(default_factory=dict)
    def stats(self)->dict[str,Any]:
        return {"definitions":len(self.definitions),"reference_names":len(self.references),"files":len({d.path for d in self.definitions})}

def _rel(p:Path,r:Path)->str:
    try: return p.relative_to(r).as_posix()
    except ValueError: return p.as_posix()

def _mod(p:Path,r:Path)->str:
    return _rel(p,r).removesuffix('.py').replace('/','.')

def _files(root:Path,max_files:int):
    out=[]
    for p in root.rglob('*.py'):
        if len(out)>=max_files: break
        if not any(x in _SKIP for x in p.parts): out.append(p)
    return sorted(out)

def build_lsp_index(root:str|Path='.',max_files:int=900)->LspIndex:
    rp=Path(root).resolve(); idx=LspIndex(str(rp))
    for p in _files(rp,max_files):
        rel=_rel(p,rp); mod=_mod(p,rp)
        try:
            src=p.read_text(encoding='utf-8',errors='replace'); tree=ast.parse(src)
        except (OSError,SyntaxError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                idx.definitions.append(Definition(n.name,'class',rel,n.lineno,n.col_offset,f'{mod}.{n.name}'))
            elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
                idx.definitions.append(Definition(n.name,'function',rel,n.lineno,n.col_offset,f'{mod}.{n.name}'))
            elif isinstance(n, ast.Name):
                idx.references.setdefault(n.id,[]).append((rel,getattr(n,'lineno',0)))
    return idx

def goto_definition(index:LspIndex,name:str)->list[Definition]:
    q=name.lower(); return [d for d in index.definitions if q in d.name.lower() or q in d.qualname.lower()][:25]

def find_references(index:LspIndex,name:str)->list[tuple[str,int]]:
    return index.references.get(name,[])[:200]

def outline_file(index:LspIndex,path:str)->list[Definition]:
    return [d for d in index.definitions if d.path==path]

def workspace_symbols(index:LspIndex,query:str,limit:int=50)->list[str]:
    return [f'{d.kind} {d.qualname} @ {d.path}:{d.line}' for d in goto_definition(index,query)[:limit]]
