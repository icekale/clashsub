export const CLASH_SHARE_KINDS = ['raw', 'clash', 'clash-ha', 'surge', 'loon', 'quanx', 'surfboard', 'singbox', 'smart']

const KIND_LABELS = {
  raw: '原始',
  clash: 'Clash',
  'clash-ha': '健康节点',
  surge: 'Surge',
  loon: 'Loon',
  quanx: 'Quantumult X',
  surfboard: 'Surfboard',
  singbox: 'sing-box',
  smart: '智能',
}

// 参数只对走转换器的路由生效：/raw 直出、/clash-ha 本地过滤都不看查询串。
export const CONVERTER_KINDS = ['clash', 'surge', 'loon', 'quanx', 'surfboard', 'singbox', 'smart']

// 与后端 converter.BOOLEAN_PARAMS 白名单一一对应（tests/test_multi_format_subscriptions.py 会
// 校验两边不漂移）；空串表示不传该参数，跟随上游默认。
export const CONVERT_PARAMS = [
  { key: 'emoji', label: 'Emoji', hint: '节点名称 Emoji 总开关' },
  { key: 'list', label: '仅节点列表', hint: '不生成策略组与规则' },
  { key: 'sort', label: '节点名排序', hint: '按名称排序节点' },
  { key: 'fdn', label: '过滤已弃用节点', hint: '去掉流量/到期信息节点' },
  { key: 'udp', label: 'UDP', hint: '覆盖目标的 UDP 选项' },
  { key: 'tfo', label: 'TCP Fast Open', hint: '覆盖目标的 TFO 选项' },
  { key: 'scv', label: '跳过证书验证', hint: '会降低安全性', risk: true },
  { key: 'new_name', label: 'Clash 新字段名', hint: 'Mihomo 路径强制使用新字段' },
  { key: 'append_type', label: '名称追加类型', hint: '节点名后追加协议类型' },
  { key: 'ver', label: 'Surge 版本', hint: '仅 Surge 输出生效', choices: ['2', '3', '4'] },
]

const DEFAULT_CHOICES = [
  { value: '', label: '默认' },
  { value: 'true', label: '开' },
  { value: 'false', label: '关' },
]

export function paramChoices(param) {
  return (param.choices || []).length
    ? [{ value: '', label: '默认' }, ...param.choices.map((value) => ({ value, label: value }))]
    : DEFAULT_CHOICES
}

export function paramsQuery(values) {
  return CONVERT_PARAMS.map((param) => {
    const value = String(values?.[param.key] ?? '').trim()
    return value ? `${param.key}=${encodeURIComponent(value)}` : ''
  })
    .filter(Boolean)
    .join('&')
}

export function withParams(url, values) {
  const query = paramsQuery(values)
  if (!url || !query) return url
  return `${url}${url.includes('?') ? '&' : '?'}${query}`
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

export async function copyText(value) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch (_) {
    /* fall through to execCommand */
  }
  const field = document.createElement('textarea')
  field.value = value
  field.readOnly = true
  field.style.position = 'fixed'
  field.style.opacity = '0'
  document.body.appendChild(field)
  field.select()
  const copied = Boolean(document.execCommand?.('copy'))
  field.remove()
  return copied
}
