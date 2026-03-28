import { ReactNode } from 'react'
import { Layout as AntLayout } from 'antd'
import TopBar from './TopBar'

const { Content } = AntLayout

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <TopBar />
      <Content
        style={{
          margin: 0,
          padding: '32px 40px',
          background: '#f5f2ee',
          minHeight: 'calc(100vh - 96px)',
          overflow: 'auto',
          position: 'relative',
        }}
      >
        <img
          src="/logo-dark.png"
          alt=""
          style={{
            position: 'fixed',
            bottom: 40,
            right: 40,
            height: 120,
            opacity: 0.06,
            pointerEvents: 'none',
            userSelect: 'none',
          }}
        />
        {children}
      </Content>
    </AntLayout>
  )
}
