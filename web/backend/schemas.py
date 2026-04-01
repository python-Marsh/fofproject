"""Pydantic request/response models for the API."""

from typing import Optional
from pydantic import BaseModel


# ── Response Models ──────────────────────────────────────

class FundEntry(BaseModel):
    name: str
    identifier: str


class SystemStatus(BaseModel):
    fund_count: int
    loaded_at: Optional[str] = None
    fund_names: list[str]
    fund_entries: list[FundEntry] = []
    index_names: list[str] = []
    index_identifiers: list[str] = []
    rdgff_names: list[str] = []
    rdgff_identifiers: list[str] = []
    data_paths: dict[str, str] = {}


class MonthlyReturn(BaseModel):
    month: str
    value: Optional[float] = None


class FundDetail(BaseModel):
    name: str
    identifier: str
    one_liner: Optional[str] = None
    geo_focus: Optional[str] = None
    strategy: Optional[list[str]] = None
    asset_class: Optional[list[str]] = None
    inception_date: Optional[str] = None
    latest_date: Optional[str] = None
    num_months: int = 0
    aum_size: Optional[float] = None
    management_fee: Optional[float] = None
    performance_fee: Optional[float] = None
    net_exposure_info: Optional[str] = None
    total_cum_rtn: Optional[float] = None
    total_ann_rtn: Optional[float] = None
    total_vol: Optional[float] = None
    total_sharpe: Optional[float] = None
    total_sortino: Optional[float] = None
    total_max_dd: Optional[float] = None
    total_pos_months: Optional[float] = None
    monthly_returns: list[MonthlyReturn] = []


class MetricsResponse(BaseModel):
    cumulative_return: Optional[float] = None
    annualized_return: Optional[float] = None
    volatility: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    positive_months: Optional[float] = None
    beta: Optional[float] = None
    correlation: Optional[float] = None


class PlotlyResponse(BaseModel):
    plotly_json: dict


class ImageResponse(BaseModel):
    image_base64: str


class MvoResponse(BaseModel):
    plotly_json: dict
    weights: dict[str, float]
    stats: dict



# ── Request Models ───────────────────────────────────────

class CumulativeReturnsRequest(BaseModel):
    fund_names: list[str]
    title: str = ""
    start_month: Optional[str] = None
    end_month: Optional[str] = None
    style: Optional[str] = "default"
    language: str = "en"
    highlight_extremes: Optional[int] = None
    strict_period: bool = False


class CorrelationRequest(BaseModel):
    fund_names: list[str]
    method: str = "pearson"
    min_overlap: int = 6
    title: str = "Fund Return Correlations"


class DistributionRequest(BaseModel):
    fund_name: str
    start_month: Optional[str] = None
    end_month: Optional[str] = None
    bins: int = 24


class RollingVolRequest(BaseModel):
    fund_name: str
    benchmark: Optional[str] = None
    window: int = 12


class WorstPerformanceRequest(BaseModel):
    fund_name: str
    benchmark: str
    n_worst: int = 10


class KeyMetricsRequest(BaseModel):
    fund_name: str
    end_month: str
    benchmark: Optional[str] = None
    language: str = "en"
    metrics: Optional[list[str]] = None
    horizontal: bool = False


class MonthlyTableRequest(BaseModel):
    fund_name: str
    end_month: Optional[str] = None
    language: str = "en"
    benchmark: Optional[str] = None
    inception_column: bool = False


class MvoRequest(BaseModel):
    fund_names: list[str]
    mode: str = "Maximum Sharpe"
    target_return: float = 0.14
    title: Optional[str] = None
