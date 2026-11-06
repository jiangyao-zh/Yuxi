export function formatFieldValue(value) {
  if (Array.isArray(value)) return value.filter((item) => item != null && String(item).trim()).join('\n')
  if (value == null) return ''
  return String(value)
}

export function parseDraft(value, multiple) {
  if (multiple) {
    return String(value || '')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
  }
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function initialDraft(item) {
  if (item.user_value != null) return formatFieldValue(item.user_value)
  if (item.status === 'conflict' || item.status === 'invalid_format' || item.status === 'missing') return ''
  return formatFieldValue(item.extracted_value)
}

export function valuesFromDrafts(items, drafts) {
  return Object.fromEntries(
    items.map((item) => [
      item.field_key,
      parseDraft(drafts[item.field_key], Boolean(item.multiple))
    ])
  )
}

export function validateDrafts(items, drafts) {
  const errors = []
  for (const item of items) {
    const value = parseDraft(drafts[item.field_key], Boolean(item.multiple))
    const empty = item.multiple ? value.length === 0 : !value
    if (item.required && empty) errors.push(`${item.label}为必填`)
  }
  return errors
}
