import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import App from './App'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        theme={{
          token: {
            fontFamily: '"Segoe UI", Roboto, "Noto Sans HK", -apple-system, BlinkMacSystemFont, sans-serif',
            colorPrimary: '#9a856b',
            colorBgContainer: '#ffffff',
            colorBgLayout: '#f5f2ee',
            colorText: '#2c2c2c',
            colorTextSecondary: '#888888',
            colorBorder: '#e0d8cf',
            colorBorderSecondary: '#ece6de',
            borderRadius: 8,
            colorLink: '#9a856b',
            colorLinkHover: '#7a6a55',
          },
          components: {
            Menu: {
              itemBg: 'transparent',
              itemSelectedBg: '#f5f2ee',
              itemSelectedColor: '#9a856b',
              itemColor: '#53565A',
              itemHoverColor: '#9a856b',
              itemHoverBg: '#faf8f5',
              activeBarBorderWidth: 0,
              itemBorderRadius: 6,
            },
            Table: {
              headerBg: '#faf8f5',
              headerColor: '#53565A',
              rowHoverBg: '#faf8f5',
              borderColor: '#ece6de',
            },
            Card: {
              headerBg: 'transparent',
              borderRadiusLG: 8,
            },
            Tabs: {
              inkBarColor: '#9a856b',
              itemActiveColor: '#9a856b',
              itemSelectedColor: '#9a856b',
              itemHoverColor: '#c1ae94',
            },
            Tag: {
              defaultBg: '#f5f2ee',
              defaultColor: '#53565A',
              borderRadiusSM: 4,
            },
            Button: {
              primaryColor: '#ffffff',
              colorPrimaryHover: '#7a6a55',
              borderRadius: 6,
            },
            Input: {
              activeBorderColor: '#9a856b',
              hoverBorderColor: '#c1ae94',
            },
            Select: {
              optionSelectedBg: '#f5f2ee',
            },
            Descriptions: {
              labelBg: '#faf8f5',
            },
          },
        }}
      >
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
