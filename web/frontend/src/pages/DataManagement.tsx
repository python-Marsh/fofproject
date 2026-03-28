import { useState } from 'react'
import { Card, Button, Tag, Table, Typography, Space, message } from 'antd'
import { ReloadOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { getStatus, reloadData } from '../api/client'

const { Title, Text } = Typography

export default function DataManagement() {
  const [reloading, setReloading] = useState(false)
  const { data, refetch } = useQuery({
    queryKey: ['status'],
    queryFn: getStatus,
  })

  const handleReload = async () => {
    setReloading(true)
    try {
      await reloadData()
      await refetch()
      message.success('Data reloaded successfully')
    } catch (e: unknown) {
      message.error(`Reload failed: ${e instanceof Error ? e.message : 'Unknown error'}`)
    } finally {
      setReloading(false)
    }
  }

  const columns = [
    { title: '#', render: (_: unknown, __: unknown, i: number) => i + 1, width: 50 },
    { title: 'Fund Name', dataIndex: 'name', key: 'name' },
  ]

  const tableData = (data?.fund_names || []).map((n, i) => ({ key: i, name: n }))

  return (
    <div>
      <Title level={3}>Data Management</Title>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Card>
          <Space size="large" align="center">
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

        <Card title={`Loaded Funds (${tableData.length})`}>
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
