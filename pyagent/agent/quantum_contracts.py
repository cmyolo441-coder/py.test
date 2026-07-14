"""Quantum contract inference."""
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
@dataclass
class Contract:
    path:str; symbol:str; line:int; params:list[str]; returns:bool; raises:int; asserts:int; doc:bool
@dataclass
class ContractReport:
    contracts:list[Contract]=field(default_factory=list)
    def stats(self)->dict[str,Any]: return {"contracts":len(self.contracts),"with_docs":sum(c.doc for c in self.contracts),"with_asserts":sum(1 for c in self.contracts if c.asserts),"with_raises":sum(1 for c in self.contracts if c.raises)}
def infer_contracts(root:str|Path='.',max_files:int=900)->ContractReport:
    rp=Path(root); out=[]
    for p in rp.rglob('*.py'):
        if len(out)>=max_files*8 or any(x in p.parts for x in ['.git','__pycache__','venv','.venv']): continue
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        rel=p.relative_to(rp).as_posix() if p.is_relative_to(rp) else str(p)
        for n in ast.walk(tree):
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
                out.append(Contract(rel,n.name,n.lineno,[a.arg for a in n.args.args],any(isinstance(c,ast.Return) and c.value is not None for c in ast.walk(n)),sum(isinstance(c,ast.Raise) for c in ast.walk(n)),sum(isinstance(c,ast.Assert) for c in ast.walk(n)),bool(ast.get_docstring(n))))
    return ContractReport(out)
def contract_context(rep:ContractReport)->str: return 'contracts '+str(rep.stats())+' gaps='+', '.join(f'{c.path}:{c.symbol}' for c in rep.contracts if not c.doc and (c.raises or c.asserts or len(c.params)>3))[:800]
def contract_gaps(rep:ContractReport,limit:int=12)->list[str]: return [f'{c.path}:{c.line}:{c.symbol} params={c.params} raises={c.raises} asserts={c.asserts}' for c in rep.contracts if not c.doc][:limit]
