"""Table endpoints returning matplotlib figures as PNG base64."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from web.backend.state import get_funds
from web.backend.utils import suppress_show, matplotlib_fig_to_base64
from web.backend.schemas import (
    KeyMetricsRequest,
    MonthlyTableRequest,
    ImageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tables", tags=["tables"])


@router.post("/key-metrics", response_model=ImageResponse)
async def key_metrics(req: KeyMetricsRequest):
    def _generate():
        funds = get_funds()
        fund = funds.get(req.fund_name)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"Fund '{req.fund_name}' not found")
        bm = funds.get(req.benchmark) if req.benchmark else fund.default_benchmark
        _BM_METRICS = {"beta", "corr", "capture"}
        _BASE_METRICS = ["cagr", "vol", "sharpe", "sortino", "mdd", "win"]
        metrics = req.metrics
        has_bm = bm is not None
        if metrics:
            if not has_bm:
                metrics = [m for m in metrics if m not in _BM_METRICS]
        else:
            # Default: base metrics + benchmark-dependent metrics when benchmark is set
            metrics = _BASE_METRICS + (["beta", "corr", "capture"] if has_bm else [])
        if not has_bm:
            # Use fund itself as dummy benchmark to avoid NoneType error
            # in label f-strings (the bm-dependent metrics are excluded above)
            bm = fund
        try:
            with suppress_show():
                fig = fund.export_key_metrics_table(
                    end_month=req.end_month,
                    benchmark_fund=bm,
                    language=req.language,
                    metrics=metrics,
                    horizontal=req.horizontal,
                    save=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate key metrics table for '{req.fund_name}': {e}",
            )
        return matplotlib_fig_to_base64(fig)

    try:
        result = await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("key-metrics failed")
        raise HTTPException(status_code=400, detail=f"Key metrics table failed: {e}")
    return ImageResponse(image_base64=result)


@router.post("/monthly-returns", response_model=ImageResponse)
async def monthly_returns(req: MonthlyTableRequest):
    def _generate():
        funds = get_funds()
        fund = funds.get(req.fund_name)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"Fund '{req.fund_name}' not found")
        bm = funds.get(req.benchmark) if req.benchmark else fund.default_benchmark
        try:
            with suppress_show():
                fig = fund.export_monthly_table(
                    language=req.language,
                    benchmark_fund=bm,
                    inception_column=req.inception_column,
                    end_month=req.end_month,
                    save=False,
                )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to generate monthly returns table for '{req.fund_name}': {e}",
            )
        return matplotlib_fig_to_base64(fig)

    try:
        result = await asyncio.to_thread(_generate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("monthly-returns failed")
        raise HTTPException(status_code=400, detail=f"Monthly returns table failed: {e}")
    return ImageResponse(image_base64=result)
