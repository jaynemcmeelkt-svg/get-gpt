import { describe, expect, it } from 'vitest'
import { filterSelectableOptions } from './search'

describe('filterSelectableOptions', () => {
  const options = [
    { id: 'dr', name: 'Discord' },
    { id: 'tg', name: 'Telegram' },
    { id: '6', name: '印度尼西亚', min_price: 1.2, max_price: 2.4, total_count: 10 },
  ]

  it('returns all options when keyword is empty', () => {
    expect(filterSelectableOptions(options, '')).toEqual(options)
  })

  it('matches by id and name case-insensitively', () => {
    expect(filterSelectableOptions(options, 'DIS')).toEqual([{ id: 'dr', name: 'Discord' }])
    expect(filterSelectableOptions(options, '6')).toEqual([
      { id: '6', name: '印度尼西亚', min_price: 1.2, max_price: 2.4, total_count: 10 },
    ])
  })

  it('returns empty array when no option matches', () => {
    expect(filterSelectableOptions(options, 'unknown')).toEqual([])
  })
})
