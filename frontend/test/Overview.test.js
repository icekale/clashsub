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
        interval_minutes: 10,
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
  })

  it('shows node health summary and offline nodes', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({
        enabled: true,
        interval_minutes: 10,
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

  it('warns when health checking is disabled', async () => {
    api.request
      .mockResolvedValueOnce(loadedOverview)
      .mockResolvedValueOnce({
        enabled: false,
        interval_minutes: 10,
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
