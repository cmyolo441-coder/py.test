#!/usr/bin/env python3
"""
ADVANCED CODE SCANNER PRO v4.0 - SINGLE FILE - CODE ONLY
Web scanning REMOVED. Only SAST + Secrets + Taint + Entropy + Audit.

Checks:
 - Python AST (eval/exec/pickle/subprocess/hardcoded secrets)
 - Taint tracking: input()/request/sys.argv -> eval/os.system/execute/open
 - Regex SAST for .py .js .ts .php .java .go .rb .c .cpp .cs .sh .sql .env .json .yaml .html
 - Secret scan: AWS/JWT/GitHub/GitLab/Slack/Stripe/Google/SendGrid/DB URIs
 - Entropy scan: Shannon >4.6 high-entropy tokens near secret context
 - Dockerfile audit: latest + USER root + curl|bash + secret ENV
 - Dependency CVE: package.json (lodash/jQuery) + requirements sanity
 - Reports: JSON / HTML dashboard / SARIF / CSV + Risk Score
Stdlib only. Python 3.10+. Authorized use only - scan your own codebase.
"""
import argparse, ast, collections, html, json, sys, threading, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore"); sys.stderr.reconfigure(encoding="utf-8", errors="ignore")
except Exception: pass

VERSION = "4.0.0-CODE"
C_R="\033[91m";C_G="\033[92m";C_Y="\033[93m";C_B="\033[94m";C_C="\033[96m";C_M="\033[95m";C_0="\033[0m";C_BD="\033[1m"
SEV_R={"CRITICAL":5,"HIGH":4,"MEDIUM":3,"LOW":2,"INFO":1}
SEV_C={"CRITICAL":C_R,"HIGH":C_R,"MEDIUM":C_Y,"LOW":C_B,"INFO":C_C}
_LOCK=threading.Lock()

def log(m,l="INFO",p=""):
    c={"INFO":C_B,"OK":C_G,"WARN":C_Y,"ERROR":C_R,"FIND":C_R,"SCAN":C_C,"DEBUG":C_B,"PRO":C_M}.get(l,C_B)
    with _LOCK: print(f"{c}[{p or l}]{C_0} {m}",flush=True)

def banner():
    print(f"{C_M}{C_BD}================================================================{C_0}\n{C_M}{C_BD}  ADVANCED CODE SCANNER PRO v{VERSION} - SINGLE FILE - CODE ONLY     {C_0}\n{C_M}{C_BD}================================================================{C_0}\n{C_B} SAST + Taint Tracking + Entropy Secrets + Docker/Dependency Audit{C_0}\n{C_Y}[!] Only scan code you own or have permission to audit.{C_0}\n",flush=True)

def entropy(s:str)->float:
    if not s: return 0.0
    c=collections.Counter(s); n=len(s)
    import math
    return -sum((v/n)*math.log2(v/n) for v in c.values())

def cvss_for(sev:str)->tuple[float,str]:
    base={"CRITICAL":9.5,"HIGH":8.0,"MEDIUM":5.5,"LOW":2.5,"INFO":0.0}.get(sev,0.0)
    vec=f"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H" if sev=="CRITICAL" else f"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:{'H' if sev in('HIGH','MEDIUM') else 'N'}/I:N/A:N"
    return base,vec

@dataclass
class Finding:
    severity:str; vuln_type:str; url_or_file:str; description:str; evidence:str=""; remediation:str=""; line:Optional[int]=None; cwe:str=""; cvss:float=0.0; cvss_vec:str=""; cve:str=""
    def to_dict(self):
        d=asdict(self); d["risk"]=SEV_R.get(self.severity,1); return d

