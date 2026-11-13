"""Run every fullagent module's __main__ self-test in isolated subprocesses."""
import ast
import concurrent.futures
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent
skip = {"__init__", "__main__"}


def _has_main_block(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "__name__"
        and any(
            isinstance(value, ast.Constant) and value.value == "__main__"
            for value in node.comparators
        )
        for node in ast.walk(tree)
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the self-test's whole process group (it runs in its own
    session — never ours), falling back to the child alone."""
    try:
        if os.name == "posix" and os.getpgid(proc.pid) != os.getpgrp():
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _run_one(m: str) -> tuple[str, bool, str]:
    with tempfile.TemporaryDirectory(
        prefix=f"fullagent-selftest-{m}-"
    ) as data_home:
        env = os.environ.copy()
        env["FULLAGENT_HOME"] = data_home
        # stdin=DEVNULL: four self-tests run at once from one terminal; one
        # that reads stdin must get EOF, not steal the terminal and hang.
        # Own session: a timed-out self-test's servers/children are killed
        # with it instead of outliving the run (subprocess.run only killed
        # the direct child).
        proc = subprocess.Popen(
            [sys.executable, "-m", f"fullagent.{m}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PKG),
            env=env,
            start_new_session=(os.name == "posix"),
        )
        try:
            stdout, stderr = proc.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""
            partial = stdout or stderr or ""
            tail = partial.strip().splitlines()
            detail = tail[-1][:80] if tail else "no output before timeout"
            return m, False, f"timed out after 300s: {detail}"
        except BaseException:
            _kill_tree(proc)
            raise
        r = subprocess.CompletedProcess(proc.args, proc.returncode,
                                        stdout or "", stderr or "")

    out = (r.stdout + r.stderr).strip().splitlines()
    tail = out[-1] if out else "(no output)"
    marker = r.stdout + r.stderr
    ok = r.returncode == 0 and (
        "PASS" in marker or "passed" in marker.lower() or "OK:" in marker
    )
    detail = tail
    if not ok:
        detail = next(
            (line.strip() for line in reversed(out) if "FAIL" in line.upper()),
            tail,
        )
    return m, ok, detail[:110]


def main() -> int:
    t0 = time.monotonic()
    mods = sorted(
        path.stem
        for path in (PKG / "fullagent").glob("*.py")
        if path.stem not in skip and _has_main_block(path)
    )
    try:
        jobs = int(os.environ.get("FULLAGENT_SELFTEST_JOBS", "4"))
    except ValueError:  # a typo'd env var must not crash the runner
        jobs = 4
    workers = max(1, min(4, jobs))
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for m, ok, detail in pool.map(_run_one, mods):
            status = "PASS" if ok else "FAIL"
            print(f"{status:>4}  {m:<16} {detail}", flush=True)
            if not ok:
                failed.append(m)

    elapsed = time.monotonic() - t0
    print()
    if failed:
        print(f"{len(failed)} FAILED ({elapsed:.1f}s): {', '.join(failed)}")
        return 1
    print(f"ALL {len(mods)} MODULE SELF-TESTS PASS ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
