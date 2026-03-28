"""Manual overwrite CSV read/write endpoints."""

import os
import shutil
import tempfile

import pandas as pd
from fastapi import APIRouter, HTTPException

from fofproject.paths import DEFAULT_INPUT_DIR
from web.backend.state import reload_funds
from web.backend.schemas import OverwriteData

router = APIRouter(prefix="/api/overwrite", tags=["overwrite"])

MANUAL_CSV = "MANUAL OVERWRITE.csv"


def _csv_path() -> str:
    return os.path.join(str(DEFAULT_INPUT_DIR), MANUAL_CSV)


@router.get("", response_model=OverwriteData)
def read_overwrite():
    """Read MANUAL OVERWRITE.csv and return as structured data."""
    path = _csv_path()
    if not os.path.exists(path):
        return OverwriteData(headers=["date"], rows=[])
    df = pd.read_csv(path)
    headers = df.columns.tolist()
    rows = df.values.tolist()
    # Replace NaN with None for JSON serialization
    rows = [
        [None if pd.isna(v) else v for v in row]
        for row in rows
    ]
    return OverwriteData(headers=headers, rows=rows)


@router.put("")
def write_overwrite(data: OverwriteData):
    """Write data back to MANUAL OVERWRITE.csv with backup, then reload funds."""
    path = _csv_path()

    # Validate: must have a date column
    if not data.headers or data.headers[0].lower() != "date":
        raise HTTPException(
            status_code=400,
            detail="First column must be 'date'",
        )

    # Create backup if file exists
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")

    # Build DataFrame and write atomically
    df = pd.DataFrame(data.rows, columns=data.headers)

    # Write to temp file then replace
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".csv")
    try:
        os.close(fd)
        df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # Reload funds with new data
    reload_funds()

    return {"success": True, "message": f"Saved {len(data.rows)} rows, funds reloaded."}
