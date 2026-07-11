"""Structured, colorised logging with a rotating file handler.

The logger writes human-readable colored output to stderr (optional) and a
rotating log file under ~/.terminal_agent/logs. It is safe to call
``get_logger`` many times; handlers are only attached once.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_CONFIGURED: set[str] = set()

LOG_DIR = Path.home() / ".terminal_agent" / "logs"
LOG_FILE = LOG_DIR / "agent.log"

_LEVEL_COLORS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;196m",
    "CRITICAL": "\033[48;5;196m\033[38;5;231m",
}
_RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelname, "")
        record.levelname_colored = f"{color}{record.levelname:<8}{_RESET}"
        return super().format(record)


def get_logger(name: str = "agent", level: int = logging.INFO, console: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    if name in _CONFIGURED:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(level)
        stream.setFormatter(ColorFormatter("%(levelname_colored)s %(message)s"))
        logger.addHandler(stream)

    _CONFIGURED.add(name)
    return logger