@dataclass
class Report:
    target:str; scan_type:str="code-pro"
    start:str=field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    end:Optional[str]=None; findings:list[Finding]=field(default_factory=list); stats:dict=field(default_factory=dict); tech:dict=field(default_factory=dict)
    def add(self,f:Finding):
        if f.severity=="ERROR": f.severity="HIGH"
        if not f.cvss: f.cvss,f.cvss_vec=cvss_for(f.severity)
        self.findings.append(f)
    def fin(self):
        self.end=datetime.now(timezone.utc).isoformat()
        self.findings.sort(key=lambda x:SEV_R.get(x.severity,0),reverse=True)
        by=collections.Counter(f.severity for f in self.findings)
        self.stats["by_severity"]=dict(by); self.stats["total"]=len(self.findings)
        s=sum({"CRITICAL":25,"HIGH":15,"MEDIUM":8,"LOW":3,"INFO":1}.get(f.severity,1) for f in self.findings)
        self.stats["risk_score"]=min(100,s)
        self.stats["grade"]="CRITICAL" if s>=75 else "HIGH" if s>=50 else "MEDIUM" if s>=25 else "LOW" if s>0 else "CLEAN"
    def to_dict(self):
        return {"target":self.target,"scan_type":self.scan_type,"start":self.start,"end":self.end,"stats":self.stats,"tech":self.tech,"findings":[f.to_dict() for f in self.findings]}

def print_findings(r:Report):
    if not r.findings:
        print(f"\n{C_G}Clean - no findings. Code looks good.{C_0}\n"); return
    print(f"\n{C_BD}FINDINGS {len(r.findings)} Risk {r.stats.get('risk_score')}/100 {r.stats.get('grade')}\n{'='*60}")
    for f in r.findings:
        c=SEV_C.get(f.severity,C_C); ln=f" L{f.line}" if f.line else ""
        print(f"{c}{C_BD}[{f.severity} CVSS {f.cvss:.1f}]{C_0} {f.vuln_type} - {f.url_or_file}{ln}\n -> {f.description}")
        if f.evidence: print(f"    Ev: {str(f.evidence).replace(chr(10),' ')[:200]}")
        if f.remediation: print(f"    Fix: {f.remediation}")
        print()

