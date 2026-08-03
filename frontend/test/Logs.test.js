import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Logs from '../src/views/Logs.vue'
import { api } from '../src/api.js'
import { viewStubs } from './viewStubs.js'


vi.mock('../src/api.js', () => ({ api: { request: vi.fn() } }))

function mountLogs() {
  return mount(Logs, { global: { stubs: viewStubs } })
}

describe('Logs', () => {
  beforeEach(() => api.request.mockReset())

  it('loads 200 redacted lines and supports manual refresh', async () => {
    api.request
      .mockResolvedValueOnce({ lines: ['refresh ok token=[REDACTED]', 'cache unchanged'] })
      .mockResolvedValueOnce({ lines: [] })
    const wrapper = mountLogs()
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(1, '/api/admin/logs?limit=200')
    expect(wrapper.get('pre').text()).toContain('token=[REDACTED]')

    await wrapper.get('[data-testid="refresh-logs"]').trigger('click')
    await flushPromises()
    expect(api.request).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('暂时没有运行日志')
  })

  it('shows a useful error state', async () => {
    api.request.mockRejectedValueOnce(new Error('network unavailable'))
    const wrapper = mountLogs()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('network unavailable')
    expect(wrapper.text()).not.toContain('暂时没有运行日志')
    expect(wrapper.find('.log-panel').exists()).toBe(false)
  })
})
