"""Goal templates — pre-built, reusable goal workflows for common tasks.

Each template is a real, working prompt template tuned for a specific goal
class. The ``/goal-templates`` command lets users browse and launch them.
Templates dramatically reduce the friction of Goal Mode for common scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GoalTemplate:
    name: str
    description: str
    category: str
    prompt: str
    suggested_effort: str = "ultrahype"
    tags: list[str] = field(default_factory=list)


TEMPLATES: list[GoalTemplate] = [
    GoalTemplate(
        name="ship-feature",
        description="Implement a complete feature end-to-end: tests + code + docs",
        category="coding",
        prompt=(
            "Implement the following feature completely: {feature}\n\n"
            "Requirements:\n"
            "1. Read the existing codebase to understand conventions\n"
            "2. Write the implementation following existing style\n"
            "3. Add unit tests covering happy path + edge cases\n"
            "4. Update relevant documentation\n"
            "5. Run the test suite and ensure it passes\n"
            "6. Verify the feature works with a manual smoke test\n\n"
            "Do not stop until the feature is fully working and tested."
        ),
        suggested_effort="ultrahype",
        tags=["coding", "feature", "tests"],
    ),
    GoalTemplate(
        name="fix-bug",
        description="Reproduce, diagnose, fix and verify a bug",
        category="coding",
        prompt=(
            "Fix this bug completely: {bug}\n\n"
            "Steps:\n"
            "1. Reproduce the bug (write a failing test if possible)\n"
            "2. Diagnose the root cause — read the relevant code\n"
            "3. Implement the fix\n"
            "4. Verify the test now passes\n"
            "5. Check for similar bugs elsewhere in the codebase\n"
            "6. Add a regression test\n\n"
            "Report: root cause, fix summary, files changed."
        ),
        suggested_effort="ultracombo",
        tags=["coding", "bug", "tests"],
    ),
    GoalTemplate(
        name="refactor-module",
        description="Refactor a module safely — preserve behaviour, improve structure",
        category="coding",
        prompt=(
            "Refactor the module at {path} to improve: {goals}\n\n"
            "Constraints:\n"
            "- Preserve all existing behaviour (tests must still pass)\n"
            "- Make incremental, reviewable changes\n"
            "- Update tests if signatures change\n"
            "- Document any new patterns\n\n"
            "Run the test suite before and after each change."
        ),
        suggested_effort="ultracombo",
        tags=["coding", "refactor"],
    ),
    GoalTemplate(
        name="code-review",
        description="Deep code review with severity-tagged findings",
        category="review",
        prompt=(
            "Perform a comprehensive code review of: {target}\n\n"
            "Cover:\n"
            "- Bugs and logic errors\n"
            "- Security vulnerabilities (OWASP Top 10)\n"
            "- Performance issues\n"
            "- Style/convention violations\n"
            "- Missing tests\n"
            "- Documentation gaps\n\n"
            "Group findings by severity (CRITICAL/HIGH/MEDIUM/LOW). "
            "For each: file:line, description, suggested fix. "
            "End with APPROVE/REQUEST_CHANGES/BLOCK verdict."
        ),
        suggested_effort="ultramax",
        tags=["review", "security", "quality"],
    ),
    GoalTemplate(
        name="security-audit",
        description="Full security audit of a project",
        category="security",
        prompt=(
            "Conduct a thorough security audit of: {target}\n\n"
            "Check:\n"
            "- Dependency vulnerabilities (CVEs)\n"
            "- Hardcoded secrets / API keys\n"
            "- Injection vulnerabilities (SQL, command, XSS)\n"
            "- Auth/authz flaws\n"
            "- Cryptographic weaknesses\n"
            "- Insecure configs\n\n"
            "Produce a report with CVSS-style severity, proof, and remediation "
            "for each finding."
        ),
        suggested_effort="enterprise",
        tags=["security", "audit"],
    ),
    GoalTemplate(
        name="add-tests",
        description="Generate comprehensive test coverage for a module",
        category="testing",
        prompt=(
            "Add comprehensive test coverage to: {target}\n\n"
            "Steps:\n"
            "1. Read the module and identify all public functions/classes\n"
            "2. For each, write tests covering:\n"
            "   - Happy path\n"
            "   - Edge cases (empty, null, boundary values)\n"
            "   - Error cases (invalid input, exceptions)\n"
            "3. Run the tests and ensure they pass\n"
            "4. Report coverage before/after if a coverage tool is available\n\n"
            "Use the project's existing test framework and style."
        ),
        suggested_effort="ultracombo",
        tags=["testing", "coverage"],
    ),
    GoalTemplate(
        name="write-docs",
        description="Generate or update documentation",
        category="docs",
        prompt=(
            "Generate documentation for: {target}\n\n"
            "Produce:\n"
            "1. A README section explaining what it does and how to use it\n"
            "2. API documentation for all public functions/classes\n"
            "3. Usage examples\n"
            "4. Common pitfalls / FAQ\n\n"
            "Match the project's existing doc style."
        ),
        suggested_effort="ultramax",
        tags=["docs", "writing"],
    ),
    GoalTemplate(
        name="performance",
        description="Profile and optimise performance bottlenecks",
        category="performance",
        prompt=(
            "Optimise the performance of: {target}\n\n"
            "Steps:\n"
            "1. Profile to identify the bottleneck (use timing tools)\n"
            "2. Analyse the root cause\n"
            "3. Implement an optimisation\n"
            "4. Measure before/after\n"
            "5. Verify correctness is preserved\n\n"
            "Report: bottleneck, fix, before/after metrics."
        ),
        suggested_effort="ultracombo",
        tags=["performance", "optimisation"],
    ),
    GoalTemplate(
        name="migrate-deps",
        description="Safely migrate dependencies to new versions",
        category="devops",
        prompt=(
            "Migrate dependencies in {target} to newer versions.\n\n"
            "Steps:\n"
            "1. List current dependencies and their versions\n"
            "2. Check for available updates and breaking changes\n"
            "3. Update one dependency at a time\n"
            "4. Run tests after each update\n"
            "5. Fix any breakages\n"
            "6. Update lock files\n\n"
            "Never force an update that breaks tests."
        ),
        suggested_effort="ultracombo",
        tags=["devops", "migration"],
    ),
    GoalTemplate(
        name="scaffold-project",
        description="Scaffold a new project from scratch",
        category="coding",
        prompt=(
            "Scaffold a new {project_type} project named {name}.\n\n"
            "Include:\n"
            "- Project structure (src/tests/docs)\n"
            "- Build/config files (pyproject.toml / package.json / etc.)\n"
            "- A working 'hello world' entry point\n"
            "- A basic test that passes\n"
            "- A README with setup instructions\n"
            "- .gitignore appropriate for the language\n\n"
            "Make it production-ready, not a toy."
        ),
        suggested_effort="ultrahype",
        tags=["coding", "scaffold", "new-project"],
    ),
    GoalTemplate(
        name="explain-codebase",
        description="Produce a deep-dive architectural explanation of a codebase",
        category="analysis",
        prompt=(
            "Analyse the codebase at {path} and produce an architectural overview.\n\n"
            "Cover:\n"
            "- High-level architecture and components\n"
            "- Key abstractions and their responsibilities\n"
            "- Data flow through the system\n"
            "- External dependencies and integrations\n"
            "- Notable patterns and anti-patterns\n"
            "- Suggested improvements\n\n"
            "Make it understandable to a new team member."
        ),
        suggested_effort="ultracombo",
        tags=["analysis", "architecture"],
    ),
    GoalTemplate(
        name="debug-ci",
        description="Diagnose and fix a failing CI pipeline",
        category="devops",
        prompt=(
            "Diagnose and fix this failing CI pipeline: {pipeline}\n\n"
            "Error: {error}\n\n"
            "Steps:\n"
            "1. Read the CI config and the failing step\n"
            "2. Reproduce the failure locally\n"
            "3. Identify the root cause\n"
            "4. Implement the fix\n"
            "5. Verify locally that the fix works\n\n"
            "Report: root cause, fix, verification."
        ),
        suggested_effort="ultracombo",
        tags=["devops", "ci", "debug"],
    ),
]


def list_templates(category: str | None = None) -> list[GoalTemplate]:
    if category:
        return [t for t in TEMPLATES if t.category == category]
    return TEMPLATES


def get_template(name: str) -> GoalTemplate | None:
    for t in TEMPLATES:
        if t.name == name:
            return t
    return None


def categories() -> list[str]:
    return sorted({t.category for t in TEMPLATES})


def render(template: GoalTemplate, **kwargs: str) -> str:
    """Fill placeholders in a goal template."""
    result = template.prompt
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value)
    return result
