"""
Centralized logging for the fofproject monitor pipeline.

Usage:
    from fofproject.log import log, set_verbose

    log.info("Processing 3 emails", phase="CLASSIFY")
    log.detail("  GPT returned firm=Citadel", phase="CLASSIFY")
    log.error("Failed to parse PDF", phase="PERF")

The monitor configures verbosity at startup; sub-functions just call
log.info / log.detail / log.error with a phase tag.
"""

import logging
import sys
from datetime import datetime


_LOGGER_NAME = "fofproject"

# ── Phase constants ──────────────────────────────────────
CLASSIFY = "CLASSIFY"
PERF = "PERF"
GRAPHS = "GRAPHS"
METRICS = "METRICS"
RECONCILE = "RECONCILE"
SYNC = "SYNC"
MONITOR = "MONITOR"
NOTION = "NOTION"
EMAIL = "EMAIL"


class _PhaseFormatter(logging.Formatter):
    """Compact formatter: ``HH:MM:SS [PHASE     ] message``."""

    _SYMBOLS = {
        logging.DEBUG: "·",
        logging.INFO: " ",
        logging.WARNING: "!",
        logging.ERROR: "X",
    }

    def format(self, record):
        sym = self._SYMBOLS.get(record.levelno, " ")
        ts = datetime.now().strftime("%H:%M:%S")
        phase = getattr(record, "phase", "")
        phase_str = f"[{phase:<10s}]" if phase else " " * 12
        return f"  {ts} {sym} {phase_str} {record.getMessage()}"


def _ensure_handler():
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_PhaseFormatter())
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


# ── Public API ───────────────────────────────────────────

class log:
    """Thin namespace so callers write ``log.info(...)`` not ``get_logger().info(...)``."""

    @staticmethod
    def info(msg, phase=""):
        _ensure_handler().info(msg, extra={"phase": phase})

    @staticmethod
    def detail(msg, phase=""):
        """Visible only in verbose mode (DEBUG level)."""
        _ensure_handler().debug(msg, extra={"phase": phase})

    @staticmethod
    def warn(msg, phase=""):
        _ensure_handler().warning(msg, extra={"phase": phase})

    @staticmethod
    def error(msg, phase=""):
        _ensure_handler().error(msg, extra={"phase": phase})


def set_verbose(verbose: bool = True):
    """Toggle DEBUG-level output (shows sub-function internals)."""
    _ensure_handler().setLevel(logging.DEBUG if verbose else logging.INFO)
