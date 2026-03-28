"""System endpoints: status and reload."""

from fastapi import APIRouter

from web.backend.state import get_funds, reload_funds, get_loaded_at, get_index_names, get_rdgff_names
from web.backend.schemas import SystemStatus

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status", response_model=SystemStatus)
def status():
    funds = get_funds()
    loaded_at = get_loaded_at()
    return SystemStatus(
        fund_count=len(funds),
        loaded_at=loaded_at.isoformat() if loaded_at else None,
        fund_names=list(funds.keys()),
        index_names=get_index_names(),
        rdgff_names=get_rdgff_names(),
    )


@router.post("/reload", response_model=SystemStatus)
def reload():
    funds = reload_funds()
    loaded_at = get_loaded_at()
    return SystemStatus(
        fund_count=len(funds),
        loaded_at=loaded_at.isoformat() if loaded_at else None,
        fund_names=list(funds.keys()),
        index_names=get_index_names(),
        rdgff_names=get_rdgff_names(),
    )
