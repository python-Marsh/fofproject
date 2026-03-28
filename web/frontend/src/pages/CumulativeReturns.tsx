import { useMemo, useState } from 'react'
import { Card, Button, Typography, Space, DatePicker, Select, Segmented, Switch, Checkbox } from 'antd'
import { LineChartOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { chartCumulativeReturns, getStatus } from '../api/client'
import PlotlyChart from '../components/PlotlyChart'

const { Title } = Typography

export default function CumulativeReturns() {
  const [selectedFunds, setSelectedFunds] = useState<string[]>([])
  const [selectedIndices, setSelectedIndices] = useState<string[]>([])
  const [selectedUnderlying, setSelectedUnderlying] = useState<string[]>([])
  const [startMonth, setStartMonth] = useState<string | undefined>()
  const [endMonth, setEndMonth] = useState<string | undefined>()
  const [style, setStyle] = useState('default')
  const [language, setLanguage] = useState('en')
  const [strictPeriod, setStrictPeriod] = useState(false)
  const [highlightExtremes, setHighlightExtremes] = useState<number | undefined>()

  const { data: status } = useQuery({ queryKey: ['status'], queryFn: getStatus })
  const allNames = status?.fund_names || []
  const indexNames = status?.index_names || []
  const rdgffNames = status?.rdgff_names || []

  const combined = useMemo(
    () => [...new Set([...selectedFunds, ...selectedIndices, ...selectedUnderlying])],
    [selectedFunds, selectedIndices, selectedUnderlying],
  )

  const mutation = useMutation({ mutationFn: chartCumulativeReturns })

  const allIndexSelected = indexNames.length > 0 && indexNames.every((n) => selectedIndices.includes(n))
  const allUnderlyingSelected = rdgffNames.length > 0 && rdgffNames.every((n) => selectedUnderlying.includes(n))

  const handleGenerate = () => {
    if (combined.length === 0) return
    mutation.mutate({
      fund_names: combined,
      start_month: startMonth,
      end_month: endMonth,
      style,
      language,
      strict_period: strictPeriod,
      highlight_extremes: highlightExtremes,
    })
  }

  const labelStyle = { marginBottom: 4, fontSize: 12, color: '#989A9C' }

  return (
    <div>
      <Title level={3}>Cumulative Returns</Title>
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div style={{ display: 'flex', gap: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={{ ...labelStyle, display: 'flex', alignItems: 'center', height: 22 }}>Funds</div>
              <Select
                mode="multiple"
                allowClear
                showSearch
                placeholder="Select funds..."
                value={selectedFunds}
                onChange={setSelectedFunds}
                options={allNames
                  .filter((n) => !indexNames.includes(n) && !rdgffNames.includes(n))
                  .map((n) => ({ label: n, value: n }))}
                style={{ width: '100%' }}
                maxTagCount="responsive"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 22, marginBottom: 4 }}>
                <span style={labelStyle as React.CSSProperties}>Indices</span>
                <Checkbox
                  checked={allIndexSelected}
                  onChange={() => setSelectedIndices(allIndexSelected ? [] : [...indexNames])}
                  style={{ fontSize: 12 }}
                >
                  Select All
                </Checkbox>
              </div>
              <Select
                mode="multiple"
                allowClear
                showSearch
                placeholder="Select indices..."
                value={selectedIndices}
                onChange={setSelectedIndices}
                options={indexNames.map((n) => ({ label: n, value: n }))}
                style={{ width: '100%' }}
                maxTagCount="responsive"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 22, marginBottom: 4 }}>
                <span style={labelStyle as React.CSSProperties}>Underlying Funds</span>
                <Checkbox
                  checked={allUnderlyingSelected}
                  onChange={() => setSelectedUnderlying(allUnderlyingSelected ? [] : [...rdgffNames])}
                  style={{ fontSize: 12 }}
                >
                  Select All
                </Checkbox>
              </div>
              <Select
                mode="multiple"
                allowClear
                showSearch
                placeholder="Select underlying funds..."
                value={selectedUnderlying}
                onChange={setSelectedUnderlying}
                options={rdgffNames.map((n) => ({ label: n, value: n }))}
                style={{ width: '100%' }}
                maxTagCount="responsive"
              />
            </div>
          </div>

          <Space wrap size="middle">
            <div>
              <div style={labelStyle}>Start</div>
              <DatePicker
                picker="month"
                onChange={(d) => setStartMonth(d ? d.format('YYYY-MM') : undefined)}
                style={{ width: 140 }}
              />
            </div>
            <div>
              <div style={labelStyle}>End</div>
              <DatePicker
                picker="month"
                onChange={(d) => setEndMonth(d ? d.format('YYYY-MM') : undefined)}
                style={{ width: 140 }}
              />
            </div>
            <div>
              <div style={labelStyle}>Style</div>
              <Select
                value={style}
                onChange={setStyle}
                options={[
                  { label: 'Default', value: 'default' },
                  { label: 'PowerPoint', value: 'pptx' },
                  { label: 'Excel', value: 'excel' },
                ]}
                style={{ width: 130 }}
              />
            </div>
            <div>
              <div style={labelStyle}>Language</div>
              <Segmented
                options={[
                  { label: 'EN', value: 'en' },
                  { label: 'CN', value: 'cn' },
                ]}
                value={language}
                onChange={(v) => setLanguage(v as string)}
              />
            </div>
            <div>
              <div style={labelStyle}>Strict Period</div>
              <Switch checked={strictPeriod} onChange={setStrictPeriod} />
            </div>
            <div>
              <div style={labelStyle}>Highlight Top N</div>
              <Select
                allowClear
                placeholder="Off"
                value={highlightExtremes}
                onChange={setHighlightExtremes}
                options={[3, 5, 10].map((n) => ({ label: `Top ${n}`, value: n }))}
                style={{ width: 100 }}
              />
            </div>
            <Button
              type="primary"
              icon={<LineChartOutlined />}
              onClick={handleGenerate}
              loading={mutation.isPending}
              disabled={combined.length === 0}
              size="large"
              style={{ marginTop: 18 }}
            >
              Generate ({combined.length} selected)
            </Button>
          </Space>
        </Space>
      </Card>

      {mutation.data && (
        <Card>
          <PlotlyChart
            data={mutation.data.plotly_json.data}
            layout={mutation.data.plotly_json.layout}
            style={{ height: 600 }}
          />
        </Card>
      )}

      {mutation.error && (
        <Card>
          <Typography.Text type="danger">
            {mutation.error instanceof Error ? mutation.error.message : 'Error generating chart'}
          </Typography.Text>
        </Card>
      )}
    </div>
  )
}
