"""Git workflow automation — branch, commit, push, PR creation.

Real, working git operations that automate the common dev loop. No GitHub
API token needed for local operations; PR creation uses ``gh`` CLI if
available, otherwise prints instructions.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from .logging_config import get_logger

log = get_logger("agent.git_workflow")


def _run_git(args: list[str], cwd: str = ".", timeout: int = 30) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        out = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, out
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


@dataclass
class GitWorkflowResult:
    success: bool
    message: str
    details: dict[str, Any] = None


def create_branch(name: str, base: str = "HEAD", cwd: str = ".") -> GitWorkflowResult:
    ok, out = _run_git(["checkout", "-b", name, base], cwd=cwd)
    if ok:
        return GitWorkflowResult(True, f"Created and switched to branch '{name}'", {"branch": name})
    return GitWorkflowResult(False, f"Failed to create branch: {out}")


def commit_all(message: str, cwd: str = ".") -> GitWorkflowResult:
    """Stage all changes and commit."""
    ok, out = _run_git(["add", "-A"], cwd=cwd)
    if not ok:
        return GitWorkflowResult(False, f"git add failed: {out}")
    ok, out = _run_git(["commit", "-m", message], cwd=cwd)
    if ok:
        # Get the commit hash.
        ok2, hash_out = _run_git(["rev-parse", "HEAD"], cwd=cwd)
        commit_hash = hash_out.strip() if ok2 else "unknown"
        return GitWorkflowResult(True, f"Committed: {message}", {"hash": commit_hash[:8]})
    return GitWorkflowResult(False, f"git commit failed: {out}")


def push(branch: str | None = None, remote: str = "origin", cwd: str = ".") -> GitWorkflowResult:
    if branch is None:
        ok, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if not ok:
            return GitWorkflowResult(False, f"Could not determine current branch: {out}")
        branch = out.strip()
    ok, out = _run_git(["push", "-u", remote, branch], cwd=cwd, timeout=700000)
    if ok:
        return GitWorkflowResult(True, f"Pushed {branch} to {remote}", {"branch": branch, "remote": remote})
    return GitWorkflowResult(False, f"Push failed: {out}")


def create_pr(title: str, body: str = "", base: str = "main", cwd: str = ".") -> GitWorkflowResult:
    """Create a PR using the ``gh`` CLI if available."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--base", base],
            capture_output=True, text=True, timeout=700000, cwd=cwd,
        )
        if proc.returncode == 0:
            url = proc.stdout.strip()
            return GitWorkflowResult(True, f"PR created: {url}", {"url": url})
        return GitWorkflowResult(False, f"gh pr create failed: {proc.stderr.strip()}")
    except FileNotFoundError:
        return GitWorkflowResult(
            False,
            "GitHub CLI (gh) not installed. Install it from https://cli.github.com/, "
            "or create the PR manually from your repo's web UI.",
        )
    except subprocess.TimeoutExpired:
        return GitWorkflowResult(False, "gh pr create timed out")


def full_workflow(branch_name: str, commit_message: str, pr_title: str = "", pr_body: str = "", base: str = "main", cwd: str = ".") -> GitWorkflowResult:
    """Branch -> commit -> push -> PR in one call."""
    steps = []
    # 1. Branch
    r = create_branch(branch_name, cwd=cwd)
    steps.append(("branch", r))
    if not r.success:
        return _aggregate("Git workflow failed at branch step", steps)
    # 2. Commit
    r = commit_all(commit_message, cwd=cwd)
    steps.append(("commit", r))
    if not r.success:
        return _aggregate("Git workflow failed at commit step", steps)
    # 3. Push
    r = push(branch_name, cwd=cwd)
    steps.append(("push", r))
    if not r.success:
        return _aggregate("Git workflow failed at push step", steps)
    # 4. PR (optional)
    if pr_title:
        r = create_pr(pr_title or commit_message, pr_body, base=base, cwd=cwd)
        steps.append(("pr", r))
    return _aggregate("Git workflow complete", steps)


def _aggregate(message: str, steps: list[tuple[str, GitWorkflowResult]]) -> GitWorkflowResult:
    all_success = all(r.success for _, r in steps)
    details = {name: {"success": r.success, "message": r.message} for name, r in steps}
    return GitWorkflowResult(all_success, message, details)


def git_status_short(cwd: str = ".") -> str:
    ok, out = _run_git(["status", "--short", "--branch"], cwd=cwd)
    return out if ok else "(git status failed)"


def recent_commits(limit: int = 10, cwd: str = ".") -> str:
    ok, out = _run_git(["log", f"-{limit}", "--oneline", "--graph", "--decorate"], cwd=cwd)
    return out if ok else "(git log failed)"
