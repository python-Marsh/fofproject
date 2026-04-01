import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Spin } from 'antd'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import DataManagement from './pages/DataManagement'

const FundDetail = lazy(() => import('./pages/FundDetail'))
const CumulativeReturns = lazy(() => import('./pages/CumulativeReturns'))
const Correlation = lazy(() => import('./pages/Correlation'))
const MVO = lazy(() => import('./pages/MVO'))

const Loading = () => (
  <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
    <Spin size="large" />
  </div>
)

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/fund/:identifier" element={<FundDetail />} />
          <Route path="/cumulative" element={<CumulativeReturns />} />
          <Route path="/correlation" element={<Correlation />} />
          <Route path="/mvo" element={<MVO />} />
          <Route path="/data" element={<DataManagement />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}
