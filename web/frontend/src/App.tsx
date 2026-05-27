import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { Spin } from 'antd'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import DataManagement from './pages/DataManagement'

const FundDetail = lazy(() => import('./pages/FundDetail'))
const CumulativeReturns = lazy(() => import('./pages/CumulativeReturns'))
const Correlation = lazy(() => import('./pages/Correlation'))
const MVO = lazy(() => import('./pages/MVO'))
const BirthdayLottery = lazy(() => import('./pages/BirthdayLottery'))

const Loading = () => (
  <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
    <Spin size="large" />
  </div>
)

function LayoutWrapper() {
  return (
    <Layout>
      <Suspense fallback={<Loading />}>
        <Outlet />
      </Suspense>
    </Layout>
  )
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        {/* Standalone birthday page – no Layout wrapper */}
        <Route path="/drleevenhappybirthday" element={<BirthdayLottery />} />

        {/* Main app routes wrapped in Layout */}
        <Route element={<LayoutWrapper />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/fund/:identifier" element={<FundDetail />} />
          <Route path="/cumulative" element={<CumulativeReturns />} />
          <Route path="/correlation" element={<Correlation />} />
          <Route path="/mvo" element={<MVO />} />
          <Route path="/data" element={<DataManagement />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
