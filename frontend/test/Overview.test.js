import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Overview from '../src/views/Overview.vue'
import { api } from '../src/api.js'
import { viewStubs } from './viewStubs.js'


const mocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('../src/api.js', () => ({ api: { request: vi.fn() } }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal()),
  useMessage: () => mocks,
}))

const loadedOverview = {
  has_cache: true,
  stale: false,
  node_count: 12,
  content_format: 'yaml',
  last_success_source: 'protocol',
  protocol_last_login_at: 1_800_000_000,
  protocol_last_subscribe_at: 1_800_000_001,
  protocol_subscription_expires_at: 1_900_000_000,
  protocol_last_error_category: null,
  consecutive_failures: 0,
}

function formatDate(value) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(new Date(value * 1000))
}

describe('Overview', () => {
  beforeEach(() => {
    api.request.mockReset()
    mocks.success.mockReset()
    mocks.error.mockReset()
    mocks.warning.mockReset()
  })

  it('shows redacted V2Board source timing facts', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({
        enabled: true,
        interval_seconds: 600,
        night_enabled: false,
        night_interval_seconds: 600,
        night_start_hour: 0,
        night_end_hour: 8,
        timeout_seconds: 5,
        checked_at: 1_800_000_000,
        total: 3,
        online: 2,
        nodes: [
          { name: 'Node A', ok: true, latency_ms: 120.5, checked_at: 1_800_000_000 },
          { name: 'Dead Node', ok: false, latency_ms: null, checked_at: 1_800_000_000 },
        ],
      })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    const text = wrapper.text()
    expect(api.request).toHaveBeenCalledWith('/api/admin/overview')
    expect(text).toContain('V2Board 协议')
    expect(text).toContain('最近协议登录')
    expect(text).toContain(formatDate(loadedOverview.protocol_last_login_at))
    expect(text).toContain('最近协议订阅')
    expect(text).toContain(formatDate(loadedOverview.protocol_last_subscribe_at))
    expect(text).toContain('协议订阅到期')
    expect(text).toContain(formatDate(loadedOverview.protocol_subscription_expires_at))
    expect(text).not.toContain('URL')
    expect(text).not.toContain('token')
    expect(text).not.toContain('机场流量')
    expect(text).not.toContain('用量到期')
  })

  it('shows cached subscription-userinfo traffic and expiry', async () => {
    api.request
      .mockResolvedValueOnce({
        ...loadedOverview,
        subscription_usage: {
          upload: 1073741824,
          download: 2147483648,
          used: 3221225472,
          total: 107374182400,
          expire_at: 1_900_000_000,
        },
      })
      .mockResolvedValueOnce({
        enabled: false,
        interval_seconds: 600,
        night_enabled: false,
        night_interval_seconds: 600,
        night_start_hour: 0,
        night_end_hour: 8,
        timeout_seconds: 5,
        checked_at: null,
        total: 0,
        online: 0,
        nodes: [],
      })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('机场流量')
    expect(text).toContain('3.00 GiB / 100.00 GiB')
    expect(text).toContain('用量到期')
    expect(text).toContain(formatDate(1_900_000_000))
  })

  it('shows node health summary and offline nodes', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({
        enabled: true,
        interval_seconds: 600,
        night_enabled: false,
        night_interval_seconds: 600,
        night_start_hour: 0,
        night_end_hour: 8,
        timeout_seconds: 5,
        checked_at: 1_800_000_000,
        total: 2,
        online: 1,
        nodes: [
          { name: 'OK Node', ok: true, latency_ms: 80, checked_at: 1_800_000_000 },
          { name: 'Dead Node', ok: false, latency_ms: null, checked_at: 1_800_000_000 },
        ],
      })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('节点健康')
    expect(text).toContain('1 / 2')
    expect(text).toContain('Dead Node')
    expect(text).toContain('最近检查失败的节点（1）')
  })

  it('shows the night schedule when reduced night frequency is enabled', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({
        enabled: true,
        interval_seconds: 60,
        night_enabled: true,
        night_interval_seconds: 600,
        night_start_hour: 0,
        night_end_hour: 8,
        timeout_seconds: 5,
        checked_at: 1_800_000_000,
        total: 2,
        online: 2,
        nodes: [],
      })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    expect(wrapper.text()).toContain('60 秒')
    expect(wrapper.text()).toContain('夜间 0:00–8:00 为 600 秒')
  })

  it('prefers backup-node status over stale cache', async () => {
    api.request
      .mockResolvedValueOnce({
        ...loadedOverview,
        has_cache: false,
        stale: true,
        consecutive_failures: 3,
        backup_active: true,
        backup_configured: true,
        backup_node_count: 2,
      })
      .mockResolvedValueOnce({ enabled: false, nodes: [] })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('正在使用备用节点')
    expect(wrapper.text()).toContain('2')
    expect(wrapper.text()).not.toContain('缓存陈旧但仍可用')
    expect(wrapper.text()).not.toContain('无可用缓存')
  })

  it('shows loopback converter diagnostics', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({ enabled: false, nodes: [] })
      .mockResolvedValueOnce({
        available: true,
        version: '1.9.4',
        commit: '2a0fde4',
        statistics: {
          uptime_seconds: 7200,
          day: { subscription_requests: 10, rule_conversions: 606 },
          lifetime: { subscription_requests: 40, rule_conversions: 1234 },
          failed: 1,
          rejected: 2,
        },
      })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    const text = wrapper.text()
    expect(api.request).toHaveBeenCalledWith('/api/admin/converter/diagnostics')
    expect(text).toContain('转换服务')
    expect(text).toContain('1.9.4')
    expect(text).toContain('2a0fde4')
    expect(text).toContain('2 小时 0 分')
    expect(text).toContain('606')
    expect(text).toContain('1 / 2')
  })

  it('keeps the overview usable when converter diagnostics fail', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({ enabled: false, nodes: [] })
      .mockRejectedValueOnce(new Error('converter offline'))
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    expect(wrapper.text()).toContain('回环转换服务未响应')
    expect(wrapper.text()).toContain('缓存健康')
    expect(wrapper.text()).not.toContain('无法读取运行状态')
  })

  it('reports statistics disabled upstream', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({ enabled: false, nodes: [] })
      .mockResolvedValueOnce({ available: true, version: null, commit: null, statistics: null })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    expect(wrapper.text()).toContain('上游统计未开启')
  })

  it('warns when health checking is disabled', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({
        enabled: false,
        interval_seconds: 600,
        night_enabled: false,
        night_interval_seconds: 600,
        night_start_hour: 0,
        night_end_hour: 8,
        timeout_seconds: 5,
        checked_at: null,
        total: 0,
        online: 0,
        nodes: [],
      })
    const wrapper = mount(Overview, { global: { stubs: viewStubs } })
    await flushPromises()

    expect(wrapper.text()).toContain('健康检查当前未开启')
  })
})
