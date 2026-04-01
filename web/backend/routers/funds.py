"""Fund list, detail, and metrics endpoints."""

import json as _json
import logging
import math
import os

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from fofproject.fund import compare_funds, INDEX_NAME_MAPPING_PATH, _load_index_name_mapping
from fofproject.paths import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, MANUAL_OVERWRITE_PATH
from web.backend.state import get_funds, get_fund_sources, get_index_names, reload_funds
from web.backend.schemas import FundDetail, MonthlyReturn, MetricsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/funds", tags=["funds"])


def _clean_value(v):
    """Replace NaN/Inf with None for JSON serialization."""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


@router.get("")
def list_funds(benchmark: str | None = Query(None)):
    """Return comparison table from compare_funds() as list of row dicts."""
    funds = get_funds()
    bm = funds.get(benchmark) if benchmark else None
    try:
        df = compare_funds(funds, benchmark_fund=bm)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to generate fund comparison table: {e}",
        )
    # Convert datetime columns to strings for JSON
    for col in ["Inception Date", "Latest Date"]:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: x.strftime("%Y-%m") if hasattr(x, "strftime") else x
            )
    records = df.to_dict(orient="records")
    # Clean NaN values and add source
    sources = get_fund_sources()
    for row in records:
        for k, v in row.items():
            row[k] = _clean_value(v)
        row["Source"] = sources.get(row.get("Name", ""), "Unknown")
    return {"funds": records}


@router.get("/{name}", response_model=FundDetail)
def get_fund(name: str):
    """Return full detail for a single fund."""
    funds = get_funds()
    fund = funds.get(name)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"Fund '{name}' not found")
    return FundDetail(
        name=fund.name,
        identifier=fund.identifier or fund.name,
        one_liner=fund.one_liner,
        geo_focus=fund.geo_focus,
        strategy=fund.strategy if isinstance(fund.strategy, list) else (
            [fund.strategy] if fund.strategy else None
        ),
        asset_class=fund.asset_class if isinstance(fund.asset_class, list) else (
            [fund.asset_class] if fund.asset_class else None
        ),
        inception_date=fund.inception_date.strftime("%Y-%m") if fund.inception_date else None,
        latest_date=fund.latest_date.strftime("%Y-%m") if fund.latest_date else None,
        num_months=fund.num_months,
        aum_size=_clean_value(fund.aum_size),
        management_fee=_clean_value(fund.management_fee),
        performance_fee=_clean_value(fund.performance_fee),
        net_exposure_info=fund.net_exposure_info,
        total_cum_rtn=_clean_value(fund.total_cum_rtn),
        total_ann_rtn=_clean_value(fund.total_ann_rtn),
        total_vol=_clean_value(fund.total_vol),
        total_sharpe=_clean_value(fund.total_sharpe),
        total_sortino=_clean_value(fund.total_sortino),
        total_max_dd=_clean_value(fund.total_max_dd),
        total_pos_months=_clean_value(fund.total_pos_months),
        monthly_returns=[
            MonthlyReturn(
                month=e["month"].strftime("%Y-%m"),
                value=_clean_value(e.get("value")),
            )
            for e in fund.monthly_returns
        ],
    )


@router.get("/{name}/metrics", response_model=MetricsResponse)
def get_metrics(
    name: str,
    start_month: str | None = Query(None),
    end_month: str | None = Query(None),
    benchmark: str | None = Query(None),
):
    """Compute metrics for a fund over a date range."""
    funds = get_funds()
    fund = funds.get(name)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"Fund '{name}' not found")

    bm = funds.get(benchmark) if benchmark else fund.default_benchmark

    try:
        result = MetricsResponse(
            cumulative_return=_clean_value(fund.cumulative_return(start_month, end_month)),
            annualized_return=_clean_value(fund.annualized_return(start_month, end_month)),
            volatility=_clean_value(fund.volatility(start_month, end_month)),
            sharpe_ratio=_clean_value(fund.sharpe_ratio(start_month, end_month)),
            sortino_ratio=_clean_value(fund.sortino_ratio(start_month, end_month)),
            max_drawdown=_clean_value(fund.max_drawdown(start_month, end_month)),
            positive_months=_clean_value(fund.positive_months(start_month, end_month)),
        )

        if bm:
            result.beta = _clean_value(fund.beta_to(bm, start_month, end_month))
            result.correlation = _clean_value(fund.correlation_to(bm, start_month, end_month))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to compute metrics for '{name}': {e}",
        )

    return result


class RenameRequest(BaseModel):
    new_name: str


@router.put("/{identifier}/rename")
def rename_fund(identifier: str, req: RenameRequest):
    """Rename a fund by identifier.

    Persists the rename via:
    - identifier_to_name mapping in index_name_mapping.json (always)
    - JSON source files where fund_name matches (if any)
    - ticker_to_name mapping for index funds
    - name_to_legend mapping (if entry exists)
    CSV columns are never renamed — the identifier_to_name override
    takes effect after reload.
    """
    funds = get_funds()
    fund = funds.get(identifier)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"Fund '{identifier}' not found")

    old_name = fund.name
    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name cannot be empty")
    if old_name == new_name:
        return {"success": True, "message": "Name unchanged.", "updated_sources": []}

    updated = []

    # 1. Always persist identifier_to_name override
    try:
        mapping = _load_index_name_mapping()
        id_map = mapping.setdefault("identifier_to_name", {})
        id_map[identifier] = new_name
        # If the override now matches what the source would produce,
        # remove it to keep the mapping clean
        # (handled implicitly — load_all_data applies overrides last)

        # 2. For index funds: also update ticker_to_name
        is_index = old_name in get_index_names()
        if is_index:
            ticker_map = mapping.get("ticker_to_name", {})
            for ticker, display in list(ticker_map.items()):
                if display == old_name:
                    ticker_map[ticker] = new_name
            mapping["ticker_to_name"] = ticker_map

        # 3. Update name_to_legend if entry exists
        legend_map = mapping.get("name_to_legend", {})
        if old_name in legend_map:
            legend_map[new_name] = legend_map.pop(old_name)
            mapping["name_to_legend"] = legend_map

        with open(str(INDEX_NAME_MAPPING_PATH), "w", encoding="utf-8") as f:
            _json.dump(mapping, f, indent=2, ensure_ascii=False)
        updated.append("index_name_mapping.json")
    except Exception as e:
        logger.warning("Failed to update name mapping: %s", e)

    # 4. Rename fund_name in JSON source files (firm folders)
    for search_root in [DEFAULT_OUTPUT_DIR, DEFAULT_INPUT_DIR]:
        search_dir = str(search_root)
        if not os.path.isdir(search_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(search_dir):
            if not os.path.basename(dirpath) == "json":
                continue
            for fname in filenames:
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, "r") as f:
                        data = _json.load(f)
                    if data.get("fund_name") == old_name:
                        data["fund_name"] = new_name
                        with open(fpath, "w") as f:
                            _json.dump(data, f, indent=2, ensure_ascii=False)
                        updated.append(f"JSON: {os.path.relpath(fpath, search_dir)}")
                except Exception as e:
                    logger.warning("Failed to rename in %s: %s", fpath, e)

    reload_funds()
    return {
        "success": True,
        "message": f"Renamed '{old_name}' to '{new_name}'.",
        "updated_sources": updated,
    }