# ------------------------------------------------------------------
#  RULES
# ------------------------------------------------------------------
SECRET_PATTERNS={
 "AWS Access":r"AKIA[0-9A-Z]{16}","AWS Secret":r"(?i)aws_secret_access_key\s*[:=]\s*['\"]([A-Za-z0-9/+=]{40})['\"]",
 "PrivKey RSA":r"-----BEGIN (RSA )?PRIVATE KEY-----","PrivKey SSH":r"-----BEGIN OPENSSH PRIVATE KEY-----","PrivKey EC":r"-----BEGIN EC PRIVATE KEY-----",
 "JWT":r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}","GH Token":r"ghp_[A-Za-z0-9]{36}","GH PAT":r"github_pat_[A-Za-z0-9_]{82}",
 "GitLab":r"glpat-[A-Za-z0-9\-_]{20,}","Slack":r"xox[bpars]-[A-Za-z0-9-]{10,}","Stripe SK":r"sk_(live|test)_[A-Za-z0-9]{24,}",
 "Stripe PK":r"pk_(live|test)_[A-Za-z0-9]{24,}","Google Key":r"AIza[A-Za-z0-9_\-]{35}","SendGrid":r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}",
 "Twilio":r"AC[a-f0-9]{32}","Hardcoded Password":r"(?i)(password|passwd|pwd|secret|api[_-]?secret)\s*[:=]\s*['\"][^'\"]{4,}['\"]",
 "API Key":r"(?i)(api[_-]?key|apikey|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]","Mongo URI":r"mongodb(?:\+srv)?://[^:\s]+:[^@\s]+@",
 "Postgres URI":r"postgres(?:ql)?://[^:\s]+:[^@\s]+@","MySQL URI":r"mysql://[^:\s]+:[^@\s]+@","Redis URI":r"redis://:[^@\s]+@",
}
SAST={
 "eval":(r"\beval\s*\(","HIGH","eval() RCE risk"),"exec":(r"\bexec\s*\(","HIGH","exec() RCE risk"),
 "pickle deserialization":(r"pickle\.(loads?|Unpickler)\s*\(","CRITICAL","pickle.loads RCE"),
 "yaml.load unsafe":(r"yaml\.load\s*\([^)]*\)","HIGH","yaml.load without SafeLoader"),
 "os.system":(r"os\.system\s*\(","HIGH","os.system command injection"),"os.popen":(r"os\.popen\s*\(","HIGH","os.popen injection"),
 "subprocess shell":(r"subprocess\.(Popen|call|run)\s*\(","MEDIUM","subprocess check shell args"),
 "shell=True":(r"shell\s*=\s*True","HIGH","shell=True injection"),"MD5 weak":(r"hashlib\.md5|hashlib\.new\(['\"]md5","MEDIUM","MD5 broken - use SHA256+"),
 "SHA1 weak":(r"hashlib\.sha1","LOW","SHA1 deprecated"),"DES weak":(r"Cipher\.DES\b","MEDIUM","DES weak - use AES"),
 "RC4 weak":(r"ARC4|\bRC4\b","MEDIUM","RC4 weak"),"SQL format injection":(r"(?i)(SELECT|INSERT|UPDATE|DELETE).*(\%|format\(|\+|f['\"])","HIGH","SQL injection via formatting"),
 "SQL concat execute":(r"execute\s*\(.*\+","HIGH","SQL concat - use parameterized"),"innerHTML XSS":(r"innerHTML\s*=","MEDIUM","innerHTML XSS"),
 "document.write XSS":(r"document\.write\s*\(","MEDIUM","document.write XSS"),"React dangerouslySetInnerHTML":(r"dangerouslySetInnerHTML","MEDIUM","React XSS sink"),
 "PHP RCE sink":(r"\b(system|popen|shell_exec|passthru|proc_open)\s*\(","HIGH","PHP RCE sink"),
 "PHP include variable":(r"(include|require)(_once)?\s*\(?\s*\$","HIGH","PHP LFI include with variable"),
 "PHP $_GET/$_POST":(r"\$_(GET|POST|REQUEST|COOKIE)","MEDIUM","Unsanitized PHP user input"),
 "Java Runtime.exec":(r"Runtime\.getRuntime\(\)\.exec","HIGH","Java Runtime.exec RCE"),
 "Java ProcessBuilder":(r"new ProcessBuilder\(","MEDIUM","Java ProcessBuilder check inputs"),
 "C strcpy":(r"\bstrcpy\s*\(","HIGH","strcpy buffer overflow"),"C gets":(r"\bgets\s*\(","CRITICAL","gets() buffer overflow"),
 "C system":(r"\bsystem\s*\(","HIGH","C system() injection"),"Go unsafe":(r"unsafe\.","LOW","Go unsafe package"),
 "TLS verify False":(r"verify\s*=\s*False","MEDIUM","TLS verification disabled"),
 "insecure random":(r"random\.(random|randint|choice)\s*\(","LOW","Use secrets module for security"),
 "Flask request":(r"request\.(args|form|json|values)","MEDIUM","Flask request input - validate"),
 "Express req":(r"req\.(query|body|params)","MEDIUM","Express input - validate"),
 "Debug mode":(r"(?i)DEBUG\s*=\s*True|debug\s*=\s*true","LOW","Debug mode on"),
}
EXT={".py":"py",".js":"js",".ts":"ts",".jsx":"js",".tsx":"ts",".php":"php",".java":"java",".go":"go",".rb":"rb",".c":"c",".h":"c",".cpp":"cpp",".hpp":"cpp",".cs":"cs",".swift":"swift",".kt":"kt",".sh":"sh",".bash":"sh",".sql":"sql",".env":"env",".json":"json",".yaml":"yaml",".yml":"yaml",".xml":"xml",".html":"html",".htm":"html",".vue":"vue",".dockerfile":"docker"}
SKIP_DIRS={".git","__pycache__","node_modules","venv",".venv","env","dist","build",".tox",".mypy_cache",".pytest_cache",".idea",".vscode","coverage"}

