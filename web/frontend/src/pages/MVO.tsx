import { useMemo, useState } from 'react'
import { Card, Button, Typography, Space, Radio, InputNumber, Descriptions, Select, Checkbox } from 'antd'
import { PieChartOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { mvoOptimize, getStatus } from '../api/client'
import PlotlyChart from '../components/PlotlyChart'
import { useResponsive } from '../hooks/useResponsive'

const { Title } = Typography

const MODES = ['Minimum Variance', 'Maximum Sharpe', 'Target Return']

export default function MVO() {
  const { isMobile } = useResponsive()
  const [selectedFunds, setSelectedFunds] = useState<string[]>([])
  const [selectedIndices, setSelectedIndices] = useState<string[]>([])
  const [selectedUnderlying, setSelectedUnderlying] = useState<string[]>([])
  const [mode, setMode] = useState('Maximum Sharpe')
  const [targetReturn, setTargetReturn] = useState(0.14)

  const { data: status } = useQuery({ queryKey: ['status'], queryFn: getStatus })
  const allEntries = status?.fund_entries || []
  const indexIds = new Set(status?.index_identifiers || [])
  const rdgffIds = new Set(status?.rdgff_identifiers || [])

  const combined = useMemo(
    () => [...new Set([...selectedFunds, ...selectedIndices, ...selectedUnderlying])],
    [selectedFunds, selectedIndices, selectedUnderlying],
  )

  const mutation = useMutation({ mutationFn: mvoOptimize })

  const indexEntries = allEntries.filter((e) => indexIds.has(e.identifier))
  const rdgffEntries = allEntries.filter((e) => rdgffIds.has(e.identifier))
  const fundEntries = allEntries.filter((e) => !indexIds.has(e.identifier) && !rdgffIds.has(e.identifier))
  const indexIdentifiers = indexEntries.map((e) => e.identifier)
  const rdgffIdentifiers = rdgffEntries.map((e) => e.identifier)

  const allIndexSelected = indexIdentifiers.length > 0 && indexIdentifiers.every((id) => selectedIndices.includes(id))
  const allUnderlyingSelected = rdgffIdentifiers.length > 0 && rdgffIdentifiers.every((id) => selectedUnderlying.includes(id))

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
          <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={{ ...labelStyle, display: 'flex', alignItems: 'center', height: 22 }}>Pipeline Funds</div>
              <Select
                mode="multiple"
                allowClear
                showSearch
                placeholder="Search by name or identifier..."
                value={selectedFunds}
                onChange={setSelectedFunds}
                options={fundEntries.map((e) => ({ label: e.name, value: e.identifier }))}
                filterOption={(input, option) =>
                  `${option?.label} ${option?.value}`.toLowerCase().includes(input.toLowerCase())
                }
                style={{ width: '100%' }}
                maxTagCount="responsive"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 22, marginBottom: 4 }}>
                <span style={labelStyle as React.CSSProperties}>Indices</span>
                <Checkbox
                  checked={allIndexSelected}
                  onChange={() => setSelectedIndices(allIndexSelected ? [] : [...indexIdentifiers])}
                  style={{ fontSize: 12 }}
                >
                  Select All
                </Checkbox>
              </div>
              <Select
                mode="multiple"
                allowClear
                showSearch
                placeholder="Search by name or identifier..."
                value={selectedIndices}
                onChange={setSelectedIndices}
                options={indexEntries.map((e) => ({ label: e.name, value: e.identifier }))}
                filterOption={(input, option) =>
                  `${option?.label} ${option?.value}`.toLowerCase().includes(input.toLowerCase())
                }
                style={{ width: '100%' }}
                maxTagCount="responsive"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 22, marginBottom: 4 }}>
                <span style={labelStyle as React.CSSProperties}>Underlying Funds</span>
                <Checkbox
                  checked={allUnderlyingSelected}
                  onChange={() => setSelectedUnderlying(allUnderlyingSelected ? [] : [...rdgffIdentifiers])}
                  style={{ fontSize: 12 }}
                >
                  Select All
                </Checkbox>
              </div>
              <Select
                mode="multiple"
                allowClear
                showSearch
                placeholder="Search by name or identifier..."
                value={selectedUnderlying}
                onChange={setSelectedUnderlying}
                options={rdgffEntries.map((e) => ({ label: e.name, value: e.identifier }))}
                filterOption={(input, option) =>
                  `${option?.label} ${option?.value}`.toLowerCase().includes(input.toLowerCase())
                }
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

      {mutation.error && (
        <Card style={{ marginTop: 16 }}>
          <Typography.Text type="danger" style={{ whiteSpace: 'pre-line' }}>
            {mutation.error instanceof Error ? mutation.error.message : 'Optimization error'}
          </Typography.Text>
        </Card>
      )}

      {result && !mutation.error && (
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Card title="Optimal Weights">
            <PlotlyChart
              data={result.plotly_json.data}
              layout={result.plotly_json.layout}
              style={{ height: 400 }}
            />
          </Card>
          <Card title="Portfolio Statistics">
            <Descriptions column={isMobile ? 1 : 4}>
              {Object.entries(result.stats).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>
                  {typeof v === 'number' ? v.toFixed(4) : String(v)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
          <Card title="Weights">
            <Descriptions column={isMobile ? 1 : 4}>
              {Object.entries(result.weights).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>
                  {(v * 100).toFixed(2)}%
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        </Space>
      )}
    </div>
  )
}
