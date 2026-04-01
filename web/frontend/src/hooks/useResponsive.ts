import { Grid } from 'antd'

export function useResponsive() {
  const screens = Grid.useBreakpoint()
  return { isMobile: !screens.md }
}
