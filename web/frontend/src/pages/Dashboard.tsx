import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Table, Typography, Input, Tag, Space, Dropdown, Button, Checkbox } from 'antd'
import { SettingOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { listFunds, type FundRow } from '../api/client'
import { useResponsive } from '../hooks/useResponsive'

const { Title } = Typography
const { Search } = Input

const pct = (v: number | null | undefined) =>
  v != null ? `${(v * 100).toFixed(2)}%` : '—'

const num = (v: number | null | undefined, digits = 2) =>
  v != null ? v.toFixed(digits) : '—'

interface ColDef {
  key: string
  title: string
  dataIndex: string
  width: number
  defaultVisible: boolean
  render?: (v: unknown, row: FundRow) => React.ReactNode
  sorter?: (a: FundRow, b: FundRow) => number
  fixed?: 'left' | 'right'
  ellipsis?: boolean
  defaultSortOrder?: 'ascend' | 'descend'
}

const ALL_COLUMNS: ColDef[] = [
  {
    key: 'Name',
    title: 'Name',
    dataIndex: 'Name',
    width: 160,
    defaultVisible: true,
    fixed: 'left',
    sorter: (a, b) => (a.Name || '').localeCompare(b.Name || ''),
    render: (name) => <a style={{ fontWeight: 600, color: '#9a856b' }}>{name as string}</a>,
  },
  {
    key: 'Identifier',
    title: 'Identifier',
    dataIndex: 'Identifier',
    width: 140,
    defaultVisible: true,
    ellipsis: true,
  },
  {
    key: 'One Liner',
    title: 'Description',
    dataIndex: 'One Liner',
    width: 240,
    defaultVisible: false,
    ellipsis: true,
  },
  {
    key: 'Geo Focus',
    title: 'Geo Focus',
    dataIndex: 'Geo Focus',
    width: 120,
    defaultVisible: false,
    render: (v) => (v as string) || '—',
  },
  {
    key: 'Strategy',
    title: 'Strategy',
    dataIndex: 'Strategy',
    width: 140,
    defaultVisible: true,
    render: (v) => {
      if (!v) return '—'
      const items = Array.isArray(v) ? v : [v]
      return (
        <Space size={2} wrap>
          {items.map((s: string, i: number) => (
            <Tag key={i} style={{ margin: 0 }}>{s}</Tag>
          ))}
        </Space>
      )
    },
  },
  {
    key: 'Asset Class',
    title: 'Asset Class',
    dataIndex: 'Asset Class',
    width: 140,
    defaultVisible: false,
    render: (v) => {
      if (!v) return '—'
      const items = Array.isArray(v) ? v : [v]
      return (
        <Space size={2} wrap>
          {items.map((s: string, i: number) => (
            <Tag key={i} style={{ margin: 0 }}>{s}</Tag>
          ))}
        </Space>
      )
    },
  },
  {
    key: 'IR Contact',
    title: 'IR Contact',
    dataIndex: 'IR Contact',
    width: 200,
    defaultVisible: false,
    ellipsis: true,
  },
  {
    key: 'AUM (in Mn USD)',
    title: 'AUM ($M)',
    dataIndex: 'AUM (in Mn USD)',
    width: 100,
    defaultVisible: true,
    sorter: (a, b) => (a['AUM (in Mn USD)'] ?? 0) - (b['AUM (in Mn USD)'] ?? 0),
    render: (v) => (v != null ? (v as number).toFixed(0) : '—'),
  },
  {
    key: 'Net Exposure',
    title: 'Net Exposure',
    dataIndex: 'Net Exposure',
    width: 160,
    defaultVisible: false,
    ellipsis: true,
  },
  {
    key: 'Net Return',
    title: 'Net Return',
    dataIndex: 'Net Return',
    width: 100,
    defaultVisible: false,
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Mgmt Fee',
    title: 'Mgmt Fee',
    dataIndex: 'Mgmt Fee',
    width: 100,
    defaultVisible: false,
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Perf Fee',
    title: 'Perf Fee',
    dataIndex: 'Perf Fee',
    width: 100,
    defaultVisible: false,
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Inception Date',
    title: 'Inception',
    dataIndex: 'Inception Date',
    width: 110,
    defaultVisible: false,
    sorter: (a, b) => (a['Inception Date'] || '').localeCompare(b['Inception Date'] || ''),
  },
  {
    key: 'Latest Date',
    title: 'Latest',
    dataIndex: 'Latest Date',
    width: 100,
    defaultVisible: false,
    sorter: (a, b) => (a['Latest Date'] || '').localeCompare(b['Latest Date'] || ''),
  },
  {
    key: 'Month Running',
    title: 'Month Running',
    dataIndex: 'Month Running',
    width: 120,
    defaultVisible: false,
    sorter: (a, b) => (a['Month Running'] ?? 0) - (b['Month Running'] ?? 0),
    render: (v) => (v != null ? (v as number).toFixed(0) : '—'),
  },
  {
    key: '# Months',
    title: 'Months',
    dataIndex: '# Months',
    width: 80,
    defaultVisible: true,
    sorter: (a, b) => (a['# Months'] ?? 0) - (b['# Months'] ?? 0),
  },
  {
    key: 'Cumulative Return',
    title: 'Cum. Return',
    dataIndex: 'Cumulative Return',
    width: 110,
    defaultVisible: false,
    sorter: (a, b) => (a['Cumulative Return'] ?? -999) - (b['Cumulative Return'] ?? -999),
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Annualized Return',
    title: 'Ann. Return',
    dataIndex: 'Annualized Return',
    width: 110,
    defaultVisible: true,
    sorter: (a, b) => (a['Annualized Return'] ?? -999) - (b['Annualized Return'] ?? -999),
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Volatility',
    title: 'Volatility',
    dataIndex: 'Volatility',
    width: 100,
    defaultVisible: true,
    sorter: (a, b) => (a.Volatility ?? 999) - (b.Volatility ?? 999),
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Sharpe Ratio',
    title: 'Sharpe',
    dataIndex: 'Sharpe Ratio',
    width: 90,
    defaultVisible: true,
    defaultSortOrder: 'descend',
    sorter: (a, b) => (a['Sharpe Ratio'] ?? -999) - (b['Sharpe Ratio'] ?? -999),
    render: (v) => num(v as number | null),
  },
  {
    key: 'Sortino Ratio',
    title: 'Sortino',
    dataIndex: 'Sortino Ratio',
    width: 90,
    defaultVisible: true,
    sorter: (a, b) => (a['Sortino Ratio'] ?? -999) - (b['Sortino Ratio'] ?? -999),
    render: (v) => num(v as number | null),
  },
  {
    key: 'Max Drawdown',
    title: 'Max DD',
    dataIndex: 'Max Drawdown',
    width: 90,
    defaultVisible: true,
    sorter: (a, b) => (a['Max Drawdown'] ?? 999) - (b['Max Drawdown'] ?? 999),
    render: (v) =>
      v != null ? (
        <span style={{ color: (v as number) < -0.2 ? '#a0522d' : undefined }}>
          {pct(v as number)}
        </span>
      ) : '—',
  },
  {
    key: 'Positive Months',
    title: 'Win Rate',
    dataIndex: 'Positive Months',
    width: 90,
    defaultVisible: true,
    sorter: (a, b) => (a['Positive Months'] ?? 0) - (b['Positive Months'] ?? 0),
    render: (v) => pct(v as number | null),
  },
  {
    key: 'Capture Ratio',
    title: 'Capture Ratio',
    dataIndex: 'Capture Ratio',
    width: 120,
    defaultVisible: false,
    sorter: (a, b) => (a['Capture Ratio'] ?? 0) - (b['Capture Ratio'] ?? 0),
    render: (v) => num(v as number | null),
  },
]

const DEFAULT_VISIBLE = new Set(ALL_COLUMNS.filter((c) => c.defaultVisible).map((c) => c.key))

export default function Dashboard() {
  const navigate = useNavigate()
  const { isMobile } = useResponsive()
  const [search, setSearch] = useState('')
  const [visibleKeys, setVisibleKeys] = useState<Set<string>>(DEFAULT_VISIBLE)
  const { data, isLoading } = useQuery({
    queryKey: ['funds'],
    queryFn: () => listFunds(),
  })

  const funds = data?.funds || []

  const filtered = useMemo(() => {
    if (!search) return funds
    const q = search.toLowerCase()
    return funds.filter(
      (f) =>
        f.Name?.toLowerCase().includes(q) ||
        (typeof f.Identifier === 'string' && f.Identifier.toLowerCase().includes(q)) ||
        f['One Liner']?.toLowerCase().includes(q) ||
        (typeof f.Strategy === 'string' && f.Strategy.toLowerCase().includes(q))
    )
  }, [funds, search])

  const columns: ColumnsType<FundRow> = ALL_COLUMNS
    .filter((c) => visibleKeys.has(c.key))
    .map((c) => ({
      title: c.title,
      dataIndex: c.dataIndex,
      width: c.width,
      fixed: c.fixed,
      ellipsis: c.ellipsis,
      defaultSortOrder: c.defaultSortOrder,
      sorter: c.sorter as ColumnsType<FundRow>[number]['sorter'],
      render: c.render as ColumnsType<FundRow>[number]['render'],
      onHeaderCell: () => ({ style: { whiteSpace: 'nowrap' as const } }),
    }))

  const toggleColumn = (key: string) => {
    setVisibleKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const columnMenu = {
    items: ALL_COLUMNS.map((c) => ({
      key: c.key,
      label: (
        <Checkbox
          checked={visibleKeys.has(c.key)}
          onChange={() => toggleColumn(c.key)}
          style={{ width: '100%' }}
        >
          {c.title}
        </Checkbox>
      ),
    })),
  }

  return (
    <div>
      <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', justifyContent: 'space-between', alignItems: isMobile ? 'stretch' : 'center', gap: isMobile ? 8 : 0, marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0, color: '#2c2c2c' }}>Fund Dashboard</Title>
        <Space style={{ width: isMobile ? '100%' : undefined }}>
          <Search
            placeholder="Search funds..."
            allowClear
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: isMobile ? '100%' : 300 }}
          />
          <Dropdown menu={columnMenu} trigger={['click']} placement="bottomRight">
            <Button icon={<SettingOutlined />}>Columns</Button>
          </Dropdown>
        </Space>
      </div>
      <Card bodyStyle={{ padding: 0 }}>
        <Table
          columns={columns}
          dataSource={filtered}
          rowKey="Identifier"
          loading={isLoading}
          size="small"
          scroll={{ x: 1200, y: 'calc(100vh - 300px)' }}
          sticky
          pagination={{ pageSize: 50, showSizeChanger: true }}
          onRow={(record) => ({
            onClick: () => navigate(`/fund/${encodeURIComponent(record.Identifier as string)}`),
            style: { cursor: 'pointer' },
          })}
        />
      </Card>
    </div>
  )
}
