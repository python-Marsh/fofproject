import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Card, Tabs, Typography, Descriptions, Select, Spin, Space, Tag, Segmented, Table, InputNumber, DatePicker, Button, Popconfirm, message } from 'antd'
import dayjs from 'dayjs'
import { PlusOutlined, DeleteOutlined, SaveOutlined } from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getFund,
  getStatus,
  tableKeyMetrics,
  tableMonthlyReturns,
  chartDistribution,
  chartRollingVol,
  chartWorstPerformance,
  getFundOverwrite,
  saveFundOverwrite,
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

  const { data: keyMetrics, error: keyMetricsError } = useQuery({
    queryKey: ['keyMetrics', decodedName, endMonth, benchmark, language],
    queryFn: () =>
      tableKeyMetrics({
        fund_name: decodedName,
        end_month: endMonth!,
        benchmark,
        language,
      }),
    enabled: !!endMonth,
    retry: false,
  })

  const { data: monthlyTable, error: monthlyTableError } = useQuery({
    queryKey: ['monthlyTable', decodedName, endMonth, benchmark, language],
    queryFn: () =>
      tableMonthlyReturns({
        fund_name: decodedName,
        end_month: endMonth,
        benchmark,
        language,
      }),
    enabled: !!endMonth,
    retry: false,
  })

  const { data: distribution, error: distError } = useQuery({
    queryKey: ['distribution', decodedName],
    queryFn: () => chartDistribution({ fund_name: decodedName }),
    enabled: !!decodedName,
    retry: false,
  })

  const { data: rollingVol, error: rollingVolError } = useQuery({
    queryKey: ['rollingVol', decodedName, benchmark],
    queryFn: () => chartRollingVol({ fund_name: decodedName, benchmark }),
    enabled: !!decodedName,
    retry: false,
  })

  const { data: worstPerf, error: worstPerfError } = useQuery({
    queryKey: ['worstPerf', decodedName, benchmark],
    queryFn: () =>
      chartWorstPerformance({
        fund_name: decodedName,
        benchmark: benchmark || 'MSCI WORLD',
      }),
    enabled: !!decodedName,
    retry: false,
  })

  // ── Overwrite state ──
  const queryClient = useQueryClient()
  const { data: overwriteData } = useQuery({
    queryKey: ['fundOverwrite', decodedName],
    queryFn: () => getFundOverwrite(decodedName),
    enabled: !!decodedName,
  })

  const [owEntries, setOwEntries] = useState<{ date: string; value: number | null }[]>([])
  const [owDirty, setOwDirty] = useState(false)
  const [owInitialized, setOwInitialized] = useState<string | null>(null)

  // Sync overwrite data when it loads or fund changes
  if (overwriteData && owInitialized !== decodedName) {
    setOwEntries(overwriteData.entries.map((e) => ({ ...e })))
    setOwDirty(false)
    setOwInitialized(decodedName)
  }

  const owSaveMutation = useMutation({
    mutationFn: (entries: { date: string; value: number | null }[]) =>
      saveFundOverwrite(decodedName, { entries }),
    onSuccess: (res) => {
      message.success(res.message)
      setOwDirty(false)
      queryClient.invalidateQueries({ queryKey: ['fundOverwrite', decodedName] })
      queryClient.invalidateQueries({ queryKey: ['status'] })
      queryClient.invalidateQueries({ queryKey: ['funds'] })
      queryClient.invalidateQueries({ queryKey: ['fund', decodedName] })
      queryClient.invalidateQueries({ queryKey: ['keyMetrics'] })
      queryClient.invalidateQueries({ queryKey: ['monthlyTable'] })
    },
    onError: (err: Error) => message.error(err.message),
  })

  const owUpdateEntry = (idx: number, field: 'date' | 'value', val: string | number | null) => {
    const next = owEntries.map((e, i) => (i === idx ? { ...e, [field]: val } : e))
    setOwEntries(next)
    setOwDirty(true)
  }

  const owAddRow = () => {
    setOwEntries([...owEntries, { date: '', value: null }])
    setOwDirty(true)
  }

  const owDeleteRow = (idx: number) => {
    setOwEntries(owEntries.filter((_, i) => i !== idx))
    setOwDirty(true)
  }

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
                  {keyMetricsError ? (
                    <Typography.Text type="danger" style={{ whiteSpace: 'pre-line' }}>
                      {keyMetricsError instanceof Error ? keyMetricsError.message : 'Failed to load key metrics'}
                    </Typography.Text>
                  ) : keyMetrics ? (
                    <PngImage src={keyMetrics.image_base64} alt="Key Metrics" downloadName={`${fund.name}_metrics.png`} />
                  ) : null}
                  {monthlyTableError ? (
                    <Typography.Text type="danger" style={{ whiteSpace: 'pre-line' }}>
                      {monthlyTableError instanceof Error ? monthlyTableError.message : 'Failed to load monthly returns'}
                    </Typography.Text>
                  ) : monthlyTable ? (
                    <PngImage src={monthlyTable.image_base64} alt="Monthly Returns" downloadName={`${fund.name}_monthly.png`} />
                  ) : null}
                </Space>
              ),
            },
            {
              key: 'distribution',
              label: 'Distribution',
              children: distError ? (
                <Typography.Text type="danger" style={{ whiteSpace: 'pre-line' }}>
                  {distError instanceof Error ? distError.message : 'Failed to load distribution chart'}
                </Typography.Text>
              ) : distribution ? (
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
              children: rollingVolError ? (
                <Typography.Text type="danger" style={{ whiteSpace: 'pre-line' }}>
                  {rollingVolError instanceof Error ? rollingVolError.message : 'Failed to load rolling volatility chart'}
                </Typography.Text>
              ) : rollingVol ? (
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
              children: worstPerfError ? (
                <Typography.Text type="danger" style={{ whiteSpace: 'pre-line' }}>
                  {worstPerfError instanceof Error ? worstPerfError.message : 'Failed to load worst performance chart'}
                </Typography.Text>
              ) : worstPerf ? (
                <PlotlyChart
                  data={worstPerf.plotly_json.data}
                  layout={worstPerf.plotly_json.layout}
                  style={{ height: 500 }}
                />
              ) : (
                <Spin />
              ),
            },
            {
              key: 'overwrite',
              label: 'Overwrite',
              children: (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                    <Typography.Text type="secondary">
                      Override monthly returns for <strong>{fund.name}</strong>. Select a month, value as decimal (0.05 = 5%).
                    </Typography.Text>
                    <Space>
                      <Button icon={<PlusOutlined />} size="small" onClick={owAddRow}>
                        Add Row
                      </Button>
                      <Popconfirm
                        title="Save overwrite?"
                        description={`This will update MANUAL OVERWRITE.csv for ${fund.name} and reload fund data.`}
                        onConfirm={() => owSaveMutation.mutate(owEntries)}
                        okText="Save"
                      >
                        <Button
                          type="primary"
                          icon={<SaveOutlined />}
                          size="small"
                          loading={owSaveMutation.isPending}
                          disabled={!owDirty}
                        >
                          Save
                        </Button>
                      </Popconfirm>
                    </Space>
                  </div>
                  <Table
                    size="small"
                    pagination={false}
                    scroll={{ y: 400 }}
                    dataSource={owEntries.map((e, i) => ({ ...e, key: i }))}
                    columns={[
                      {
                        title: '#',
                        width: 50,
                        render: (_: unknown, __: unknown, i: number) => i + 1,
                      },
                      {
                        title: 'Date',
                        dataIndex: 'date',
                        width: 150,
                        render: (val: string, _: unknown, i: number) => (
                          <DatePicker
                            picker="month"
                            size="small"
                            value={val ? dayjs(val, 'YYYY-MM') : null}
                            onChange={(d) => owUpdateEntry(i, 'date', d ? d.format('YYYY-MM') : '')}
                            style={{ width: '100%' }}
                          />
                        ),
                      },
                      {
                        title: 'Return',
                        dataIndex: 'value',
                        width: 150,
                        render: (val: number | null, _: unknown, i: number) => (
                          <InputNumber
                            size="small"
                            value={val}
                            onChange={(v) => owUpdateEntry(i, 'value', v)}
                            step={0.001}
                            style={{ width: '100%' }}
                            controls={false}
                          />
                        ),
                      },
                      {
                        title: '',
                        width: 40,
                        render: (_: unknown, __: unknown, i: number) => (
                          <Button
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            size="small"
                            onClick={() => owDeleteRow(i)}
                          />
                        ),
                      },
                    ]}
                  />
                </div>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