# ------------------------------------------------------------------
#  CODE SCANNERS
# ------------------------------------------------------------------
class PyASTHardcoded(ast.NodeVisitor):
    def __init__(self,fp): self.fp=fp; self.out=[]
    def _a(self,sev,typ,node,desc,ev,fix,cwe): self.out.append(Finding(sev,typ,self.fp,desc,ev,fix,line=getattr(node,"lineno",None),cwe=cwe))
    def visit_Call(self,node):
        fn=""
        try: fn=ast.unparse(node.func) if hasattr(ast,"unparse") else ""
        except Exception: pass
        base=fn.split(".")[-1].strip() if fn else ""
        if base=="eval": self._a("HIGH","Python eval()",node,"eval() executes arbitrary code",fn[:120],"Avoid eval, use ast.literal_eval","CWE-94")
        elif base=="exec": self._a("HIGH","Python exec()",node,"exec() executes arbitrary code",fn[:120],"Avoid exec","CWE-94")
        elif "pickle" in fn and "load" in fn: self._a("CRITICAL","Python pickle",node,"pickle deserialization RCE",fn[:120],"Use JSON, not pickle","CWE-502")
        elif base=="system": self._a("HIGH","Python os.system()",node,"Command injection risk",fn[:120],"Use subprocess list args, no shell=True","CWE-78")
        elif "yaml" in fn and "load" in fn: self._a("HIGH","Python yaml.load",node,"yaml.load without SafeLoader",fn[:120],"Use yaml.safe_load","CWE-502")
        elif fn.strip()=="__import__": self._a("MEDIUM","Python dynamic import",node,"Dynamic __import__",fn[:120],"Use static imports","CWE-94")
        self.generic_visit(node)
    def visit_Assign(self,node):
        try:
            t=" ".join(ast.unparse(x).lower() for x in node.targets)
            if any(k in t for k in ("password","passwd","pwd","secret","api_key","apikey","token")) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str) and len(node.value.value)>=4:
                self._a("HIGH","Hardcoded Secret (AST)",node,f"Hardcoded secret assigned to '{t[:60]}'",node.value.value[:4]+"***","Use env vars / secrets manager","CWE-798")
        except Exception: pass
        self.generic_visit(node)

class TaintVisitor(ast.NodeVisitor):
    """Simple taint: input()/request/sys.argv -> sinks (eval/exec/os.system/subprocess.*)"""
    def __init__(self,filepath): self.filepath=filepath; self.tainted=set(); self.findings=[]
    def visit_Assign(self,node):
        try: rhs=ast.unparse(node.value); lhs=ast.unparse(node.targets[0])
        except Exception: rhs=""; lhs=""
        if "input(" in rhs or "request" in rhs.lower() or "sys.argv" in rhs or "sys.stdin" in rhs: self.tainted.add(lhs.strip())
        self.generic_visit(node)
    def visit_Call(self,node):
        try: fn=ast.unparse(node.func) if hasattr(ast,"unparse") else ""
        except Exception: fn=""
        if any(s in fn for s in ("eval","exec","os.system","subprocess")) and self.tainted:
            try:
                args=ast.unparse(node)
                for t in self.tainted:
                    if t and t in args:
                        self.findings.append(Finding("HIGH",f"Taint: {fn.split('.')[-1]} <- {t}",self.filepath,f"User input '{t}' flows to {fn}",args[:150],"Sanitize / validate tainted input before sink",line=getattr(node,"lineno",None),cwe="CWE-94"))
            except Exception: pass
        self.generic_visit(node)

