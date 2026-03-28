import { useMemo, useState } from 'react'
import { Card, Button, Typography, Space, Radio, InputNumber, Descriptions, Select, Checkbox } from 'antd'
import { PieChartOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { mvoOptimize, getStatus } from '../api/client'
import PlotlyChart from '../components/PlotlyChart'

const { Title } = Typography

const MODES = ['Minimum Variance', 'Maximum Sharpe', 'Target Return']

export default function MVO() {
  const [selectedFunds, setSelectedFunds] = useState<string[]>([])
  const [selectedIndices, setSelectedIndices] = useState<string[]>([])
  const [selectedUnderlying, setSelectedUnderlying] = useState<string[]>([])
  const [mode, setMode] = useState('Maximum Sharpe')
  const [targetReturn, setTargetReturn] = useState(0.14)

  const { data: status } = useQuery({ queryKey: ['status'], queryFn: getStatus })
  const allNames = status?.fund_names || []
  const indexNames = status?.index_names || []
  const rdgffNames = status?.rdgff_names || []

  const combined = useMemo(
    () => [...new Set([...selectedFunds, ...selectedIndices, ...selectedUnderlying])],
    [selectedFunds, selectedIndices, selectedUnderlying],
  )

  const mutation = useMutation({ mutationFn: mvoOptimize })

  const allIndexSelected = indexNames.length > 0 && indexNames.every((n) => selectedIndices.includes(n))
  const allUnderlyingSelected = rdgffNames.length > 0 && rdgffNames.every((n) => selectedUnderlying.includes(n))

  const handleOptimize = () => {
    if (combined.length < 2) return
    mutation.mutate({
      fund_names: combined,
      mode,
      target_return: targetReturn,
    })
  }

  const result = mutation.data
  const labelStyle = { marginBottom: 4, fontSize: 12, color: '#989A9C' }

  return (
    <div>
      <Title level={3}>Mean-Variance Optimization</Title>
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
              <div style={labelStyle}>Mode</div>
              <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
                {MODES.map((m) => (
                  <Radio.Button key={m} value={m}>{m}</Radio.Button>
                ))}
              </Radio.Group>
            </div>
            {mode === 'Target Return' && (
              <div>
                <div style={labelStyle}>Target Return (%)</div>
                <InputNumber
                  min={0}
                  max={1}
                  step={0.01}
                  value={targetReturn}
                  onChange={(v) => setTargetReturn(v || 0.14)}
                  formatter={(v) => `${((v || 0) * 100).toFixed(1)}%`}
                  parser={(v) => parseFloat(v?.replace('%', '') || '14') / 100}
                  style={{ width: 120 }}
                />
              </div>
            )}
            <Button
              type="primary"
              icon={<PieChartOutlined />}
              onClick={handleOptimize}
              loading={mutation.isPending}
              disabled={combined.length < 2}
              size="large"
              style={{ marginTop: 18 }}
            >
              Optimize ({combined.length} selected)
            </Button>
          </Space>
        </Space>
      </Card>

      {result && (
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Card title="Optimal Weights">
            <PlotlyChart
              data={result.plotly_json.data}
              layout={result.plotly_json.layout}
              style={{ height: 400 }}
            />
          </Card>
          <Card title="Portfolio Statistics">
            <Descriptions column={4}>
              {Object.entries(result.stats).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>
                  {typeof v === 'number' ? v.toFixed(4) : String(v)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
          <Card title="Weights">
            <Descriptions column={4}>
              {Object.entries(result.weights).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>
                  {(v * 100).toFixed(2)}%
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        </Space>
      )}

      {mutation.error && (
        <Card>
          <Typography.Text type="danger">
            {mutation.error instanceof Error ? mutation.error.message : 'Optimization error'}
          </Typography.Text>
        </Card>
      )}
    </div>
  )
}
