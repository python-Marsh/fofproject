import { useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu } from 'antd'
import {
  DashboardOutlined,
  LineChartOutlined,
  HeatMapOutlined,
  PieChartOutlined,
  EditOutlined,
  DatabaseOutlined,
} from '@ant-design/icons'

const { Sider } = Layout

const items = [
  { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
  { key: '/cumulative', icon: <LineChartOutlined />, label: 'Cumulative Returns' },
  { key: '/correlation', icon: <HeatMapOutlined />, label: 'Correlation' },
  { key: '/mvo', icon: <PieChartOutlined />, label: 'MVO' },
  { key: '/overwrite', icon: <EditOutlined />, label: 'Manual Overwrite' },
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data Management' },
]

export default function Sidebar() {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <Sider
      width={220}
      style={{
        background: '#ffffff',
        borderRight: '1px solid #e8e0d6',
        height: 'calc(100vh - 96px)',
        position: 'sticky',
        top: 96,
        overflow: 'auto',
      }}
    >
      <div style={{ padding: '24px 0 8px' }}>
        <div
          style={{
            padding: '0 24px 16px',
            fontSize: 11,
            fontWeight: 600,
            color: '#989A9C',
            letterSpacing: 1.5,
            textTransform: 'uppercase',
          }}
        >
          Navigation
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{
            background: 'transparent',
            borderRight: 'none',
            color: '#53565A',
            fontWeight: 500,
          }}
        />
      </div>
    </Sider>
  )
}
