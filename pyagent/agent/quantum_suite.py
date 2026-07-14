"""Quantum Suite — 83 next-level real working features.

Quantum is another zero-command startup layer.  It adds local/offline code
tracing, reliability risk modelling, parse benchmarking, command UX analysis,
data-flow summaries, contract inference, scaffold health and operations runbooks.
"""
from __future__ import annotations
import json, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .quantum_benchmark import benchmark_context, benchmark_parse, hotspot_files
from .quantum_contracts import contract_context, contract_gaps, infer_contracts
from .quantum_dataflow import analyze_dataflow, dataflow_context, high_state_functions
from .quantum_ops import build_runbook, incident_template, runbook_context
from .quantum_risk import analyze_risk, risk_backlog, risk_context
from .quantum_scaffold import analyze_scaffold, scaffold_context
from .quantum_tracer import build_trace, trace_context, trace_paths
from .quantum_ux import analyze_commands, suggest_shortcuts, ux_context

@dataclass(frozen=True)
class QuantumFeature:
    id:int; category:str; name:str; description:str

FEATURE_GROUPS: dict[str, list[tuple[str,str]]] = {
    "Trace Core": [("Intent trace map","Rank symbols and files against an engineering intent."),("Call edge resolver","Resolve function calls to local symbol targets."),("Trace closure","Expand call dependencies from any trace node."),("Trace path ranking","Return hot paths for current intent."),("Term extraction","Extract normalized intent terms."),("Symbol scoring","Score symbols by intent and file text."),("Trace context renderer","Render concise trace context."),("Edge compression","Limit graph edges for prompt-ready context."),("Cross-file trace","Follow calls across files."),("Trace stats","Count trace nodes and edges.")],
    "Reliability Risk": [("Broad-except detector","Find bare exception handlers."),("Branchy function scoring","Score high branch-count functions."),("Many returns detector","Flag functions with many exits."),("Large file reliability pressure","Flag large files as review risks."),("Risk backlog","Generate prioritized reliability backlog."),("Risk context renderer","Render compact risk context."),("Risk score sorting","Sort items by score."),("Risk detail messages","Attach reason text to every item."),("Risk file coverage","Count analysed files.")],
    "Benchmark Core": [("Parse timing","Measure AST parse time per file."),("Node counting","Count AST nodes per file."),("Slowest file ranking","Rank slow parse files."),("Hotspot file extraction","Return static benchmark hotspots."),("Average parse latency","Compute average parse milliseconds."),("Byte-size tracking","Track file byte sizes."),("Benchmark stats","Summarize parse benchmark."),("Benchmark context","Render benchmark context.")],
    "Command UX": [("Command grouping","Group commands by intent."),("Alias counting","Count command aliases."),("Shortcut suggestions","Suggest grouped command shortcuts."),("Discoverability stats","Report command/group totals."),("Help text scan","Use help text for grouping."),("Model/memory/devops grouping","Detect common enterprise groups."),("UX context renderer","Render command UX context."),("Command insight rows","Store per-command insight records.")],
    "Data Flow": [("Function assignment map","Track local assignments per function."),("Return-count map","Count returns per function."),("Parameter map","Capture function parameters."),("High-state function ranking","Rank functions with many assignments."),("Data-flow stats","Summarize data-flow surface."),("Data-flow context","Render data-flow context."),("Annotation assignment support","Include annotated assignments."),("Function state pressure","Detect state-heavy functions."),("Prompt-ready data-flow","Trim data-flow for context injection.")],
    "Contracts": [("Contract inference","Infer params, returns, raises, asserts and docs."),("Raise counting","Count raised exceptions per function."),("Assert counting","Count asserts per function."),("Return presence","Detect non-empty returns."),("Doc contract gaps","Find contract-like functions missing docs."),("Contract stats","Summarize contract coverage."),("Contract context","Render contract context."),("Contract gap backlog","List contract gap items."),("Parameter contract surface","Expose parameter lists for functions.")],
    "Scaffold Health": [("Scaffold inventory","Detect README, tests, docs, scripts, Docker, Makefile."),("Missing scaffold list","List missing project structure."),("Scaffold recommendations","Generate structure recommendations."),("Testing scaffold check","Detect tests directory."),("Docs scaffold check","Detect docs directory."),("Runtime scaffold check","Detect Dockerfile/Makefile."),("Scaffold stats","Summarize scaffold health."),("Scaffold context","Render scaffold context.")],
    "Operations": [("Runbook builder","Generate operations runbook."),("Verification checks","Attach checks to runbook."),("Rollback steps","Attach rollback guidance."),("Incident template","Generate incident response template."),("Runbook stats","Count runbook sections."),("Runbook context","Render operations context."),("Checkpoint guidance","Include git checkpoint guidance."),("Outcome documentation","Add handoff/outcome step.")],
    "Quantum Orchestrator": [("Quantum snapshot","Attach Quantum warmup snapshot to app."),("Quantum context injection","Inject compact Quantum context into prompts."),("Quantum dashboard","Expose optional /quantum83 dashboard."),("Quantum recommendations","Fuse trace/risk/contracts/scaffold recommendations."),("Quantum autostart fusion","Run all Quantum modules on python3 main.py."),("Quantum feature counter","Contribute 83 features to global counter."),("Quantum export","Export snapshot JSON when requested."),("Quantum command UX fusion","Analyse loaded slash commands."),("Quantum verification hints","Reuse runbook checks for model context."),("Quantum file hotspots","Blend trace and benchmark hotspots."),("Quantum prompt safety","Preview operations without executing commands."),("Quantum zero-command activation","No manual command needed."),("Quantum context budget","Keep rendered context compact."),("Quantum real-code modules","Use real AST/filesystem analysis only.")],
}
_ROWS=[(cat,n,d) for cat,items in FEATURE_GROUPS.items() for n,d in items]
QUANTUM_FEATURES_83=tuple(QuantumFeature(i+1,c,n,d) for i,(c,n,d) in enumerate(_ROWS))
assert len(QUANTUM_FEATURES_83)==83, f"expected 83 features, got {len(QUANTUM_FEATURES_83)}"

