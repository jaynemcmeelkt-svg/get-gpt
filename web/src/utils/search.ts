export interface SearchableOption {
  id: string | number
  name?: string
}

export function filterSelectableOptions<T extends SearchableOption>(options: T[], keyword: string): T[] {
  const normalized = keyword.trim().toLowerCase()
  if (!normalized) return options

  return options.filter((option) => {
    const idPart = String(option.id).toLowerCase()
    const namePart = String(option.name ?? '').toLowerCase()
    return idPart.includes(normalized) || namePart.includes(normalized)
  })
}