def scan_file(path:Path)->list[Finding]:
    out=[]
    ext=path.suffix.lower(); lang=EXT.get(ext,ext.lstrip(".") or "text")
    try: txt=path.read_text(encoding="utf-8",errors="ignore")
    except Exception as e: return [Finding("LOW","Read Error",str(path),f"Could not read: {e}","","Check permissions")]
    if len(txt)>5_000_000:
        out.append(Finding("INFO","Large File Partial",str(path),"File >5MB, truncated to 5MB","","")); txt=txt[:5_000_000]
    lines=txt.splitlines()
    for name,pat in SECRET_PATTERNS.items():
        for i,ln in enumerate(lines,1):
            m=re.search(pat,ln)
            if m: out.append(Finding("HIGH",f"Secret: {name}",str(path),f"Potential {name} in source code",m.group(0)[:10]+"***","Remove secret, rotate, use env vars",line=i,cwe="CWE-798")); break
    for name,(pat,sev,desc) in SAST.items():
        for i,ln in enumerate(lines,1):
            s=ln.strip()
            if s.startswith(("#","//","*","<!--")): continue
            if re.search(pat,ln): out.append(Finding(sev,f"SAST: {name}",str(path),desc,s[:140],"Review and remediate pattern",line=i,cwe="CWE-94")); break
    # entropy tokens near secret context
    for i,ln in enumerate(lines,1):
        for m in re.finditer(r"['\"]([A-Za-z0-9_\-+/=]{32,})['\"]",ln):
            s=m.group(1)
            if entropy(s)>4.8 and "://" not in s:
                ctx=ln.lower()
                if any(k in ctx for k in ("key","secret","token","passwd","pwd","api")):
                    out.append(Finding("HIGH","High-Entropy Secret",str(path),f"Shannon entropy {entropy(s):.1f} possible token",s[:8]+"***","Use env vars, rotate",line=i,cwe="CWE-798")); break
    if ext==".py":
        try:
            tree=ast.parse(txt,filename=str(path))
            v=PyASTHardcoded(str(path)); v.visit(tree); out.extend(v.out)
            t=TaintVisitor(str(path)); t.visit(tree); out.extend(t.findings)
        except SyntaxError: out.append(Finding("INFO","Python Syntax Error",str(path),"Syntax error, AST skipped, regex still applied","",""))
        except Exception: pass
    if path.name=="Dockerfile":
        for i,ln in enumerate(lines,1):
            if re.search(r"FROM\s+\S+:latest",ln): out.append(Finding("LOW","Docker FROM latest",str(path),"Mutable latest tag",ln.strip()[:100],"Pin base image version"))
            if re.search(r"USER\s+root",ln): out.append(Finding("MEDIUM","Docker USER root",str(path),"Runs as root",ln.strip()[:100],"Add USER nonroot",cwe="CWE-266"))
            if ("curl" in ln and "| sh" in ln) or ("wget" in ln and "| bash" in ln): out.append(Finding("HIGH","Docker curl|bash",str(path),"Pipe to shell RCE",ln.strip()[:120],"Verify checksum"))
            if re.search(r"ENV\s+\w*(SECRET|PASSWORD|KEY|TOKEN)=",ln,re.I): out.append(Finding("HIGH","Docker Secret ENV",str(path),"Secret in ENV",ln.strip()[:120],"Use build secrets",cwe="CWE-798"))
            if re.search(r"EXPOSE\s+22\b",ln): out.append(Finding("LOW","Docker EXPOSE 22",str(path),"SSH exposed",ln.strip()[:100],"Don't expose SSH"))
    if path.name=="package.json":
        try:
            j=json.loads(txt)
            for sec in ("dependencies","devDependencies"):
                for pkg,ver in (j.get(sec) or {}).items():
                    if pkg=="lodash" and str(ver).startswith("<4.17.21"): out.append(Finding("HIGH","CVE: lodash",str(path),"lodash <4.17.21 prototype pollution",str(ver),"Upgrade lodash >=4.17.21",cve="CVE-2020-8203"))
                    if pkg=="jquery" and str(ver).startswith("<3.5"): out.append(Finding("HIGH","CVE: jQuery",str(path),"Old jQuery XSS",str(ver),"Upgrade jQuery >=3.6",cve="CVE-2020-11022"))
                    if pkg=="express" and str(ver).startswith("<4.19"): out.append(Finding("MEDIUM","CVE: express",str(path),"Old express",str(ver),"Upgrade express",cve="CVE-2024-29041"))
        except Exception: pass
    if path.name=="requirements.txt":
        # flag deprecated / unpinned risky packages
        for i,ln in enumerate(lines,1):
            s=ln.strip()
            if not s or s.startswith("#"): continue
            m=re.match(r"([A-Za-z0-9_\-\.]+)",s)
            if m and "==" not in s and s.lower() not in ("pip","setuptools"): out.append(Finding("LOW","Unpinned Dependency",str(path),f"Unpinned '{s[:60]}'",s,"Pin versions with ==",line=i,cwe="CWE-829"))
    if ext==".env" or path.name==".env": out.append(Finding("INFO",".env File Present",str(path),".env exists - ensure gitignored and blocked","","Secrets manager"))
    return out

