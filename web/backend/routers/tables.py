"""Table endpoints returning matplotlib figures as PNG base64."""

import asyncio

from fastapi import APIRouter, HTTPException

from web.backend.state import get_funds
from web.backend.utils import suppress_show, matplotlib_fig_to_base64
from web.backend.schemas import (
    KeyMetricsRequest,
    MonthlyTableRequest,
    ImageResponse,
)

router = APIRouter(prefix="/api/tables", tags=["tables"])


@router.post("/key-metrics", response_model=ImageResponse)
async def key_metrics(req: KeyMetricsRequest):
    def _generate():
        funds = get_funds()
        fund = funds.get(req.fund_name)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"Fund '{req.fund_name}' not found")
        bm = funds.get(req.benchmark) if req.benchmark else fund.default_benchmark
        # Filter out benchmark-dependent metrics when no benchmark is available
        _BM_METRICS = {"beta", "corr", "capture"}
        metrics = req.metrics
        has_bm = bm is not None
        if not has_bm:
            if metrics:
                metrics = [m for m in metrics if m not in _BM_METRICS]
            else:
                metrics = ["cagr", "vol", "sharpe", "sortino", "mdd", "win"]
            # Use fund itself as dummy benchmark to avoid NoneType error
            # in label f-strings (the bm-dependent metrics are excluded above)
            bm = fund
        with suppress_show():
            fig = fund.export_key_metrics_table(
                end_month=req.end_month,
                benchmark_fund=bm,
                language=req.language,
                metrics=metrics,
                horizontal=req.horizontal,
                save=False,
            )
        return matplotlib_fig_to_base64(fig)

    result = await asyncio.to_thread(_generate)
    return ImageResponse(image_base64=result)


@router.post("/monthly-returns", response_model=ImageResponse)
async def monthly_returns(req: MonthlyTableRequest):
    def _generate():
        funds = get_funds()
        fund = funds.get(req.fund_name)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"Fund '{req.fund_name}' not found")
        bm = funds.get(req.benchmark) if req.benchmark else fund.default_benchmark
        with suppress_show():
            fig = fund.export_monthly_table(
                language=req.language,
                benchmark_fund=bm,
                inception_column=req.inception_column,
                end_month=req.end_month,
                save=False,
            )
        return matplotlib_fig_to_base64(fig)

    result = await asyncio.to_thread(_generate)
    return ImageResponse(image_base64=result)
