"""
Centralized path configuration for the fofproject package.

Routing logic (per directory):
    1. Environment variable override (if set)
    2. Synology NAS mounted path (if exists)
    3. Local project fallback
"""

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── NAS mount points (Synology Docker) ───────────────────
_NAS_EMAIL_STORAGE = Path("/data/RDGFF Emails")
_NAS_OUTPUT = Path("/data/output")
_NAS_FUND_FIRM = Path("/data/Hedge Funds")
_NAS_INPUT = Path("/data/Input")


def _resolve(env_var: str, nas_path: Path, local_path: Path) -> Path:
    """Pick the first available path: env override → NAS mount → local."""
    override = os.getenv(env_var)
    if override:
        return Path(override)
    if nas_path.exists():
        return nas_path
    return local_path


# ── Directory constants ──────────────────────────────────

# Where connection.py downloads raw emails from Outlook
EMAIL_STORAGE_DIR = _resolve(
    "EMAIL_STORAGE_DIR",
    _NAS_EMAIL_STORAGE,
    _PROJECT_ROOT / "testing" / "emails",
)

# Where classify.py reads emails from
DEFAULT_EMAIL_INPUT_DIR = _resolve(
    "EMAIL_INPUT_DIR",
    _NAS_EMAIL_STORAGE,
    _PROJECT_ROOT / "testing" / "email",
)

# Where classify.py writes the firm/fund folder structure
# Also where notion.py watches and performance.py reads
DEFAULT_OUTPUT_DIR = _resolve(
    "OUTPUT_DIR",
    _NAS_FUND_FIRM,
    _PROJECT_ROOT /"testing" / "hedge funds",
)

# Notion watches the same directory as classify output
DEFAULT_WATCH_FOLDER = DEFAULT_OUTPUT_DIR

# General output directory for charts/tables (fund.py)
SAVE_DIR = _resolve(
    "SAVE_DIR",
    _NAS_OUTPUT,
    _PROJECT_ROOT / "output",
)
SAVE_DIR.mkdir(parents=True, exist_ok=True)

# Where load_all_data reads CSVs and JSON subfolders from
DEFAULT_INPUT_DIR = _resolve(
    "INPUT_DIR",
    _NAS_INPUT,
    _PROJECT_ROOT / "input",
)

# Manual overwrite CSV — lives alongside other input data
MANUAL_OVERWRITE_PATH = DEFAULT_INPUT_DIR / "MANUAL OVERWRITE.csv"

# Document templates directory (DOCX/PPTX templates and JSON specs)
TEMPLATE_DIR = _resolve(
    "TEMPLATE_DIR",
    _NAS_INPUT / "templates",
    _PROJECT_ROOT / "tests" / "fixtures" / "documents" / "template_input",
)