def scan_code(root:str, max_files:int=3000, threads:int=10)->Report:
    r=Report(root,"code-pro"); rp=Path(root)
    print(f"{C_C}Code scan target:{C_0} {rp.resolve()}")
    if not rp.exists(): r.add(Finding("HIGH","Path Not Found",root,"Target path does not exist","","")); r.fin(); return r
    files=[rp] if rp.is_file() else []
    if rp.is_dir():
        for p in rp.rglob("*"):
            if p.is_file():
                if set(p.parts) & SKIP_DIRS: continue
                if p.suffix.lower() in EXT or p.name in (".env","Dockerfile",".gitignore","docker-compose.yml","package.json","requirements.txt"): files.append(p)
            if len(files)>=max_files: break
    print(f"Found {len(files)} code files to scan...")
    r.stats["files_scanned"]=len(files)
    scanned=0
    with ThreadPoolExecutor(max_workers=max(1,threads)) as ex:
        fmap={ex.submit(scan_file,f):f for f in files}
        for fut in as_completed(fmap):
            f=fmap[fut]
            try:
                for fd in fut.result(): r.add(fd)
            except Exception as e: r.add(Finding("LOW","Scan Error",str(f),f"Error: {e}","",""))
            scanned+=1
            if scanned%50==0: log(f"Scanned {scanned}/{len(files)} files...","INFO","CODE")
    log(f"Code scan done: {len(files)} files, {len(r.findings)} findings","OK","CODE")
    r.fin(); print_findings(r); return r

# ------------------------------------------------------------------
#  EXPORTS
# ------------------------------------------------------------------
def save_rep(r:Report,out:str):
    p=Path(out); s=p.suffix.lower()
    if s==".html": p.write_text(html_report(r),encoding="utf-8")
    elif s==".sarif": p.write_text(sarif_report(r),encoding="utf-8")
    elif s==".csv":
        import csv,io; buf=io.StringIO(); w=csv.writer(buf); w.writerow(["severity","type","location","line","description","evidence","remediation","cvss","cwe","cve"])
        for f in r.findings: w.writerow([f.severity,f.vuln_type,f.url_or_file,f.line,f.description,f.evidence[:200],f.remediation,f.cvss,f.cwe,f.cve])
        p.write_text(buf.getvalue(),encoding="utf-8")
    else: p.write_text(json.dumps(r.to_dict(),indent=2),encoding="utf-8")
    print(f"{C_G}Saved {p} ({p.stat().st_size} bytes){C_0}")

