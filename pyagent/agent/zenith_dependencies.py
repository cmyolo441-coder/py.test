"""Zenith dependency and import intelligence."""
from __future__ import annotations
import ast, tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
_SKIP={'.git','__pycache__','.venv','venv','node_modules','.pytest_cache','dist','build'}
@dataclass
class DependencyReport:
    imports_by_file: dict[str,list[str]]=field(default_factory=dict); external: Counter[str]=field(default_factory=Counter); internal: Counter[str]=field(default_factory=Counter); declared: list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"files":len(self.imports_by_file),"external":self.external.most_common(20),"internal":self.internal.most_common(20),"declared":len(self.declared)}
def _rel(p:Path,r:Path)->str:
    try: return p.relative_to(r).as_posix()
    except ValueError: return p.as_posix()
def _imports(tree:ast.AST)->list[str]:
    out=[]
    for n in ast.walk(tree):
        if isinstance(n, ast.Import): out += [a.name.split('.')[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module: out.append(n.module.split('.')[0])
    return out
def _declared(root:Path)->list[str]:
    deps=[]; req=root/'requirements.txt'; pp=root/'pyproject.toml'
    if req.exists():
        deps += [l.strip().split('=')[0].split('>')[0].split('<')[0] for l in req.read_text(errors='replace').splitlines() if l.strip() and not l.strip().startswith('#')]
    if pp.exists():
        try: deps += [d.split('=')[0].split('>')[0].split('<')[0] for d in tomllib.loads(pp.read_text()).get('project',{}).get('dependencies',[])]
        except Exception: pass
    return sorted(set(d for d in deps if d))
def analyze_dependencies(root:str|Path='.', package_prefixes:tuple[str,...]=('agent','tests')) -> DependencyReport:
    rp=Path(root).resolve(); rep=DependencyReport(declared=_declared(rp))
    for p in rp.rglob('*.py'):
        if any(x in _SKIP for x in p.parts): continue
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except (OSError,SyntaxError): continue
        rel=_rel(p,rp); imps=sorted(set(_imports(tree))); rep.imports_by_file[rel]=imps
        for imp in imps:
            (rep.internal if imp in package_prefixes else rep.external)[imp]+=1
    return rep
def missing_declarations(rep:DependencyReport)->list[str]:
    std={'os','sys','json','time','pathlib','typing','dataclasses','collections','re','ast','math','hashlib','subprocess','sqlite3','threading','asyncio','tempfile'}
    declared={d.replace('-','_').lower() for d in rep.declared}
    return [m for m,_ in rep.external.most_common() if m not in std and m.lower() not in declared][:30]
def dependency_context(rep:DependencyReport)->str:
    return '\n'.join(['dependencies:', f"external={rep.external.most_common(10)}", f"internal={rep.internal.most_common(10)}", f"missing_declarations={missing_declarations(rep)[:10]}"])
