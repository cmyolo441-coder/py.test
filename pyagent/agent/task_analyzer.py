"""Task complexity analyzer — decides when to auto-orchestrate sub-agents.

Examines user input and determines:
  1. Is this task complex enough for parallel sub-agents?
  2. Which specialists should be spawned?
  3. What's the recommended orchestration mode?

The agent uses this to automatically launch sub-agents for large tasks
without the user having to manually call /multi-agent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Keywords that signal multi-step / multi-domain work.
_COMPLEX_KEYWORDS = frozenset([
    # Building / creating
    "build", "create", "implement", "develop", "design", "architect",
    "setup", "set up", "establish", "scaffold", "generate",
    # Refactoring / large changes
    "refactor", "rewrite", "migrate", "overhaul", "restructure", "reorganize",
    # Analysis / review
    "analyze", "review", "audit", "evaluate", "assess", "inspect",
    # Multi-file / project-wide
    "project", "codebase", "application", "system", "module", "package",
    "codebase", "entire", "all files", "every file", "across",
    # Testing
    "test", "testing", "tests", "coverage", "regression",
    # Security
    "security", "vulnerability", "vulnerabilities", "secure", "hardening",
    # Documentation
    "documentation", "docs", "readme", "guide", "tutorial",
    # Debugging complex issues
    "debug", "debugging", "investigate", "root cause", "diagnose",
    # Multi-aspect
    "optimize", "performance", "quality", "improve", "enhancement",
])

# Patterns that indicate multiple requirements (bullet points, numbered lists, "and").
_MULTI_REQUIREMENT_PATTERNS = [
    r"(?:^|\n)\s*[-*]\s+",           # bullet points
    r"(?:^|\n)\s*\d+[.)]\s+",       # numbered lists
    r"\band\b.*\band\b",             # multiple "and"
    r"\balso\b",                     # "also" indicates additional req
    r"\bthen\b.*\bthen\b",          # multiple "then"
    r"\bfirst\b.*\bthen\b.*\bthen\b", # sequence
    r"\bstep\s+\d+",                # explicit steps
]

# Specialist mapping: keywords -> recommended specialists.
_SPECIALIST_KEYWORDS: dict[str, list[str]] = {
    # Code writing / building
    "build": ["coder", "planner"],
    "create": ["coder", "planner"],
    "implement": ["coder", "planner"],
    "develop": ["coder", "planner"],
    "write": ["coder"],
    "code": ["coder"],
    "function": ["coder"],
    "class": ["coder"],
    "module": ["coder"],
    "script": ["coder"],
    # Design / architecture
    "design": ["planner", "coder"],
    "architect": ["planner", "coder"],
    "architecture": ["planner"],
    "structure": ["planner"],
    # Refactoring
    "refactor": ["coder", "reviewer"],
    "rewrite": ["coder", "reviewer"],
    "migrate": ["coder", "planner"],
    "restructure": ["coder", "planner"],
    # Review / analysis
    "review": ["reviewer", "security"],
    "audit": ["reviewer", "security"],
    "analyze": ["researcher", "reviewer"],
    "evaluate": ["reviewer"],
    "inspect": ["reviewer"],
    # Testing
    "test": ["tester", "coder"],
    "tests": ["tester", "coder"],
    "testing": ["tester"],
    "coverage": ["tester"],
    "regression": ["tester"],
    # Security
    "security": ["security", "reviewer"],
    "vulnerability": ["security"],
    "vulnerabilities": ["security"],
    "secure": ["security"],
    "hardening": ["security"],
    # Documentation
    "documentation": ["writer"],
    "docs": ["writer"],
    "readme": ["writer"],
    "guide": ["writer"],
    # Debugging
    "debug": ["debugger", "researcher"],
    "debugging": ["debugger"],
    "investigate": ["debugger", "researcher"],
    "root cause": ["debugger"],
    "diagnose": ["debugger"],
    # Optimization
    "optimize": ["coder", "reviewer"],
    "performance": ["coder", "reviewer"],
    "speed": ["coder"],
    "fast": ["coder"],
    # Research
    "research": ["researcher"],
    "find": ["researcher"],
    "search": ["researcher"],
    "look up": ["researcher"],
    "explain": ["researcher"],
    "how": ["researcher"],
    "what is": ["researcher"],
}


@dataclass
class TaskAnalysis:
    """Result of task complexity analysis."""
    is_complex: bool
    confidence: float  # 0.0 - 1.0
    recommended_specialists: list[str]
    orchestration_mode: str  # "sequential" | "parallel" | "pipeline" | "single"
    reason: str


def analyze_task(user_input: str) -> TaskAnalysis:
    """Analyze a user task and determine if it needs sub-agents.

    Returns a TaskAnalysis with complexity assessment and recommendations.
    """
    text = user_input.lower().strip()
    word_count = len(text.split())

    # Score components
    complexity_score = 0.0
    matched_specialists: set[str] = set()
    reasons: list[str] = []

    # 1. Length-based scoring (longer = more complex)
    if word_count > 100:
        complexity_score += 0.3
        reasons.append("long task description")
    elif word_count > 50:
        complexity_score += 0.2
        reasons.append("detailed task")
    elif word_count > 20:
        complexity_score += 0.1

    # 2. Keyword matching
    keyword_hits = 0
    for keyword in _COMPLEX_KEYWORDS:
        if keyword in text:
            keyword_hits += 1
            # Find matching specialists
            if keyword in _SPECIALIST_KEYWORDS:
                matched_specialists.update(_SPECIALIST_KEYWORDS[keyword])

    if keyword_hits >= 5:
        complexity_score += 0.4
        reasons.append(f"many complexity keywords ({keyword_hits})")
    elif keyword_hits >= 3:
        complexity_score += 0.3
        reasons.append(f"multiple complexity keywords ({keyword_hits})")
    elif keyword_hits >= 1:
        complexity_score += 0.1

    # 3. Multi-requirement detection
    multi_req_hits = 0
    for pattern in _MULTI_REQUIREMENT_PATTERNS:
        if re.search(pattern, user_input, re.MULTILINE | re.IGNORECASE):
            multi_req_hits += 1

    if multi_req_hits >= 3:
        complexity_score += 0.3
        reasons.append("multiple requirements detected")
    elif multi_req_hits >= 2:
        complexity_score += 0.2
        reasons.append("multiple requirements")
    elif multi_req_hits >= 1:
        complexity_score += 0.1

    # 4. Sentence count (more sentences = more complex)
    sentence_count = max(1, len(re.split(r'[.!?]+', text)))
    if sentence_count > 10:
        complexity_score += 0.2
        reasons.append(f"many sentences ({sentence_count})")
    elif sentence_count > 5:
        complexity_score += 0.1

    # 5. Code-related patterns (file extensions, paths, etc.)
    code_patterns = [
        r'\.py\b', r'\.js\b', r'\.ts\b', r'\.go\b', r'\.rs\b',
        r'function\s+\w+', r'class\s+\w+', r'def\s+\w+',
        r'import\s+', r'from\s+\w+\s+import',
        r'npm\s+', r'pip\s+', r'cargo\s+',
        r'git\s+', r'docker\s+', r'kubectl\s+',
    ]
    code_hits = sum(1 for p in code_patterns if re.search(p, text))
    if code_hits >= 3:
        complexity_score += 0.2
        reasons.append("multiple code references")

    # Determine if complex (threshold: 0.4)
    is_complex = complexity_score >= 0.4
    confidence = min(1.0, complexity_score)

    # Determine orchestration mode
    if not is_complex:
        mode = "single"
        specialists = []
    elif len(matched_specialists) >= 3:
        mode = "parallel"
        specialists = list(matched_specialists)[:6]  # cap at 6
    elif "planner" in matched_specialists and "coder" in matched_specialists:
        mode = "pipeline"
        specialists = ["planner", "coder", "reviewer"]
    else:
        mode = "sequential"
        specialists = list(matched_specialists)[:4]

    # Ensure at least some specialists if complex
    if is_complex and not specialists:
        specialists = ["researcher", "coder", "reviewer"]
        mode = "parallel"

    reason = "; ".join(reasons) if reasons else "simple task"

    return TaskAnalysis(
        is_complex=is_complex,
        confidence=confidence,
        recommended_specialists=specialists,
        orchestration_mode=mode,
        reason=reason,
    )


def should_auto_orchestrate(user_input: str, config: Any = None) -> TaskAnalysis:
    """Public API: determine if a task should be auto-orchestrated.

    Respects config settings for auto-orchestration.
    """
    # Check if auto-orchestration is enabled
    if config is not None and not getattr(config, "auto_orchestrate", True):
        return TaskAnalysis(
            is_complex=False,
            confidence=0.0,
            recommended_specialists=[],
            orchestration_mode="single",
            reason="auto-orchestration disabled in config",
        )

    # Don't auto-orchestrate very short inputs (likely simple questions)
    if len(user_input.strip()) < 20:
        return TaskAnalysis(
            is_complex=False,
            confidence=0.0,
            recommended_specialists=[],
            orchestration_mode="single",
            reason="input too short",
        )

    # Don't auto-orchestrate if it looks like a simple command/query
    simple_patterns = [
        r'^(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure)',
        r'^(what|who|when|where|why|how)\s+is\b',
        r'^(show|list|print|display|tell me)\s+',
        r'^/',
    ]
    for pattern in simple_patterns:
        if re.match(pattern, user_input.strip(), re.IGNORECASE):
            return TaskAnalysis(
                is_complex=False,
                confidence=0.0,
                recommended_specialists=[],
                orchestration_mode="single",
                reason="simple query/command",
            )

    return analyze_task(user_input)
