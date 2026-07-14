"""Zenith code-map and boundary intelligence."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class CodeMap:
    layers:dict[str,list[str]]=field(default_factory=dict); owners:dict[str,str]=field(default_factory=dict); boundaries:list[str]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"layers":{k:len(v) for k,v in self.layers.items()},"owners":len(self.owners),"boundaries":self.boundaries[:20]}
def infer_layer(path:str)->str:
    p=path.lower()
    for layer,keys in {'ui':['ui','theme','prompt','widget'],'commands':['commands'],'tools':['tools'],'providers':['providers'],'memory':['memory','rag','knowledge'],'runtime':['docker','cicd','telemetry','prometheus'],'core':['core','app','config','guard']}.items():
        if any(k in p for k in keys): return layer
    return 'other'
def build_code_map(root:str|Path='.') -> CodeMap:
    rp=Path(root); cm=CodeMap(defaultdict(list),{},[])
    for p in rp.rglob('*.py'):
        if any(x in p.parts for x in ['.git','__pycache__','venv','.venv']): continue
        rel=p.relative_to(rp).as_posix() if p.is_relative_to(rp) else str(p); layer=infer_layer(rel); cm.layers[layer].append(rel); cm.owners[rel]=layer
    for rel,layer in cm.owners.items():
        if layer=='other' and rel.startswith('agent/'): cm.boundaries.append(f'Classify boundary for {rel}')
    return cm
def code_map_context(cm:CodeMap)->str: return 'layers: '+', '.join(f'{k}={len(v)}' for k,v in cm.layers.items())
