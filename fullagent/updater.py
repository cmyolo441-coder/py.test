"""Self-update: ``fullagent update``.

Two install shapes are supported:

- Source install (the pip wrapper ``install.sh`` writes): the running
  interpreter IS the venv python, so ``sys.executable -m pip install
  --upgrade git+<repo>`` refreshes the code in place.
- Frozen binary (PyInstaller release asset): download the latest
  platform asset from GitHub Releases and atomically replace
  ``sys.executable``.

``fullagent update --check`` only reports whether a newer release
exists.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.request

REPO = "cmyolo441-coder/py.test"
_API = f"https://api.github.com/repos/{REPO}"
_GIT_URL = f"https://github.com/{REPO}.git"
_UA = {"User-Agent": "fullagent-updater",
       "Accept": "application/vnd.github+json"}


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.strip().lstrip("v").split(".")
                 if x.isdigit())


def _latest_release() -> dict:
    req = urllib.request.Request(f"{_API}/releases/latest", headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _main_version() -> str | None:
    """__version__ on the main branch — what a source install would get.

    Source installs track main, NOT GitHub releases: comparing against
    releases/latest is wrong whenever a release was deleted or main is
    ahead of the last tag.
    """
    url = (f"https://raw.githubusercontent.com/{REPO}/main/"
           f"fullagent/__init__.py")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _binary_asset() -> str | None:
    if sys.platform.startswith("linux"):
        return "fullagent-linux-x64"
    if sys.platform == "darwin":
        return ("fullagent-darwin-arm64"
                if platform.machine().lower() in ("arm64", "aarch64")
                else None)
    if sys.platform == "win32":
        return "fullagent-windows-x64.exe"
    return None


def _update_source() -> int:
    print(">> Source install — upgrading via pip "
          f"(git+{REPO})...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade",
         f"git+{_GIT_URL}"],
        check=False)
    if r.returncode != 0:
        print("!! pip upgrade failed", file=sys.stderr)
        return r.returncode or 1
    print(">> Done — run `fullagent` again for the new version.")
    return 0


def _update_binary(asset: str, tag: str) -> int:
    url = (f"https://github.com/{REPO}/releases/download/"
           f"{tag}/{asset}")
    target = os.path.realpath(sys.executable)
    tmp = tempfile.NamedTemporaryFile(
        prefix="fullagent-", dir=os.path.dirname(target) or ".",
        delete=False)
    tmp.close()
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=60) as r, \
                open(tmp.name, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        if sys.platform != "win32":
            os.chmod(tmp.name, 0o755)
        os.replace(tmp.name, target)
    except Exception as e:  # noqa: BLE001 — any failure leaves old binary
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        print(f"!! update failed: {e}", file=sys.stderr)
        return 1
    print(f">> Updated binary to {tag} — restart `fullagent` to use it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    check_only = "--check" in argv
    from . import __version__
    cur = __version__.lstrip("v")

    if getattr(sys, "frozen", False):
        # Binary installs track GitHub Releases.
        try:
            tag = str(_latest_release().get("tag_name") or "")
        except Exception as e:  # noqa: BLE001 — network/DNS/offline
            print(f"update: could not reach GitHub releases ({e})",
                  file=sys.stderr)
            return 1
        latest, channel = tag.lstrip("v"), "release"
        newer = bool(tag) and _ver_tuple(tag) > _ver_tuple(cur)
        if newer:
            asset = _binary_asset()
            if asset is None:
                print(f"update: no binary asset for this platform "
                      f"({sys.platform}/{platform.machine()})",
                      file=sys.stderr)
                return 1
            if check_only:
                print(f"installed: v{cur}   latest: {tag}  (update available)")
                return 0
            return _update_binary(asset, tag)
    else:
        # Source installs track the main branch.
        try:
            latest = _main_version()
        except Exception as e:  # noqa: BLE001
            print(f"update: could not reach GitHub ({e})", file=sys.stderr)
            return 1
        channel = "main"
        # pip decides what actually changes; version equality only skips
        # the (slow) reinstall when main is not ahead
        newer = bool(latest) and _ver_tuple(latest) > _ver_tuple(cur)

    if check_only:
        state = " (update available)" if newer else " (up to date)"
        print(f"installed: v{cur}   latest {channel}: {latest or '?'}{state}")
        return 0
    if not newer:
        print(f"fullagent is up to date (v{cur}, latest {channel} "
              f"{latest or '?'}).")
        return 0
    return _update_source()
