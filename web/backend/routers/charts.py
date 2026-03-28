"""Chart endpoints returning Plotly JSON for client-side rendering."""

import asyncio

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

router = APIRouter(prefix="/api/charts", tags=["charts"])


def _get_fund_or_404(name: str):
    funds = get_funds()
    fund = funds.get(name)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"Fund '{name}' not found")
    return fund


@router.post("/cumulative-returns", response_model=PlotlyResponse)
async def cumulative_returns(req: CumulativeReturnsRequest):
    def _generate():
        funds = get_funds()
        selected = subset_of_funds(funds, req.fund_names)
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
        return plotly_fig_to_json(fig)

    result = await asyncio.to_thread(_generate)
    return PlotlyResponse(plotly_json=result)


@router.post("/correlation-heatmap")
async def correlation_heatmap(req: CorrelationRequest):
    def _generate():
        funds = get_funds()
        selected = subset_of_funds(funds, req.fund_names)
        with suppress_show():
            fig, corr_df, overlap_df = plot_fund_correlation_heatmap(
                selected,
                method=req.method,
                min_overlap=req.min_overlap,
                title=req.title,
                save=False,
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

    return await asyncio.to_thread(_generate)


@router.post("/return-distribution", response_model=PlotlyResponse)
async def return_distribution(req: DistributionRequest):
    def _generate():
        fund = _get_fund_or_404(req.fund_name)
        with suppress_show():
            fig = fund.plot_monthly_return_distribution(
                start_month=req.start_month,
                end_month=req.end_month,
                bins=req.bins,
                save=False,
            )
        return plotly_fig_to_json(fig)

    result = await asyncio.to_thread(_generate)
    return PlotlyResponse(plotly_json=result)


@router.post("/rolling-volatility", response_model=PlotlyResponse)
async def rolling_volatility(req: RollingVolRequest):
    def _generate():
        fund = _get_fund_or_404(req.fund_name)
        funds = get_funds()
        bm = funds.get(req.benchmark) if req.benchmark else fund.default_benchmark
        with suppress_show():
            fig = fund.plot_rolling_vol_vs_benchmark(
                benchmark_fund=bm,
                window=req.window,
                save=False,
            )
        return plotly_fig_to_json(fig)

    result = await asyncio.to_thread(_generate)
    return PlotlyResponse(plotly_json=result)


@router.post("/worst-performance", response_model=PlotlyResponse)
async def worst_performance(req: WorstPerformanceRequest):
    def _generate():
        fund = _get_fund_or_404(req.fund_name)
        funds = get_funds()
        bm = funds.get(req.benchmark)
        if bm is None:
            raise HTTPException(status_code=404, detail=f"Benchmark '{req.benchmark}' not found")
        with suppress_show():
            fig = fund.compare_worst_performance(
                benchmark_fund=bm,
                n_worst=req.n_worst,
                save=False,
            )
        return plotly_fig_to_json(fig)

    result = await asyncio.to_thread(_generate)
    return PlotlyResponse(plotly_json=result)
