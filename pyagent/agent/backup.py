"""Automatic file backup and simple version history."""
from __future__ import annotations

import shutil
import time
from pathlib import Path


class BackupManager:
    """Keeps timestamped backups of files under a backup directory."""

    def __init__(self, backup_dir: str | Path = ".backups") -> None:
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def backup(self, path: str | Path) -> str:
        src = Path(path)
        if not src.is_file():
            return f"Error: not a file: {path}"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.backup_dir / f"{src.name}.{stamp}.bak"
        shutil.copy2(src, dest)
        return str(dest)

    def versions(self, filename: str) -> list[str]:
        return sorted(str(p) for p in self.backup_dir.glob(f"{filename}.*.bak"))

    def restore(self, backup_path: str, target: str) -> str:
        bp = Path(backup_path)
        if not bp.is_file():
            return f"Error: backup not found: {backup_path}"
        shutil.copy2(bp, target)
        return f"Restored {target} from {backup_path}"
