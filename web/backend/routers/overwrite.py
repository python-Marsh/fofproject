"""Manual overwrite CSV read/write endpoints (per-fund)."""

import os

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fofproject.paths import MANUAL_OVERWRITE_PATH
from web.backend.state import reload_funds

router = APIRouter(prefix="/api/overwrite", tags=["overwrite"])


class FundOverwriteData(BaseModel):
    """Overwrite data for a single fund: list of {date, value} entries."""
    entries: list[dict]  # [{"date": "YYYY-MM" or "DD/MM/YYYY", "value": 0.05 | null}, ...]


def _csv_path() -> str:
    return str(MANUAL_OVERWRITE_PATH)


def _read_df() -> pd.DataFrame:
    """Read the overwrite CSV, keeping date as string."""
    path = _csv_path()
    if os.path.exists(path):
        return pd.read_csv(path, dtype={"date": str})
    return pd.DataFrame(columns=["date"])


def _write_df(df: pd.DataFrame) -> None:
    """Write DataFrame to CSV."""
    path = _csv_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)


@router.get("/{identifier}")
def read_fund_overwrite(identifier: str):
    """Read overwrite entries for a single fund by identifier."""
    try:
        df = _read_df()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read overwrite CSV: {e}",
        )
    if identifier not in df.columns:
        return FundOverwriteData(entries=[])

    entries = []
    for _, row in df.iterrows():
        val = row[identifier]
        if pd.isna(val):
            continue
        # Convert DD/MM/YYYY to YYYY-MM for frontend month picker
        date_str = str(row["date"]).strip()
        try:
            from datetime import datetime as _dt
            parsed = _dt.strptime(date_str, "%d/%m/%Y")
            date_str = parsed.strftime("%Y-%m")
        except ValueError:
            pass
        entries.append({"date": date_str, "value": val})
    return FundOverwriteData(entries=entries)


def _normalize_date(date_str: str) -> str:
    """Convert YYYY-MM to DD/MM/YYYY (month-end), or return as-is if already DD/MM/YYYY."""
    import calendar

    date_str = str(date_str).strip()
    # YYYY-MM format → last day of month / MM / YYYY
    if len(date_str) == 7 and date_str[4] == "-":
        year, month = int(date_str[:4]), int(date_str[5:])
        last_day = calendar.monthrange(year, month)[1]
        return f"{last_day:02d}/{month:02d}/{year}"
    return date_str


@router.put("/{identifier}")
def write_fund_overwrite(identifier: str, data: FundOverwriteData):
    """Write overwrite entries for a single fund by identifier, merging into the CSV."""
    df = _read_df()

    # Add the fund column if it doesn't exist (column name = identifier)
    if identifier not in df.columns:
        df[identifier] = None

    # Normalize all incoming dates to DD/MM/YYYY (month-end)
    incoming = {_normalize_date(e["date"]): e["value"] for e in data.entries}

    # Normalize existing dates in the DataFrame for consistent comparison
    df["date"] = df["date"].astype(str).str.strip()

    # Ensure all incoming dates exist as rows in the DataFrame
    existing_dates = set(df["date"].tolist())
    new_rows = []
    for date_str in incoming:
        if date_str not in existing_dates:
            new_row = {col: None for col in df.columns}
            new_row["date"] = date_str
            new_rows.append(new_row)
    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    # Update values for this fund column:
    # - Set value for dates in incoming
    # - Clear value for dates NOT in incoming (handles deletions from UI)
    for idx, row in df.iterrows():
        date_str = str(row["date"]).strip()
        if date_str in incoming:
            df.at[idx, identifier] = incoming[date_str]
        else:
            df.at[idx, identifier] = None

    # Drop fund columns that are entirely empty (no overwrite data left)
    fund_cols = [c for c in df.columns if c != "date"]
    for col in fund_cols:
        if df[col].isna().all():
            df = df.drop(columns=[col])

    # Drop rows where all remaining fund columns are empty
    fund_cols = [c for c in df.columns if c != "date"]
    if fund_cols:
        df = df.dropna(subset=fund_cols, how="all").reset_index(drop=True)

    # Sort by date
    df["_sort"] = pd.to_datetime(df["date"], format="%d/%m/%Y", errors="coerce")
    df = df.sort_values("_sort").drop(columns=["_sort"]).reset_index(drop=True)

    try:
        _write_df(df)
        reload_funds()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to save overwrite data for '{identifier}': {e}",
        )

    return {
        "success": True,
        "message": f"Saved {len(incoming)} overwrite entries for {identifier}, funds reloaded.",
    }
