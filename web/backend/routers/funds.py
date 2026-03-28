"""Fund list, detail, and metrics endpoints."""

import math

from fastapi import APIRouter, HTTPException, Query

from fofproject.fund import compare_funds
from web.backend.state import get_funds
from web.backend.schemas import FundDetail, MonthlyReturn, MetricsResponse

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
    df = compare_funds(funds, benchmark_fund=bm)
    # Convert datetime columns to strings for JSON
    for col in ["Inception Date", "Latest Date"]:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: x.strftime("%Y-%m") if hasattr(x, "strftime") else x
            )
    records = df.to_dict(orient="records")
    # Clean NaN values
    for row in records:
        for k, v in row.items():
            row[k] = _clean_value(v)
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

    return result
