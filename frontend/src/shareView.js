export const CLASH_SHARE_KINDS = ['raw', 'clash', 'clash-ha', 'surge', 'loon', 'smart']

const KIND_LABELS = {
  raw: '原始',
  clash: 'Clash',
  'clash-ha': '健康节点',
  surge: 'Surge',
  loon: 'Loon',
  smart: '智能',
}

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind
}

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
