"""Zenith persistent JSON cache."""
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class CacheEntry:
    value:Any; created:float; ttl:float|None=None
@dataclass
class ZenithCache:
    path:Path; data:dict[str,CacheEntry]=field(default_factory=dict)
    def load(self)->None:
        if not self.path.exists(): return
        try: self.data={k:CacheEntry(**v) for k,v in json.loads(self.path.read_text()).items()}
        except Exception: self.data={}
    def save(self)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True); self.path.write_text(json.dumps({k:v.__dict__ for k,v in self.data.items()},indent=2,default=str))
    def set(self,key:str,value:Any,ttl:float|None=None)->None: self.data[key]=CacheEntry(value,time.time(),ttl); self.save()
    def get(self,key:str,default:Any=None)->Any:
        e=self.data.get(key)
        if not e: return default
        if e.ttl and time.time()-e.created>e.ttl: self.data.pop(key,None); self.save(); return default
        return e.value
    def stats(self)->dict[str,Any]: return {"entries":len(self.data),"path":str(self.path)}
def fingerprint(obj:Any)->str: return hashlib.sha256(json.dumps(obj,sort_keys=True,default=str).encode()).hexdigest()[:20]
def get_cache(path:str|Path|None=None)->ZenithCache:
    c=ZenithCache(Path(path) if path else Path.home()/'.terminal_agent'/'zenith_cache.json'); c.load(); return c
def memoize_snapshot(kind:str,payload:dict[str,Any])->dict[str,Any]:
    c=get_cache(); key=f'{kind}:{fingerprint(payload)}'; previous=c.get(f'{kind}:latest'); c.set(key,payload); c.set(f'{kind}:latest',payload); return {"key":key,"changed":previous!=payload,"cache":c.stats()}
