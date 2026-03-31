import { useNavigate, useLocation } from 'react-router-dom'
import { Menu } from 'antd'
import {
  DashboardOutlined,
  LineChartOutlined,
  HeatMapOutlined,
  PieChartOutlined,
  DatabaseOutlined,
} from '@ant-design/icons'

const items = [
  { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
  { key: '/cumulative', icon: <LineChartOutlined />, label: 'Cumulative Returns' },
  { key: '/correlation', icon: <HeatMapOutlined />, label: 'Correlation' },
  { key: '/mvo', icon: <PieChartOutlined />, label: 'MVO' },
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data Management' },
]

export default function TopBar() {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <div
      style={{
        background: '#ffffff',
        borderBottom: '1px solid #e8e0d6',
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}
    >
<div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '20px 40px 8px',
          gap: 12,
          position: 'relative',
        }}
      >
        <h1
          style={{
            fontSize: 42,
            fontWeight: 700,
            color: '#2c2c2c',
            letterSpacing: 6,
            textTransform: 'uppercase',
            margin: 0,
          }}
        >
          FOF Workstation
        </h1>
        <img
          src="/profile-dark.png"
          alt="River Delta"
          style={{ height: 72, objectFit: 'contain' }}
        />
      </div>
      <div style={{ height: 2, background: '#c1ae94', margin: '0 40px' }} />
      <Menu
        mode="horizontal"
        selectedKeys={[location.pathname]}
        items={items}
        onClick={({ key }) => navigate(key)}
        style={{
          background: 'transparent',
          borderBottom: 'none',
          padding: '0 32px',
          fontWeight: 500,
          justifyContent: 'center',
        }}
      />
    </div>
  )
}
