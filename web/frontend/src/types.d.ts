declare module 'react-plotly.js' {
  import { Component } from 'react'

  interface PlotParams {
    data: object[]
    layout?: object
    config?: object
    style?: React.CSSProperties
    useResizeHandler?: boolean
    onInitialized?: (figure: object, graphDiv: HTMLElement) => void
    onUpdate?: (figure: object, graphDiv: HTMLElement) => void
  }

  export default class Plot extends Component<PlotParams> {}
}

declare module 'react-plotly.js/factory' {
  import { Component } from 'react'
  interface PlotParams {
    data: object[]
    layout?: object
    config?: object
    style?: React.CSSProperties
    useResizeHandler?: boolean
  }
  export default function createPlotlyComponent(plotly: object): new () => Component<PlotParams>
}

declare module 'plotly.js-cartesian-dist-min' {
  const Plotly: object
  export default Plotly
}
