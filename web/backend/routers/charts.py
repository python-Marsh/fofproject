"""Chart endpoints returning Plotly JSON for client-side rendering."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from fofproject.fund import subset_of_funds
from fofproject.batch import plot_cumulative_returns, plot_fund_correlation_heatmap
from web.backend.state import get_funds
from web.backend.utils import suppress_show, plotly_fig_to_json
from web.backend.schemas import (
    CumulativeReturnsRequest,
    CorrelationRequest,
    DistributionRequest,
    RollingVolRequest,
    WorstPerformanceRequest,
    PlotlyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/charts", tags=["charts"])


def _get_fund_or_404(name: str):
    funds = get_funds()
    fund = funds.get(name)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"Fund '{name}' not found")
    return fund


def _fund_date_ranges(selected: dict) -> str:
    """Build a summary of each fund's date range."""
    ranges = []
    for name, f in selected.items():
        if f.monthly_returns:
            first = f.monthly_returns[0]["month"]
            last = f.monthly_returns[-1]["month"]
            ranges.append(f"  {name}: {first:%Y-%m} to {last:%Y-%m}")
        else:
            ranges.append(f"  {name}: no data")
    return "\n".join(ranges)


@router.post("/cumulative-returns", response_model=PlotlyResponse)
async def cumulative_returns(req: CumulativeReturnsRequest):
    def _generate():
        funds = get_funds()
        selected = subset_of_funds(funds, req.fund_names)
        try:
            with suppress_show():
                fig = plot_cumulative_returns(
                    funds=selected,
                    title=req.title,
                    start_month=req.start_month,
                    end_month=req.end_month,
                    style=req.style,
                    language=req.language,
                    highlight_extremes=req.highlight_extremes or False,
                    strict_period=req.strict_period,
                    save=False,
                    aspect_lock=True,
                )
        except ValueError as e:
            msg = str(e)
            detail = f"{msg}\n\nFund date ranges:\n{_fund_date_ranges(selected)}"
            if req.strict_period:
                detail += (
                    "\n\nStrict Period is enabled — all funds must have data "
                    "for the entire selected period. Disable Strict Period or "
                    "adjust the date range."
                )
            raise HTTPException(status_code=400, detail=detail)
        except Exception as e:
            logger.exception("cumulative-returns failed")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate cumulative returns chart: {e}",
            )
        return plotly_fig_to_json(fig)

    try:
        result = await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("cumulative-returns failed")
        raise HTTPException(status_code=400, detail=f"Chart generation failed: {e}")
    return PlotlyResponse(plotly_json=result)


@router.post("/correlation-heatmap")
async def correlation_heatmap(req: CorrelationRequest):
    def _generate():
        funds = get_funds()
        selected = subset_of_funds(funds, req.fund_names)
        try:
            with suppress_show():
                fig, corr_df, overlap_df = plot_fund_correlation_heatmap(
                    selected,
                    method=req.method,
                    min_overlap=req.min_overlap,
                    title=req.title,
                    save=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate correlation heatmap: {e}",
            )
        import numpy as np

        def clean_dict(d):
            """Replace NaN/Inf with None recursively in nested dicts."""
            return {
                k: {k2: (None if isinstance(v2, float) and (np.isnan(v2) or np.isinf(v2)) else v2)
                     for k2, v2 in v.items()} if isinstance(v, dict) else v
                for k, v in d.items()
            }

        return {
            "plotly_json": plotly_fig_to_json(fig),
            "correlation_matrix": clean_dict(corr_df.to_dict()),
            "overlap_matrix": clean_dict(overlap_df.to_dict()),
        }

    try:
        return await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("correlation-heatmap failed")
        raise HTTPException(status_code=400, detail=f"Correlation heatmap failed: {e}")


@router.post("/return-distribution", response_model=PlotlyResponse)
async def return_distribution(req: DistributionRequest):
    def _generate():
        fund = _get_fund_or_404(req.fund_name)
        try:
            with suppress_show():
                fig = fund.plot_monthly_return_distribution(
                    start_month=req.start_month,
                    end_month=req.end_month,
                    bins=req.bins,
                    save=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate return distribution for '{req.fund_name}': {e}",
            )
        return plotly_fig_to_json(fig)

    try:
        result = await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("return-distribution failed")
        raise HTTPException(status_code=400, detail=f"Return distribution failed: {e}")
    return PlotlyResponse(plotly_json=result)


@router.post("/rolling-volatility", response_model=PlotlyResponse)
async def rolling_volatility(req: RollingVolRequest):
    def _generate():
        fund = _get_fund_or_404(req.fund_name)
        funds = get_funds()
        bm = funds.get(req.benchmark) if req.benchmark else fund.default_benchmark
        try:
            with suppress_show():
                fig = fund.plot_rolling_vol_vs_benchmark(
                    benchmark_fund=bm,
                    window=req.window,
                    save=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate rolling volatility for '{req.fund_name}': {e}",
            )
        return plotly_fig_to_json(fig)

    try:
        result = await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rolling-volatility failed")
        raise HTTPException(status_code=400, detail=f"Rolling volatility failed: {e}")
    return PlotlyResponse(plotly_json=result)


@router.post("/worst-performance", response_model=PlotlyResponse)
async def worst_performance(req: WorstPerformanceRequest):
    def _generate():
        fund = _get_fund_or_404(req.fund_name)
        funds = get_funds()
        bm = funds.get(req.benchmark)
        if bm is None:
            raise HTTPException(status_code=404, detail=f"Benchmark '{req.benchmark}' not found")
        try:
            with suppress_show():
                fig = fund.compare_worst_performance(
                    benchmark_fund=bm,
                    n_worst=req.n_worst,
                    save=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate worst performance for '{req.fund_name}': {e}",
            )
        return plotly_fig_to_json(fig)

    try:
        result = await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("worst-performance failed")
        raise HTTPException(status_code=400, detail=f"Worst performance chart failed: {e}")
    return PlotlyResponse(plotly_json=result)
