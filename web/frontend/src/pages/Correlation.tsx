import { useMemo, useState } from 'react'
import { Card, Button, Typography, Space, Select, InputNumber, Checkbox } from 'antd'
import { HeatMapOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { chartCorrelation, getStatus } from '../api/client'
import PlotlyChart from '../components/PlotlyChart'
import { useResponsive } from '../hooks/useResponsive'

const { Title } = Typography

export default function Correlation() {
  const { isMobile } = useResponsive()
  const [selectedFunds, setSelectedFunds] = useState<string[]>([])
  const [selectedIndices, setSelectedIndices] = useState<string[]>([])
  const [selectedUnderlying, setSelectedUnderlying] = useState<string[]>([])
  const [method, setMethod] = useState('pearson')
  const [minOverlap, setMinOverlap] = useState(6)

  const { data: status } = useQuery({ queryKey: ['status'], queryFn: getStatus })
  const allEntries = status?.fund_entries || []
  const indexIds = new Set(status?.index_identifiers || [])
  const rdgffIds = new Set(status?.rdgff_identifiers || [])

  const combined = useMemo(
    () => [...new Set([...selectedFunds, ...selectedIndices, ...selectedUnderlying])],
    [selectedFunds, selectedIndices, selectedUnderlying],
  )

  const mutation = useMutation({ mutationFn: chartCorrelation })

  const indexEntries = allEntries.filter((e) => indexIds.has(e.identifier))
  const rdgffEntries = allEntries.filter((e) => rdgffIds.has(e.identifier))
  const fundEntries = allEntries.filter((e) => !indexIds.has(e.identifier) && !rdgffIds.has(e.identifier))
  const indexIdentifiers = indexEntries.map((e) => e.identifier)
  const rdgffIdentifiers = rdgffEntries.map((e) => e.identifier)

  const allIndexSelected = indexIdentifiers.length > 0 && indexIdentifiers.every((id) => selectedIndices.includes(id))
  const allUnderlyingSelected = rdgffIdentifiers.length > 0 && rdgffIdentifiers.every((id) => selectedUnderlying.includes(id))

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
            style={{ height: isMobile ? 350 : 600 }}
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
