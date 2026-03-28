import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Card, Tabs, Typography, Descriptions, Select, Spin, Space, Tag, Segmented } from 'antd'
import { useQuery } from '@tanstack/react-query'
import {
  getFund,
  getStatus,
  tableKeyMetrics,
  tableMonthlyReturns,
  chartDistribution,
  chartRollingVol,
  chartWorstPerformance,
} from '../api/client'
import PlotlyChart from '../components/PlotlyChart'
import PngImage from '../components/PngImage'

const { Title } = Typography

const pct = (v: number | null | undefined) =>
  v != null ? `${(v * 100).toFixed(2)}%` : '—'

export default function FundDetail() {
  const { name } = useParams<{ name: string }>()
  const decodedName = decodeURIComponent(name || '')
  const [benchmark, setBenchmark] = useState<string | undefined>()
  const [language, setLanguage] = useState<string>('en')

  const { data: status } = useQuery({ queryKey: ['status'], queryFn: getStatus })
  const { data: fund, isLoading } = useQuery({
    queryKey: ['fund', decodedName],
    queryFn: () => getFund(decodedName),
    enabled: !!decodedName,
  })

  const endMonth = fund?.latest_date || undefined

  const { data: keyMetrics } = useQuery({
    queryKey: ['keyMetrics', decodedName, endMonth, benchmark, language],
    queryFn: () =>
      tableKeyMetrics({
        fund_name: decodedName,
        end_month: endMonth!,
        benchmark,
        language,
      }),
    enabled: !!endMonth,
  })

  const { data: monthlyTable } = useQuery({
    queryKey: ['monthlyTable', decodedName, endMonth, benchmark, language],
    queryFn: () =>
      tableMonthlyReturns({
        fund_name: decodedName,
        end_month: endMonth,
        benchmark,
        language,
      }),
    enabled: !!endMonth,
  })

  const { data: distribution } = useQuery({
    queryKey: ['distribution', decodedName],
    queryFn: () => chartDistribution({ fund_name: decodedName }),
    enabled: !!decodedName,
  })

  const { data: rollingVol } = useQuery({
    queryKey: ['rollingVol', decodedName, benchmark],
    queryFn: () => chartRollingVol({ fund_name: decodedName, benchmark }),
    enabled: !!decodedName,
  })

  const { data: worstPerf } = useQuery({
    queryKey: ['worstPerf', decodedName, benchmark],
    queryFn: () =>
      chartWorstPerformance({
        fund_name: decodedName,
        benchmark: benchmark || 'MSCI WORLD',
      }),
    enabled: !!decodedName,
  })

  if (isLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />

  if (!fund) return <Title level={4}>Fund not found</Title>

  const benchmarkOptions = (status?.fund_names || []).map((n) => ({
    label: n,
    value: n,
  }))

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>{fund.name}</Title>
          {fund.one_liner && (
            <Typography.Text type="secondary" style={{ fontSize: 14 }}>
              {fund.one_liner}
            </Typography.Text>
          )}
          <div style={{ marginTop: 8 }}>
            {fund.strategy?.map((s, i) => <Tag key={i}>{s}</Tag>)}
            {fund.geo_focus && <Tag color="#c1ae94">{fund.geo_focus}</Tag>}
          </div>
        </div>
        <Space>
          <Segmented
            options={[
              { label: 'EN', value: 'en' },
              { label: 'CN', value: 'cn' },
            ]}
            value={language}
            onChange={(v) => setLanguage(v as string)}
          />
          <Select
            allowClear
            showSearch
            placeholder="Benchmark"
            value={benchmark}
            onChange={setBenchmark}
            options={benchmarkOptions}
            style={{ width: 200 }}
          />
        </Space>
      </div>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions size="small" column={6}>
          <Descriptions.Item label="Inception">{fund.inception_date || '—'}</Descriptions.Item>
          <Descriptions.Item label="Latest">{fund.latest_date || '—'}</Descriptions.Item>
          <Descriptions.Item label="Months">{fund.num_months}</Descriptions.Item>
          <Descriptions.Item label="Ann. Return">{pct(fund.total_ann_rtn)}</Descriptions.Item>
          <Descriptions.Item label="Sharpe">{fund.total_sharpe?.toFixed(2) ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Max DD">{pct(fund.total_max_dd)}</Descriptions.Item>
          <Descriptions.Item label="AUM ($M)">
            {fund.aum_size != null ? fund.aum_size.toFixed(0) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Mgmt Fee">{pct(fund.management_fee)}</Descriptions.Item>
          <Descriptions.Item label="Perf Fee">{pct(fund.performance_fee)}</Descriptions.Item>
          <Descriptions.Item label="Vol">{pct(fund.total_vol)}</Descriptions.Item>
          <Descriptions.Item label="Sortino">{fund.total_sortino?.toFixed(2) ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Win Rate">{pct(fund.total_pos_months)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card>
        <Tabs
          items={[
            {
              key: 'summary',
              label: 'Summary',
              children: (
                <Space direction="vertical" size="large" style={{ width: '100%' }}>
                  {keyMetrics && (
                    <PngImage src={keyMetrics.image_base64} alt="Key Metrics" downloadName={`${fund.name}_metrics.png`} />
                  )}
                  {monthlyTable && (
                    <PngImage src={monthlyTable.image_base64} alt="Monthly Returns" downloadName={`${fund.name}_monthly.png`} />
                  )}
                </Space>
              ),
            },
            {
              key: 'distribution',
              label: 'Distribution',
              children: distribution ? (
                <PlotlyChart
                  data={distribution.plotly_json.data}
                  layout={distribution.plotly_json.layout}
                  style={{ height: 500 }}
                />
              ) : (
                <Spin />
              ),
            },
            {
              key: 'rolling-vol',
              label: 'Rolling Volatility',
              children: rollingVol ? (
                <PlotlyChart
                  data={rollingVol.plotly_json.data}
                  layout={rollingVol.plotly_json.layout}
                  style={{ height: 500 }}
                />
              ) : (
                <Spin />
              ),
            },
            {
              key: 'worst',
              label: 'Worst Months',
              children: worstPerf ? (
                <PlotlyChart
                  data={worstPerf.plotly_json.data}
                  layout={worstPerf.plotly_json.layout}
                  style={{ height: 500 }}
                />
              ) : (
                <Spin />
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
