"""Zenith config surface analyzer."""
from __future__ import annotations
import ast, json, os, tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class ConfigSurface:
    files:list[str]=field(default_factory=list); env_vars:list[str]=field(default_factory=list); dataclasses:list[str]=field(default_factory=list); json_keys:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"files":self.files,"env_vars":sorted(set(self.env_vars))[:50],"dataclasses":self.dataclasses[:30],"json_keys":self.json_keys[:50]}
def analyze_config(root:str|Path='.') -> ConfigSurface:
    rp=Path(root); cs=ConfigSurface()
    for name in ['pyproject.toml','config.example.yaml','.env.example','pytest.ini','Makefile','requirements.txt']:
        if (rp/name).exists(): cs.files.append(name)
    for p in rp.rglob('*.py'):
        if any(x in p.parts for x in ['.git','__pycache__','venv','.venv']): continue
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        rel=p.relative_to(rp).as_posix() if p.is_relative_to(rp) else str(p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn=getattr(n.func,'attr',getattr(n.func,'id',''))
                if fn in {'getenv','get'} and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value,str): cs.env_vars.append(n.args[0].value)
            if isinstance(n, ast.ClassDef):
                if any(getattr(d,'id',getattr(d,'attr',''))=='dataclass' for d in n.decorator_list): cs.dataclasses.append(f'{rel}:{n.name}')
    pp=rp/'pyproject.toml'
    if pp.exists():
        try: cs.json_keys += list(tomllib.loads(pp.read_text()).keys())
        except Exception: pass
    return cs
def config_context(cs:ConfigSurface)->str: return f"config files={cs.files} env={sorted(set(cs.env_vars))[:15]} dataclasses={cs.dataclasses[:8]}"
