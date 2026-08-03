export function buildShareRequest(form) {
  const allowClash = Boolean(form.allowClash)
  return {
    label: String(form.label || '').trim(),
    days: Number(form.days || 365),
    allow_raw: Boolean(form.allowRaw || allowClash),
    allow_clash: allowClash,
  }
}


export function statusLabel(item) {
  if (item.revoked) return '已撤销'
  if (item.expired) return '已过期'
  return '有效'
}
