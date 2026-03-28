import { useState, useEffect } from 'react'
import { Card, Button, Typography, Table, Input, InputNumber, Space, message, Popconfirm } from 'antd'
import { SaveOutlined, PlusOutlined, DeleteOutlined } from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getOverwrite, saveOverwrite } from '../api/client'

const { Title } = Typography

export default function ManualOverwrite() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['overwrite'],
    queryFn: getOverwrite,
  })

  const [headers, setHeaders] = useState<string[]>([])
  const [rows, setRows] = useState<(string | number | null)[][]>([])
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (data) {
      setHeaders(data.headers)
      setRows(data.rows)
      setDirty(false)
    }
  }, [data])

  const saveMutation = useMutation({
    mutationFn: saveOverwrite,
    onSuccess: (res) => {
      message.success(res.message)
      setDirty(false)
      queryClient.invalidateQueries({ queryKey: ['overwrite'] })
      queryClient.invalidateQueries({ queryKey: ['status'] })
      queryClient.invalidateQueries({ queryKey: ['funds'] })
    },
    onError: (err: Error) => message.error(err.message),
  })

  const handleCellChange = (rowIdx: number, colIdx: number, value: string | number | null) => {
    const newRows = rows.map((r) => [...r])
    newRows[rowIdx][colIdx] = value
    setRows(newRows)
    setDirty(true)
  }

  const addRow = () => {
    const newRow = headers.map(() => null as string | number | null)
    setRows([...rows, newRow])
    setDirty(true)
  }

  const deleteRow = (idx: number) => {
    setRows(rows.filter((_, i) => i !== idx))
    setDirty(true)
  }

  const addColumn = () => {
    const name = prompt('Fund name (ALL CAPS):')
    if (!name) return
    setHeaders([...headers, name])
    setRows(rows.map((r) => [...r, null]))
    setDirty(true)
  }

  const handleSave = () => {
    saveMutation.mutate({ headers, rows })
  }

  const columns = [
    {
      title: '#',
      width: 50,
      render: (_: unknown, __: unknown, i: number) => i + 1,
    },
    ...headers.map((h, colIdx) => ({
      title: h,
      key: h,
      width: colIdx === 0 ? 120 : 110,
      render: (_: unknown, __: unknown, rowIdx: number) => {
        const val = rows[rowIdx]?.[colIdx]
        if (colIdx === 0) {
          // date column
          return (
            <Input
              size="small"
              value={val as string || ''}
              onChange={(e) => handleCellChange(rowIdx, colIdx, e.target.value)}
              placeholder="DD/MM/YYYY"
              style={{ width: '100%' }}
            />
          )
        }
        return (
          <InputNumber
            size="small"
            value={val as number}
            onChange={(v) => handleCellChange(rowIdx, colIdx, v)}
            step={0.001}
            style={{ width: '100%' }}
            controls={false}
          />
        )
      },
    })),
    {
      title: '',
      width: 40,
      render: (_: unknown, __: unknown, i: number) => (
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          size="small"
          onClick={() => deleteRow(i)}
        />
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>Manual Overwrite</Title>
        <Space>
          <Button icon={<PlusOutlined />} onClick={addColumn}>Add Fund Column</Button>
          <Button icon={<PlusOutlined />} onClick={addRow}>Add Row</Button>
          <Popconfirm
            title="Save changes?"
            description="This will overwrite MANUAL OVERWRITE.csv and reload all fund data."
            onConfirm={handleSave}
            okText="Save"
          >
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saveMutation.isPending}
              disabled={!dirty}
            >
              Save to CSV
            </Button>
          </Popconfirm>
        </Space>
      </div>
      <Card bodyStyle={{ padding: 0 }}>
        <Table
          columns={columns}
          dataSource={rows.map((_, i) => ({ key: i }))}
          loading={isLoading}
          size="small"
          pagination={false}
          scroll={{ x: headers.length * 120, y: 600 }}
        />
      </Card>
      <Typography.Text type="secondary" style={{ marginTop: 8, display: 'block' }}>
        Format: date column as DD/MM/YYYY, return values as decimals (e.g. 0.05 = 5%).
        Changes are written to MANUAL OVERWRITE.csv and take priority over all other data sources.
      </Typography.Text>
    </div>
  )
}
