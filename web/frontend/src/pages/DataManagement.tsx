import { useState } from 'react'
import { Card, Button, Tag, Table, Typography, Space, Descriptions, message } from 'antd'
import { ReloadOutlined, CheckCircleOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { getStatus, reloadData, listFunds } from '../api/client'
import { useResponsive } from '../hooks/useResponsive'

const { Title, Text } = Typography

export default function DataManagement() {
  const { isMobile } = useResponsive()
  const [reloading, setReloading] = useState(false)
  const { data, refetch } = useQuery({
    queryKey: ['status'],
    queryFn: getStatus,
  })

  const handleReload = async () => {
    setReloading(true)
    try {
      await reloadData()
      await Promise.all([refetch(), refetchFunds()])
      message.success('Data reloaded successfully')
    } catch (e: unknown) {
      message.error(`Reload failed: ${e instanceof Error ? e.message : 'Unknown error'}`)
    } finally {
      setReloading(false)
    }
  }

  const { data: fundsData, refetch: refetchFunds } = useQuery({
    queryKey: ['funds'],
    queryFn: () => listFunds(),
  })

  const columns = [
    { title: '#', render: (_: unknown, __: unknown, i: number) => i + 1, width: 50 },
    { title: 'Fund Name', dataIndex: 'Name', key: 'Name' },
    { title: 'Identifier', dataIndex: 'Identifier', key: 'Identifier' },
    { title: 'Source', dataIndex: 'Source', key: 'Source',
      filters: [...new Set((fundsData?.funds || []).map((f) => f.Source as string))].filter(Boolean).map((s) => ({ text: s, value: s })),
      onFilter: (value: unknown, record: Record<string, unknown>) => record.Source === value,
    },
  ]

  const tableData = (fundsData?.funds || []).map((f, i) => ({ key: i, ...f }))

  return (
    <div>
      <Title level={3}>Data Management</Title>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Card>
          <Space size="large" align="center" direction={isMobile ? 'vertical' : 'horizontal'} style={{ width: '100%' }}>
            <div>
              <Text type="secondary">Status</Text>
              <div>
                <Tag icon={<CheckCircleOutlined />} color="success">
                  {data?.fund_count || 0} funds loaded
                </Tag>
              </div>
            </div>
            <div>
              <Text type="secondary">Last Refresh</Text>
              <div>
                <Text strong>
                  {data?.loaded_at
                    ? new Date(data.loaded_at).toLocaleString()
                    : 'Never'}
                </Text>
              </div>
            </div>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              loading={reloading}
              onClick={handleReload}
              size="large"
            >
              Reload All Data
            </Button>
          </Space>
        </Card>

        {data?.data_paths && Object.keys(data.data_paths).length > 0 && (
          <Card title={<><FolderOpenOutlined /> Data Sources</>}>
            <Descriptions column={1} size="small">
              {Object.entries(data.data_paths).map(([label, path]) => (
                <Descriptions.Item key={label} label={label}>
                  <Text code>{path}</Text>
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        )}

        <Card title={`Loaded Funds (${tableData.length})`} style={{ marginTop: 0 }}>
          <Table
            columns={columns}
            dataSource={tableData}
            size="small"
            pagination={{ pageSize: 50 }}
          />
        </Card>
      </Space>
    </div>
  )
}
