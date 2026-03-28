import { useMemo, useState } from 'react'
import { Card, Button, Typography, Space, Select, InputNumber, Checkbox } from 'antd'
import { HeatMapOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { chartCorrelation, getStatus } from '../api/client'
import PlotlyChart from '../components/PlotlyChart'

const { Title } = Typography

export default function Correlation() {
  const [selectedFunds, setSelectedFunds] = useState<string[]>([])
  const [selectedIndices, setSelectedIndices] = useState<string[]>([])
  const [selectedUnderlying, setSelectedUnderlying] = useState<string[]>([])
  const [method, setMethod] = useState('pearson')
  const [minOverlap, setMinOverlap] = useState(6)

  const { data: status } = useQuery({ queryKey: ['status'], queryFn: getStatus })
  const allNames = status?.fund_names || []
  const indexNames = status?.index_names || []
  const rdgffNames = status?.rdgff_names || []

  const combined = useMemo(
    () => [...new Set([...selectedFunds, ...selectedIndices, ...selectedUnderlying])],
    [selectedFunds, selectedIndices, selectedUnderlying],
  )

  const mutation = useMutation({ mutationFn: chartCorrelation })

  const allIndexSelected = indexNames.length > 0 && indexNames.every((n) => selectedIndices.includes(n))
  const allUnderlyingSelected = rdgffNames.length > 0 && rdgffNames.every((n) => selectedUnderlying.includes(n))

  const handleGenerate = () => {
    if (combined.length < 2) return
    mutation.mutate({ fund_names: combined, method, min_overlap: minOverlap })
  }

  const labelStyle = { marginBottom: 4, fontSize: 12, color: '#989A9C' }

  return (
    <div>
      <Title level={3}>Correlation Heatmap</Title>
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
              <div style={labelStyle}>Method</div>
              <Select
                value={method}
                onChange={setMethod}
                options={[
                  { label: 'Pearson', value: 'pearson' },
                  { label: 'Spearman', value: 'spearman' },
                  { label: 'Kendall', value: 'kendall' },
                ]}
                style={{ width: 130 }}
              />
            </div>
            <div>
              <div style={labelStyle}>Min Overlap</div>
              <InputNumber min={1} max={120} value={minOverlap} onChange={(v) => setMinOverlap(v || 6)} />
            </div>
            <Button
              type="primary"
              icon={<HeatMapOutlined />}
              onClick={handleGenerate}
              loading={mutation.isPending}
              disabled={combined.length < 2}
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
            {mutation.error instanceof Error ? mutation.error.message : 'Error generating heatmap'}
          </Typography.Text>
        </Card>
      )}
    </div>
  )
}
