"""In-memory fund data store with reload capability."""

import os
import threading
from datetime import datetime

from fofproject.load import load_all_data

_funds = None
_loaded_at = None
_index_names = []
_rdgff_names = []
_overwrite_names: set[str] = set()
_json_names: set[str] = set()
_lock = threading.Lock()


def get_funds():
    """Return the current in-memory FundDict. Loads on first call."""
    global _funds, _loaded_at
    if _funds is None:
        reload_funds()
    return _funds


def reload_funds():
    """Reload all fund data from disk (CSV + JSON + manual overwrite)."""
    global _funds, _loaded_at, _index_names, _rdgff_names, _overwrite_names, _json_names
    with _lock:
        _funds = load_all_data()
        _loaded_at = datetime.now()
        _index_names, _rdgff_names = _categorize_funds()
        _overwrite_names = _read_overwrite_names()
        _json_names = _read_json_fund_names()
    return _funds


def _categorize_funds():
    """Determine which fund names are indices vs RETURN DATA.csv funds."""
    import pandas as pd
    from fofproject.fund import load_benchmarks
    from fofproject.paths import DEFAULT_INPUT_DIR

    base = str(DEFAULT_INPUT_DIR)
    index_names = []
    rdgff_names = []

    bm_path = os.path.join(base, "HF index comparison.xlsx")
    if os.path.exists(bm_path):
        bm = load_benchmarks(bm_path)
        index_names = sorted(bm.keys())

    return_path = os.path.join(base, "RETURN DATA.csv")
    if os.path.exists(return_path):
        df = pd.read_csv(return_path, nrows=0)
        bm_set = set(index_names)
        rdgff_names = [c for c in df.columns if c != "date" and c not in bm_set]

    return index_names, rdgff_names


def _read_overwrite_names() -> set[str]:
    """Return fund names present in any manual overwrite CSV."""
    import pandas as pd
    from fofproject.paths import DEFAULT_INPUT_DIR, MANUAL_OVERWRITE_PATH

    names: set[str] = set()
    paths = [
        os.path.join(str(DEFAULT_INPUT_DIR), "MANUAL OVERWRITE.csv"),
        str(MANUAL_OVERWRITE_PATH),
    ]
    for p in dict.fromkeys(paths):  # dedupe while preserving order
        if os.path.exists(p):
            try:
                df = pd.read_csv(p, nrows=0)
                df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
                names.update(c for c in df.columns if c != "date")
            except Exception:
                pass
    return names


def _read_json_fund_names() -> set[str]:
    """Return fund names loaded from JSON firm folders (lightweight scan)."""
    import json as _json
    from fofproject.paths import DEFAULT_OUTPUT_DIR

    names: set[str] = set()
    firms_path = str(DEFAULT_OUTPUT_DIR)
    if not os.path.isdir(firms_path):
        return names
    for firm_name in os.listdir(firms_path):
        firm_dir = os.path.join(firms_path, firm_name)
        if not os.path.isdir(firm_dir):
            continue
        for fund_folder in os.listdir(firm_dir):
            json_dir = os.path.join(firm_dir, fund_folder, "json")
            if not os.path.isdir(json_dir):
                continue
            for fname in os.listdir(json_dir):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(json_dir, fname), "r") as f:
                        data = _json.load(f)
                    fund_name = data.get("fund_name")
                    if fund_name:
                        names.add(fund_name)
                except Exception:
                    pass
    return names


def get_loaded_at():
    return _loaded_at


def get_index_names():
    return _index_names


def get_rdgff_names():
    return _rdgff_names


def get_fund_sources() -> dict[str, str]:
    """Return a mapping of fund_name -> composite source label."""
    funds = get_funds()
    bm_set = set(_index_names)
    csv_set = set(_rdgff_names)
    sources = {}
    for name in funds:
        parts = []
        if name in bm_set:
            parts.append("HF index comparison.xlsx")
        if name in csv_set:
            parts.append("RETURN DATA.csv")
        if name in _json_names:
            parts.append("JSON (Firm Folder)")
        if name in _overwrite_names:
            parts.append("Manual Overwrite")
        sources[name] = " + ".join(parts) if parts else "Unknown"
    return sources
