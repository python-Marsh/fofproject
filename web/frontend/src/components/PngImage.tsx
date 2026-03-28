import { Button } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'

interface Props {
  src: string
  alt?: string
  downloadName?: string
}

export default function PngImage({ src, alt = 'Chart', downloadName }: Props) {
  const handleDownload = () => {
    const a = document.createElement('a')
    a.href = src
    a.download = downloadName || 'chart.png'
    a.click()
  }

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <img src={src} alt={alt} style={{ maxWidth: '100%', borderRadius: 8 }} />
      {downloadName && (
        <Button
          icon={<DownloadOutlined />}
          size="small"
          onClick={handleDownload}
          style={{ position: 'absolute', top: 8, right: 8, opacity: 0.8 }}
        />
      )}
    </div>
  )
}
