"""System endpoints: status and reload."""

from fastapi import APIRouter

from fofproject.paths import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, MANUAL_OVERWRITE_PATH
from web.backend.state import get_funds, reload_funds, get_loaded_at, get_index_names, get_rdgff_names
from web.backend.schemas import SystemStatus

router = APIRouter(prefix="/api/system", tags=["system"])


def _build_data_paths() -> dict[str, str]:
    benchmark_path = DEFAULT_INPUT_DIR / "HF index comparison.xlsx"
    return {
        "Input Directory": str(DEFAULT_INPUT_DIR),
        "Benchmark File": str(benchmark_path),
        "Manual Overwrite": str(MANUAL_OVERWRITE_PATH),
        "JSON Firm Folders": str(DEFAULT_OUTPUT_DIR),
    }


def _build_status(funds, loaded_at) -> SystemStatus:
    return SystemStatus(
        fund_count=len(funds),
        loaded_at=loaded_at.isoformat() if loaded_at else None,
        fund_names=list(funds.keys()),
        index_names=get_index_names(),
        rdgff_names=get_rdgff_names(),
        data_paths=_build_data_paths(),
    )


@router.get("/status", response_model=SystemStatus)
def status():
    return _build_status(get_funds(), get_loaded_at())


@router.post("/reload", response_model=SystemStatus)
def reload():
    return _build_status(reload_funds(), get_loaded_at())
