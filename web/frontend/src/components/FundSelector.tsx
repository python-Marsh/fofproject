import { Select } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { listFunds } from '../api/client'

interface Props {
  value: string[]
  onChange: (val: string[]) => void
  placeholder?: string
  style?: React.CSSProperties
}

export default function FundSelector({ value, onChange, placeholder, style }: Props) {
  const { data } = useQuery({
    queryKey: ['funds'],
    queryFn: () => listFunds(),
  })

  const options = (data?.funds || []).map((f) => ({
    label: f.Name,
    value: f.Identifier,
    searchText: `${f.Name} ${f.Identifier || ''}`.toLowerCase(),
  }))

  return (
    <Select
      mode="multiple"
      allowClear
      showSearch
      placeholder={placeholder || 'Select funds...'}
      value={value}
      onChange={onChange}
      options={options}
      filterOption={(input, option) =>
        (option?.searchText as string)?.includes(input.toLowerCase()) ?? false
      }
      style={{ minWidth: 300, ...style }}
      maxTagCount="responsive"
    />
  )
}
