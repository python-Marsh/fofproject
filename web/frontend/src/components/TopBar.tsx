import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Menu, Button, Drawer } from 'antd'
import {
  DashboardOutlined,
  LineChartOutlined,
  HeatMapOutlined,
  PieChartOutlined,
  DatabaseOutlined,
  MenuOutlined,
} from '@ant-design/icons'
import { useResponsive } from '../hooks/useResponsive'

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
  const { isMobile } = useResponsive()
  const [drawerOpen, setDrawerOpen] = useState(false)

  const handleNav = (key: string) => {
    navigate(key)
    setDrawerOpen(false)
  }

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
          padding: isMobile ? '12px 16px 6px' : '20px 40px 8px',
          gap: isMobile ? 8 : 12,
          position: 'relative',
        }}
      >
        {isMobile && (
          <Button
            type="text"
            icon={<MenuOutlined />}
            onClick={() => setDrawerOpen(true)}
            style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }}
          />
        )}
        <h1
          style={{
            fontSize: isMobile ? 20 : 42,
            fontWeight: 700,
            color: '#2c2c2c',
            letterSpacing: isMobile ? 2 : 6,
            textTransform: 'uppercase',
            margin: 0,
          }}
        >
          FOF Workstation
        </h1>
        <img
          src="/profile-dark.png"
          alt="River Delta"
          style={{ height: isMobile ? 36 : 72, objectFit: 'contain' }}
        />
      </div>
      <div style={{ height: 2, background: '#c1ae94', margin: isMobile ? '0 16px' : '0 40px' }} />

      {isMobile ? (
        <Drawer
          placement="left"
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          width={260}
          styles={{ body: { padding: 0 } }}
        >
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            items={items}
            onClick={({ key }) => handleNav(key)}
            style={{ borderRight: 'none' }}
          />
        </Drawer>
      ) : (
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
      )}
    </div>
  )
}