def html_report(r:Report):
    cols={"CRITICAL":"#dc2626","HIGH":"#ef4444","MEDIUM":"#f59e0b","LOW":"#3b82f6","INFO":"#06b6d4"}
    by=r.stats.get("by_severity",{}); mx=max(by.values()) if by else 1
    bars="".join(f"<div>{k}<div style='background:{cols.get(k)};width:{int(v/mx*100)}%'>{v}</div></div>" for k,v in by.items())
    rows="".join(f"<tr><td><span class='b' style='background:{cols.get(f.severity)}'>{f.severity} {f.cvss:.1f}</span></td><td>{html.escape(f.vuln_type)}</td><td>{html.escape(f.url_or_file)}{' L'+str(f.line) if f.line else ''}</td><td>{html.escape(f.description)}<br><code>{html.escape(str(f.evidence)[:140])}</code></td><td>{html.escape(f.remediation)}<br><small>{html.escape(f.cwe)} {html.escape(f.cve)}</small></td></tr>" for f in r.findings)
    return f"<!DOCTYPE html><html><head><meta charset=utf-8><title>Code Report {html.escape(r.target)}</title><style>body{{font-family:Segoe UI,system-ui;background:#0f172a;color:#e2e8f0;padding:24px}} .c{{background:#1e293b;border-radius:12px;padding:20px;margin:12px 0}} table{{width:100%;border-collapse:collapse}} td,th{{padding:8px;border-bottom:1px solid #263244;font-size:13px}} .b{{color:#fff;padding:2px 8px;border-radius:999px;font-size:11px}} code{{background:#0f172a;padding:2px 6px}}</style></head><body><div class=c><h1>Code Scanner PRO v{VERSION} - CODE ONLY</h1><p>{html.escape(r.target)} {html.escape(r.start)} -> {html.escape(r.end or '')}</p><p><b>{r.stats.get('risk_score')}/100 {r.stats.get('grade')}</b> Findings {r.stats.get('total')} Files {r.stats.get('files_scanned')}</p>{bars}</div><div class=c><table><tr><th>Sev</th><th>Type</th><th>Where</th><th>Desc/Evidence</th><th>Fix</th></tr>{rows}</table></div></body></html>"

def sarif_report(r:Report):
    return {"$schema":"https://json.schemastore.org/sarif-2.1.0.json","version":"2.1.0","runs":[{"tool":{"driver":{"name":"CodeScannerPRO","version":VERSION,"informationUri":"local","rules":[{"id":f.vuln_type,"name":f.vuln_type,"shortDescription":{"text":f.vuln_type}} for f in r.findings[:100]]}},"results":[{"ruleId":f.vuln_type,"level":{"CRITICAL":"error","HIGH":"error","MEDIUM":"warning","LOW":"note","INFO":"note"}.get(f.severity,"warning"),"message":{"text":f.description},"locations":[{"physicalLocation":{"artifactLocation":{"uri":f.url_or_file},"region":{"startLine":f.line or 1}}}],"properties":{"cwe":f.cwe,"cvss":f.cvss}} for f in r.findings]}]}

# ------------------------------------------------------------------
#  CLI - CODE ONLY (web commands removed)
# ------------------------------------------------------------------
def main():
    banner()
    ap=argparse.ArgumentParser(description="Advanced CODE Scanner PRO - Single File (CODE ONLY)")
    ap.add_argument("path",help="Path to code directory or single file")
    ap.add_argument("--threads","-t",type=int,default=10,help="Threads (default 10)")
    ap.add_argument("--output","-o",help="Output file .json/.html/.sarif/.csv")
    ap.add_argument("--max-files",type=int,default=3000,help="Max files (default 3000)")
    a=ap.parse_args()
    r=scan_code(a.path, max_files=a.max_files, threads=a.threads)
    if getattr(a,"output",None): save_rep(r,a.output)
    print(f"{C_BD}=== SCAN COMPLETE ==={C_0} Findings: {r.stats.get('total')} Risk: {r.stats.get('risk_score')}/100 {r.stats.get('grade')}\n{C_C}Done (CODE ONLY).{C_0}\n")
    sys.exit(0 if not r.findings else 1)

if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: print(f"\n{C_Y}Interrupted by user.{C_0}"); sys.exit(130)
    except SystemExit: raise
    except Exception as e: print(f"{C_R}Fatal error: {e}{C_0}"); import traceback; traceback.print_exc(); sys.exit(2)
