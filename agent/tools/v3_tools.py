"""New enterprise v3 tool groups — exposes v3 features as agent tools.

Tools grouped here:
  * RAG search (semantic codebase query)
  * Multi-agent run
  * Self-reflection
  * Code metrics + dead code + duplicates
  * SAST scan
  * SBOM generate
  * Infrastructure scan
  * PII scan
  * Docker orchestration
  * Cloud cost analysis
  * Vision analyze
  * Browser navigate
  * Git workflow
  * CI/CD generate
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .base import Tool, ToolResult


def _rag_search(query: str, top_k: int = 5) -> ToolResult:
    from ..rag_v2 import get_vector_store
    store = get_vector_store()
    if not store.documents:
        return ToolResult(output="No documents indexed. Use /index-codebase to index first.")
    context = store.answer_context(query, top_k=top_k)
    return ToolResult(output=context, metadata={"documents": len(store.documents)})


def _index_codebase(root: str = ".") -> ToolResult:
    from ..rag_v2 import index_codebase
    stats = index_codebase(root)
    return ToolResult(
        output=f"Indexed {stats['newly_indexed']} chunks from {stats['sources']} sources. Total: {stats['documents']} chunks.",
        metadata=stats,
    )


def _code_metrics(root: str = ".") -> ToolResult:
    from ..code_metrics import analyze_codebase, suggest_refactoring
    report = analyze_codebase(root)
    suggestions = suggest_refactoring(report)
    output = report.dashboard()
    if suggestions:
        output += "\n\nRefactoring suggestions:\n" + "\n".join(f"  - {s}" for s in suggestions)
    return ToolResult(output=output, metadata={
        "files": report.files_scanned,
        "functions": report.total_functions,
        "dead_code": len(report.dead_code),
        "duplicates": len(report.duplicates),
    })


def _sast_scan(root: str = ".") -> ToolResult:
    from ..sast import scan_codebase
    report = scan_codebase(root)
    return ToolResult(output=report.summary(), metadata={
        "findings": len(report.findings),
        "critical": report.critical_count,
        "high": report.high_count,
    })


def _generate_sbom(root: str = ".") -> ToolResult:
    from ..sbom import generate_sbom, sbom_summary
    import json
    sbom = generate_sbom(root)
    summary = sbom_summary(sbom)
    # Also write to disk.
    path = Path(root) / "sbom.json"
    path.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    return ToolResult(output=summary + f"\n\nWritten to: {path}")


def _scan_infrastructure(root: str = ".") -> ToolResult:
    from ..iac_scanner import scan_infrastructure
    reports = scan_infrastructure(root)
    if not reports:
        return ToolResult(output="No Dockerfiles, Terraform or CloudFormation files found.")
    parts = [r.summary() for r in reports]
    return ToolResult(output="\n\n".join(parts))


def _scan_pii(root: str = ".") -> ToolResult:
    from ..pii_scanner import scan_directory_for_pii
    report = scan_directory_for_pii(root)
    return ToolResult(output=report.summary(), metadata={"findings": len(report.findings)})


def _docker_compose_generate(language: str = "python", port: int = 8000) -> ToolResult:
    from ..docker_orchestrator import generate_dockerfile
    path = generate_dockerfile(language=language, port=port)
    return ToolResult(output=f"Generated Dockerfile for {language} at {path}")


def _cloud_cost_analyze() -> ToolResult:
    from ..cloud_cost import analyse_resources, EXAMPLE_RESOURCES
    report = analyse_resources(EXAMPLE_RESOURCES)
    return ToolResult(output=report.dashboard(), metadata={
        "resources": len(report.resources),
        "savings": len(report.savings),
        "annual_saving": report.total_annual_saving,
    })


def _analyze_image(path: str = "", url: str = "", prompt: str = "Describe this image.") -> ToolResult:
    from ..vision import analyze_image
    if path:
        result = analyze_image(path=path, prompt=prompt)
    elif url:
        result = analyze_image(url=url, prompt=prompt)
    else:
        return ToolResult(output="Error: provide either path or url", success=False)
    return ToolResult(output=result.description, metadata={
        "width": result.width,
        "height": result.height,
        "format": result.format,
        "size_bytes": result.size_bytes,
    })


def _browser_navigate(url: str, take_screenshot: bool = False) -> ToolResult:
    from ..browser_automation import navigate
    result = navigate(url, take_screenshot=take_screenshot)
    if not result.success:
        return ToolResult(output=f"Error: {result.error}", success=False)
    output = f"Title: {result.title}\nURL: {result.url}\n\nText:\n{result.text[:3000]}"
    if result.screenshot_path:
        output += f"\n\nScreenshot: {result.screenshot_path}"
    return ToolResult(output=output, metadata={"url": url, "title": result.title})


def _git_workflow(branch: str, message: str, pr_title: str = "") -> ToolResult:
    from ..git_workflow import full_workflow
    result = full_workflow(branch_name=branch, commit_message=message, pr_title=pr_title)
    return ToolResult(
        output=result.message,
        success=result.success,
        metadata=result.details,
    )


def _generate_cicd(platform: str = "all", root: str = ".") -> ToolResult:
    from ..cicd_builder import generate_all, generate_github_actions, generate_gitlab_ci, generate_circleci, generate_jenkinsfile
    if platform == "all":
        paths = generate_all(root)
    elif platform == "github":
        paths = {"github": generate_github_actions(root)}
    elif platform == "gitlab":
        paths = {"gitlab": generate_gitlab_ci(root)}
    elif platform == "circleci":
        paths = {"circleci": generate_circleci(root)}
    elif platform == "jenkins":
        paths = {"jenkins": generate_jenkinsfile(root)}
    else:
        return ToolResult(output=f"Unknown platform: {platform}. Use: all, github, gitlab, circleci, jenkins", success=False)
    output = "Generated CI/CD config(s):\n" + "\n".join(f"  {k}: {v}" for k, v in paths.items())
    return ToolResult(output=output)


def _knowledge_graph_build(root: str = ".") -> ToolResult:
    from ..knowledge_graph import build_graph_from_codebase
    kg = build_graph_from_codebase(root)
    return ToolResult(output=kg.dashboard(), metadata=kg.stats())


def _knowledge_graph_query(name: str, kind: str = "") -> ToolResult:
    from ..knowledge_graph import get_knowledge_graph
    # Re-build from the current directory if empty.
    kg = get_knowledge_graph()
    if not kg.nodes:
        kg = build_graph_from_codebase(".")
    results = kg.find(name, kind=kind or None)
    if not results:
        return ToolResult(output=f"No nodes found matching '{name}'.")
    output = f"Found {len(results)} node(s):\n"
    for node in results[:20]:
        output += f"  [{node.kind}] {node.name} at {node.location}\n"
    return ToolResult(output=output)


def _long_term_memory_recall(query: str) -> ToolResult:
    from ..long_term_memory import get_long_term_memory
    mem = get_long_term_memory()
    context = mem.context_for_query(query)
    return ToolResult(output=context or "No relevant memories found.")


def _long_term_memory_remember(fact: str, category: str = "lesson") -> ToolResult:
    from ..long_term_memory import get_long_term_memory
    mem = get_long_term_memory()
    fact_id = mem.record_fact(fact, category=category)
    return ToolResult(output=f"Remembered fact '{fact}' (id={fact_id}, category={category}).")


def get_v3_tools() -> list[Tool]:
    s = {"type": "string"}
    i = {"type": "integer"}
    b = {"type": "boolean"}
    return [
        Tool(
            name="rag_search",
            description="Semantic search over the indexed codebase (embedding-based). Use /index-codebase first.",
            parameters={"type": "object", "properties": {"query": s, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]},
            func=_rag_search,
        ),
        Tool(
            name="index_codebase",
            description="Index the codebase into the vector store for semantic search.",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_index_codebase,
            dangerous=True,
        ),
        Tool(
            name="code_metrics",
            description="Analyze codebase: complexity, dead code, duplicates, TODOs. Returns a full report.",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_code_metrics,
        ),
        Tool(
            name="sast_scan",
            description="Run static security analysis on the codebase (SQL injection, XSS, secrets, etc.).",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_sast_scan,
        ),
        Tool(
            name="generate_sbom",
            description="Generate a CycloneDX SBOM (Software Bill of Materials) for the project.",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_generate_sbom,
        ),
        Tool(
            name="scan_infrastructure",
            description="Scan Dockerfiles, Terraform and CloudFormation for insecure configurations.",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_scan_infrastructure,
        ),
        Tool(
            name="scan_pii",
            description="Scan for PII (emails, phone numbers, credit cards, SSNs, Aadhaar, PAN).",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_scan_pii,
        ),
        Tool(
            name="generate_dockerfile",
            description="Generate a production-ready Dockerfile for Python or Node.js.",
            parameters={"type": "object", "properties": {"language": {"type": "string", "default": "python"}, "port": {"type": "integer", "default": 8000}}},
            func=_docker_compose_generate,
        ),
        Tool(
            name="cloud_cost_analyze",
            description="Analyze cloud resources and suggest cost savings (right-size, RI, spot, terminate idle).",
            parameters={"type": "object", "properties": {}},
            func=_cloud_cost_analyze,
        ),
        Tool(
            name="analyze_image",
            description="Analyze an image (file or URL) — describe contents, extract text, detect UI bugs.",
            parameters={"type": "object", "properties": {"path": s, "url": s, "prompt": {"type": "string", "default": "Describe this image."}}},
            func=_analyze_image,
        ),
        Tool(
            name="browser_navigate",
            description="Navigate to a URL with a headless browser and return page text/HTML.",
            parameters={"type": "object", "properties": {"url": s, "take_screenshot": {"type": "boolean", "default": False}}, "required": ["url"]},
            func=_browser_navigate,
        ),
        Tool(
            name="git_workflow",
            description="Run the full git workflow: branch -> commit -> push -> (optional PR).",
            parameters={"type": "object", "properties": {"branch": s, "message": s, "pr_title": {"type": "string", "default": ""}}, "required": ["branch", "message"]},
            func=_git_workflow,
            dangerous=True,
        ),
        Tool(
            name="generate_cicd",
            description="Generate CI/CD config files (github, gitlab, circleci, jenkins, or all).",
            parameters={"type": "object", "properties": {"platform": {"type": "string", "default": "all"}, "root": {"type": "string", "default": "."}}},
            func=_generate_cicd,
        ),
        Tool(
            name="knowledge_graph_build",
            description="Build a knowledge graph from the codebase (files, classes, functions, imports).",
            parameters={"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            func=_knowledge_graph_build,
        ),
        Tool(
            name="knowledge_graph_query",
            description="Query the knowledge graph for entities by name.",
            parameters={"type": "object", "properties": {"name": s, "kind": {"type": "string", "default": ""}}, "required": ["name"]},
            func=_knowledge_graph_query,
        ),
        Tool(
            name="memory_recall",
            description="Recall relevant facts and past interactions from long-term memory.",
            parameters={"type": "object", "properties": {"query": s}, "required": ["query"]},
            func=_long_term_memory_recall,
        ),
        Tool(
            name="memory_remember",
            description="Store a fact in long-term memory for future reference.",
            parameters={"type": "object", "properties": {"fact": s, "category": {"type": "string", "default": "lesson"}}, "required": ["fact"]},
            func=_long_term_memory_remember,
        ),
    ]
