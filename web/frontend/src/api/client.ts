const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

function put<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
}

// ── System ──
export const getStatus = () => request<SystemStatus>('/system/status')
export const reloadData = () => post<SystemStatus>('/system/reload', {})

// ── Funds ──
export const listFunds = (benchmark?: string) => {
  const q = benchmark ? `?benchmark=${encodeURIComponent(benchmark)}` : ''
  return request<{ funds: FundRow[] }>(`/funds${q}`)
}
export const getFund = (identifier: string) =>
  request<FundDetailResponse>(`/funds/${encodeURIComponent(identifier)}`)
export const getMetrics = (identifier: string, params: MetricsParams) => {
  const q = new URLSearchParams()
  if (params.start_month) q.set('start_month', params.start_month)
  if (params.end_month) q.set('end_month', params.end_month)
  if (params.benchmark) q.set('benchmark', params.benchmark)
  return request<MetricsResponse>(`/funds/${encodeURIComponent(identifier)}/metrics?${q}`)
}

// ── Charts ──
export const chartCumulativeReturns = (body: CumulativeReturnsReq) =>
  post<PlotlyResponse>('/charts/cumulative-returns', body)
export const chartCorrelation = (body: CorrelationReq) =>
  post<CorrelationResponse>('/charts/correlation-heatmap', body)
export const chartDistribution = (body: DistributionReq) =>
  post<PlotlyResponse>('/charts/return-distribution', body)
export const chartRollingVol = (body: RollingVolReq) =>
  post<PlotlyResponse>('/charts/rolling-volatility', body)
export const chartWorstPerformance = (body: WorstPerfReq) =>
  post<PlotlyResponse>('/charts/worst-performance', body)

// ── Tables ──
export const tableKeyMetrics = (body: KeyMetricsReq) =>
  post<ImageResponse>('/tables/key-metrics', body)
export const tableMonthlyReturns = (body: MonthlyTableReq) =>
  post<ImageResponse>('/tables/monthly-returns', body)

// ── MVO ──
export const mvoOptimize = (body: MvoReq) =>
  post<MvoResponse>('/mvo/optimize', body)

// ── Rename ──
export const renameFund = (identifier: string, newName: string) =>
  put<{ success: boolean; message: string; updated_sources: string[] }>(
    `/funds/${encodeURIComponent(identifier)}/rename`,
    { new_name: newName },
  )

// ── Overwrite ──
export const getFundOverwrite = (identifier: string) =>
  request<FundOverwriteData>(`/overwrite/${encodeURIComponent(identifier)}`)
export const saveFundOverwrite = (identifier: string, data: FundOverwriteData & { new_name?: string }) =>
  put<{ success: boolean; message: string }>(`/overwrite/${encodeURIComponent(identifier)}`, data)

// ── Types ──
export interface FundEntry {
  name: string
  identifier: string
}

export interface SystemStatus {
  fund_count: number
  loaded_at: string | null
  fund_names: string[]
  fund_entries: FundEntry[]
  index_names: string[]
  index_identifiers: string[]
  rdgff_names: string[]
  rdgff_identifiers: string[]
  data_paths: Record<string, string>
}

export interface FundRow {
  Name: string
  Identifier: string
  'One Liner': string | null
  'Geo Focus': string | null
  Strategy: string | string[] | null
  'Asset Class': string | string[] | null
  'AUM (in Mn USD)': number | null
  'Mgmt Fee': number | null
  'Perf Fee': number | null
  'Inception Date': string | null
  'Latest Date': string | null
  'Month Running': number | null
  '# Months': number | null
  'Cumulative Return': number | null
  'Annualized Return': number | null
  Volatility: number | null
  'Sharpe Ratio': number | null
  'Sortino Ratio': number | null
  'Max Drawdown': number | null
  'Positive Months': number | null
  'Capture Ratio'?: number | null
  [key: string]: unknown
}

export interface FundDetailResponse {
  name: string
  identifier: string
  one_liner: string | null
  geo_focus: string | null
  strategy: string[] | null
  asset_class: string[] | null
  inception_date: string | null
  latest_date: string | null
  num_months: number
  aum_size: number | null
  management_fee: number | null
  performance_fee: number | null
  net_exposure_info: string | null
  total_cum_rtn: number | null
  total_ann_rtn: number | null
  total_vol: number | null
  total_sharpe: number | null
  total_sortino: number | null
  total_max_dd: number | null
  total_pos_months: number | null
  monthly_returns: { month: string; value: number | null }[]
}

export interface MetricsParams {
  start_month?: string
  end_month?: string
  benchmark?: string
}

export interface MetricsResponse {
  cumulative_return: number | null
  annualized_return: number | null
  volatility: number | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  max_drawdown: number | null
  positive_months: number | null
  beta: number | null
  correlation: number | null
}

export interface PlotlyResponse {
  plotly_json: { data: object[]; layout: object }
}

export interface CorrelationResponse {
  plotly_json: { data: object[]; layout: object }
  correlation_matrix: Record<string, Record<string, number>>
  overlap_matrix: Record<string, Record<string, number>>
}

export interface ImageResponse {
  image_base64: string
}

export interface MvoResponse {
  plotly_json: { data: object[]; layout: object }
  weights: Record<string, number>
  stats: Record<string, number | string>
}

export interface FundOverwriteData {
  entries: { date: string; value: number | null }[]
}

// Request types
export interface CumulativeReturnsReq {
  fund_names: string[]
  title?: string
  start_month?: string
  end_month?: string
  style?: string
  language?: string
  highlight_extremes?: number
  strict_period?: boolean
}

export interface CorrelationReq {
  fund_names: string[]
  method?: string
  min_overlap?: number
  title?: string
}

export interface DistributionReq {
  fund_name: string
  start_month?: string
  end_month?: string
  bins?: number
}

export interface RollingVolReq {
  fund_name: string
  benchmark?: string
  window?: number
}

export interface WorstPerfReq {
  fund_name: string
  benchmark: string
  n_worst?: number
}

export interface KeyMetricsReq {
  fund_name: string
  end_month: string
  benchmark?: string
  language?: string
  metrics?: string[]
  horizontal?: boolean
}

export interface MonthlyTableReq {
  fund_name: string
  end_month?: string
  language?: string
  benchmark?: string
  inception_column?: boolean
}

export interface MvoReq {
  fund_names: string[]
  mode?: string
  target_return?: number
  title?: string
}