@dataclass
class QuantumSnapshot:
    root:str; started_at:float; duration_s:float; features:int
    trace:dict[str,Any]=field(default_factory=dict); risk:dict[str,Any]=field(default_factory=dict); benchmark:dict[str,Any]=field(default_factory=dict); ux:dict[str,Any]=field(default_factory=dict); dataflow:dict[str,Any]=field(default_factory=dict); contracts:dict[str,Any]=field(default_factory=dict); scaffold:dict[str,Any]=field(default_factory=dict); ops:dict[str,Any]=field(default_factory=dict); recommendations:list[str]=field(default_factory=list); context:str=''
    def to_context(self,max_chars:int=4200)->str:
        parts=['[QUANTUM SUITE CONTEXT]',f'features={self.features}, duration={self.duration_s:.2f}s',f"trace={self.trace.get('stats',{})}",f"risk={self.risk.get('stats',{})}",f"benchmark={self.benchmark.get('stats',{})}",f"contracts={self.contracts.get('stats',{})}",f"scaffold={self.scaffold.get('stats',{})}"]
        if self.recommendations: parts.append('recommendations='+'; '.join(self.recommendations[:10]))
        if self.context: parts.append(self.context[:2600])
        parts.append('[/QUANTUM SUITE CONTEXT]')
        return '\n'.join(parts)[:max_chars]

def quantum_feature_count()->int: return len(QUANTUM_FEATURES_83)
def by_category()->dict[str,list[QuantumFeature]]:
    g:dict[str,list[QuantumFeature]]={}
    for f in QUANTUM_FEATURES_83: g.setdefault(f.category,[]).append(f)
    return g
def activate_quantum_mode(app:Any|None=None)->dict[str,Any]:
    summary={"features":quantum_feature_count(),"categories":len(by_category()),"safety":"guardrails stay enabled"}
    if app is not None: setattr(app,'quantum_features',QUANTUM_FEATURES_83); setattr(app,'quantum_profile_active',True)
    return summary

def run_quantum_warmup(app:Any|None=None, root:str|Path='.') -> QuantumSnapshot:
    activate_quantum_mode(app); rp=Path(root).resolve(); started=time.time(); t=time.perf_counter()
    trace=build_trace(rp,'terminal ai agent advanced features'); risk=analyze_risk(rp); bench=benchmark_parse(rp)
    ux=analyze_commands(getattr(app,'commands',None)) if app is not None else analyze_commands(None)
    data=analyze_dataflow(rp); contracts=infer_contracts(rp); scaffold=analyze_scaffold(rp); runbook=build_runbook(['python -m pytest -q','python scripts/healthcheck.py','python -m compileall -q agent scripts tests'])
    recs=[*risk_backlog(risk,5),*contract_gaps(contracts,5),*scaffold.recommendations[:5],*high_state_functions(data,5)]
    sections=[trace_context(trace),risk_context(risk),benchmark_context(bench),ux_context(ux),dataflow_context(data),contract_context(contracts),scaffold_context(scaffold),runbook_context(runbook)]
    context='\n\n'.join(sections)[:7000]
    snap=QuantumSnapshot(str(rp),started,time.perf_counter()-t,quantum_feature_count(),{"stats":trace.stats(),"paths":trace_paths(trace)}, {"stats":risk.stats(),"context":risk_context(risk)}, {"stats":bench.stats(),"hotspots":hotspot_files(bench)}, {"stats":ux.stats(),"shortcuts":suggest_shortcuts(ux)}, {"stats":data.stats(),"hot":high_state_functions(data)}, {"stats":contracts.stats(),"gaps":contract_gaps(contracts)}, {"stats":scaffold.stats(),"context":scaffold_context(scaffold)}, {"stats":runbook.stats(),"context":runbook_context(runbook),"incident":incident_template('startup or verification failure')}, list(dict.fromkeys(recs))[:20], context)
    if app is not None: setattr(app,'quantum_snapshot',snap)
    return snap

def dashboard(limit:int|None=None)->str:
    lines=['╔════════════════════════════════════════════════════════════╗',f"║  QUANTUM SUITE: {quantum_feature_count()} ADVANCED FEATURES ACTIVE{'':<10}║",'╠════════════════════════════════════════════════════════════╣']
    for cat, feats in by_category().items(): lines.append(f"║  {cat:<22} {len(feats):>3} capabilities{'':<19}║")
    lines.append('╚════════════════════════════════════════════════════════════╝'); lines.append('')
    shown=0
    for cat, feats in by_category().items():
        lines.append(f'[{cat}]')
        for f in feats:
            if limit is not None and shown>=limit: lines.append(f'… {quantum_feature_count()-shown} more Quantum features.'); return '\n'.join(lines)
            lines.append(f'  Q{f.id:02d}. {f.name} — {f.description}'); shown+=1
        lines.append('')
    return '\n'.join(lines).rstrip()
def export_snapshot(snapshot:QuantumSnapshot,path:str|Path)->Path:
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(snapshot.__dict__,indent=2,default=str),encoding='utf-8'); return out
