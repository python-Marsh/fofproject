"""In-memory fund data store with reload capability."""

import os
import threading
from datetime import datetime

from fofproject.load import load_all_data

_funds = None
_loaded_at = None
_index_names = []
_rdgff_names = []
_lock = threading.Lock()


def get_funds():
    """Return the current in-memory FundDict. Loads on first call."""
    global _funds, _loaded_at
    if _funds is None:
        reload_funds()
    return _funds


def reload_funds():
    """Reload all fund data from disk (CSV + JSON + manual overwrite)."""
    global _funds, _loaded_at, _index_names, _rdgff_names
    with _lock:
        _funds = load_all_data()
        _loaded_at = datetime.now()
        _index_names, _rdgff_names = _categorize_funds()
    return _funds


def _categorize_funds():
    """Determine which fund names are indices (BENCHMARK.csv) vs RETURN DATA.csv funds."""
    import pandas as pd
    from fofproject.fund import load_benchmarks
    from fofproject.paths import DEFAULT_INPUT_DIR

    base = str(DEFAULT_INPUT_DIR)
    index_names = []
    rdgff_names = []

    bm_path = os.path.join(base, "BENCHMARK.csv")
    if os.path.exists(bm_path):
        bm = load_benchmarks(bm_path)
        index_names = sorted(bm.keys())

    return_path = os.path.join(base, "RETURN DATA.csv")
    if os.path.exists(return_path):
        df = pd.read_csv(return_path, nrows=0)
        bm_set = set(index_names)
        rdgff_names = [c for c in df.columns if c != "date" and c not in bm_set]

    return index_names, rdgff_names


def get_loaded_at():
    return _loaded_at


def get_index_names():
    return _index_names


def get_rdgff_names():
    return _rdgff_names
