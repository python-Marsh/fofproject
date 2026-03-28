import Plotly from 'plotly.js-cartesian-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'

const Plot = createPlotlyComponent(Plotly)

interface Props {
  data: object[]
  layout: object
  style?: React.CSSProperties
}

export default function PlotlyChart({ data, layout, style }: Props) {
  // Strip fixed width/height from backend layout so autosize works
  const { width, height, ...restLayout } = layout as Record<string, unknown>

  return (
    <Plot
      data={data}
      layout={{
        ...restLayout,
        autosize: true,
      }}
      useResizeHandler
      style={{ width: '100%', height: '100%', ...style }}
      config={{
        responsive: true,
        displaylogo: false,
        toImageButtonOptions: { format: 'png', scale: 2 },
      }}
    />
  )
}
