"""MVO (Mean-Variance Optimization) endpoint."""

import asyncio

from fastapi import APIRouter, HTTPException

from fofproject.fund import subset_of_funds
from fofproject.mvo import minimum_variance_analysis
from web.backend.state import get_funds
from web.backend.utils import suppress_show, plotly_fig_to_json
from web.backend.schemas import MvoRequest, MvoResponse

router = APIRouter(prefix="/api/mvo", tags=["mvo"])


@router.post("/optimize", response_model=MvoResponse)
async def optimize(req: MvoRequest):
    def _generate():
        funds = get_funds()
        selected = subset_of_funds(funds, req.fund_names)
        with suppress_show():
            fig, weights, stats = minimum_variance_analysis(
                funds=selected,
                mode=req.mode,
                target_return=req.target_return,
                title=req.title,
            )
        return {
            "plotly_json": plotly_fig_to_json(fig),
            "weights": {k: float(v) for k, v in weights.items()},
            "stats": {k: float(v) if isinstance(v, (int, float)) else v for k, v in stats.items()},
        }

    try:
        result = await asyncio.to_thread(_generate)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        err_type = type(e).__name__
        msg = str(e)
        if "OSQP" in err_type or "singular" in msg.lower() or "non-convex" in msg.lower() or msg.strip().isdigit():
            raise HTTPException(
                status_code=422,
                detail="Optimization failed — the selected funds likely have insufficient overlapping data to build a valid covariance matrix. Try removing funds with very short track records.",
            )
        raise HTTPException(status_code=422, detail=f"Optimization failed: {msg}")
    return MvoResponse(**result)
