import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  create,
  NAlert,
  NButton,
  NCheckbox,
  NFormItem,
  NInput,
  NInputNumber,
  NPopconfirm,
  NSkeleton,
  NTag,
} from 'naive-ui'

import Shares from '../src/views/Shares.vue'
import { api } from '../src/api.js'
import { PopconfirmStub, viewStubs } from './viewStubs.js'


const messages = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('../src/api.js', () => ({ api: { request: vi.fn() } }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal()),
  useMessage: () => messages,
}))

const summary = {
  id: 'bdf2d725-3a35-4ee0-a893-50bca5c73d51',
  label: '小林',
  expires_at: 2_000_000_000,
  allow_raw: true,
  allow_clash: false,
  revoked: false,
  expired: false,
  last_access_at: null,
  access_count: 2,
  recoverable: false,
}

const SecretRevealStub = {
  name: 'SecretRevealDialog',
  props: ['show', 'rawUrl', 'clashUrl'],
  template: '<div v-if="show" data-testid="secret-reveal">{{ rawUrl }} {{ clashUrl }}</div>',
}

function mountShares() {
  return mount(Shares, {
    global: {
      stubs: { ...viewStubs, SecretRevealDialog: SecretRevealStub },
    },
  })
}

const naive = create({
  components: [
    NAlert,
    NButton,
    NCheckbox,
    NFormItem,
    NInput,
    NInputNumber,
    NPopconfirm,
    NSkeleton,
    NTag,
  ],
})

function mountSharesWithNaive() {
  return mount(Shares, {
    global: {
      plugins: [naive],
      stubs: { SecretRevealDialog: SecretRevealStub },
    },
  })
}

function buttonWithText(wrapper, text) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

describe('Shares', () => {
  beforeEach(() => {
    api.request.mockReset()
    messages.success.mockReset()
    messages.error.mockReset()
    messages.warning.mockReset()
  })

  it('shows record metadata and automatically restores its raw link', async () => {
    api.request.mockResolvedValueOnce([{
      ...summary,
      recoverable: true,
      urls: { raw: 'https://sub.example.com/raw/stable-token' },
    }])
    const wrapper = mountShares()
    await flushPromises()

    expect(api.request).toHaveBeenCalledWith('/api/admin/shares')
    expect(wrapper.text()).toContain(summary.id)
    expect(wrapper.text()).toContain('链接可恢复')
    expect(wrapper.text()).toContain('有效')
    expect(wrapper.text()).toContain('2033')
    expect(wrapper.text()).toContain('/raw/stable-token')
  })

  it('shows existing links straight from the list response without reveal POSTs', async () => {
    api.request.mockResolvedValueOnce([{
      ...summary,
      allow_clash: true,
      recoverable: true,
      urls: {
        raw: 'https://sub.example.com/raw/inline-token',
        clash: 'https://sub.example.com/clash/inline-token',
        'clash-ha': 'https://sub.example.com/clash-ha/inline-token',
        surge: 'https://sub.example.com/surge/inline-token',
        loon: 'https://sub.example.com/loon/inline-token',
        smart: 'https://sub.example.com/smart/inline-token',
      },
    }])
    const wrapper = mountShares()
    await flushPromises()

    expect(wrapper.text()).toContain('/raw/inline-token')
    expect(wrapper.text()).toContain('/smart/inline-token')
    // 只有列表请求本身，不触发任何 reveal POST。
    expect(api.request).toHaveBeenCalledTimes(1)
    expect(buttonWithText(wrapper, '重新获取链接')).toBeTruthy()
  })

  it('reveals existing links through the CSRF-protected endpoint', async () => {
    api.request
      .mockResolvedValueOnce([{ ...summary, recoverable: true }])
      .mockResolvedValueOnce({ url: 'https://sub.example.com/raw/stable-token' })
    const wrapper = mountShares()
    await flushPromises()

    await buttonWithText(wrapper, '查看全部链接').trigger('click')
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(2, `/api/admin/shares/${summary.id}/reveal`, {
      method: 'POST',
      body: { kind: 'raw' },
    })
    expect(wrapper.text()).toContain('/raw/stable-token')
  })

  it('automatically shows all recoverable historical links for a multi-format share', async () => {
    api.request.mockResolvedValueOnce([{
      ...summary,
      allow_clash: true,
      recoverable: true,
      urls: {
        raw: 'https://sub.example.com/raw/history',
        clash: 'https://sub.example.com/clash/history',
        'clash-ha': 'https://sub.example.com/clash-ha/history',
        surge: 'https://sub.example.com/surge/history',
        loon: 'https://sub.example.com/loon/history',
        smart: 'https://sub.example.com/smart/history',
      },
    }])
    const wrapper = mountShares()
    await flushPromises()

    expect(wrapper.text()).toContain('https://sub.example.com/raw/history')
    expect(wrapper.text()).toContain('https://sub.example.com/clash/history')
    expect(wrapper.text()).toContain('https://sub.example.com/clash-ha/history')
    expect(wrapper.text()).toContain('https://sub.example.com/surge/history')
    expect(wrapper.text()).toContain('https://sub.example.com/loon/history')
    expect(wrapper.text()).toContain('https://sub.example.com/smart/history')
    expect(api.request).toHaveBeenCalledTimes(1)
  })

  it('keeps successful historical links visible when one format fails', async () => {
    api.request
      .mockResolvedValueOnce([{ ...summary, allow_clash: true, recoverable: true }])
      .mockResolvedValueOnce({ url: 'https://sub.example.com/raw/history' })
      .mockResolvedValueOnce({ url: 'https://sub.example.com/clash/history' })
      .mockResolvedValueOnce({ url: 'https://sub.example.com/clash-ha/history' })
      .mockRejectedValueOnce(new Error('surge unavailable'))
      .mockResolvedValueOnce({ url: 'https://sub.example.com/loon/history' })
      .mockResolvedValueOnce({ url: 'https://sub.example.com/smart/history' })
    const wrapper = mountShares()
    await flushPromises()

    await buttonWithText(wrapper, '查看全部链接').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('https://sub.example.com/raw/history')
    expect(wrapper.text()).toContain('https://sub.example.com/clash/history')
    expect(wrapper.text()).toContain('https://sub.example.com/loon/history')
    expect(wrapper.text()).toContain('https://sub.example.com/smart/history')
    expect(wrapper.text()).toContain('部分链接加载失败')
    expect(buttonWithText(wrapper, '重新获取链接')).toBeTruthy()
  })

  it('shows a retry hint when every historical link fails to load', async () => {
    api.request
      .mockResolvedValueOnce([{ ...summary, allow_clash: true, recoverable: true }])
      .mockRejectedValueOnce(new Error('raw unavailable'))
      .mockRejectedValueOnce(new Error('clash unavailable'))
      .mockRejectedValueOnce(new Error('clash-ha unavailable'))
      .mockRejectedValueOnce(new Error('surge unavailable'))
      .mockRejectedValueOnce(new Error('loon unavailable'))
      .mockRejectedValueOnce(new Error('smart unavailable'))
    const wrapper = mountShares()
    await flushPromises()

    await buttonWithText(wrapper, '查看全部链接').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('链接加载失败')
    expect(buttonWithText(wrapper, '查看全部链接')).toBeTruthy()
  })

  it('merges successful links from a later partial retry', async () => {
    api.request
      .mockResolvedValueOnce([{ ...summary, allow_clash: true, recoverable: true }])
      .mockResolvedValueOnce({ url: 'https://sub.example.com/raw/history' })
      .mockResolvedValueOnce({ url: 'https://sub.example.com/clash/history' })
      .mockResolvedValueOnce({ url: 'https://sub.example.com/clash-ha/history' })
      .mockRejectedValueOnce(new Error('surge unavailable'))
      .mockResolvedValueOnce({ url: 'https://sub.example.com/loon/history' })
      .mockResolvedValueOnce({ url: 'https://sub.example.com/smart/history' })
      .mockRejectedValueOnce(new Error('raw unavailable'))
      .mockRejectedValueOnce(new Error('clash unavailable'))
      .mockRejectedValueOnce(new Error('clash-ha unavailable'))
      .mockResolvedValueOnce({ url: 'https://sub.example.com/surge/history' })
      .mockRejectedValueOnce(new Error('loon unavailable'))
      .mockRejectedValueOnce(new Error('smart unavailable'))
    const wrapper = mountShares()
    await flushPromises()

    await buttonWithText(wrapper, '查看全部链接').trigger('click')
    await flushPromises()
    await buttonWithText(wrapper, '重新获取链接').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('https://sub.example.com/raw/history')
    expect(wrapper.text()).toContain('https://sub.example.com/clash/history')
    expect(wrapper.text()).toContain('https://sub.example.com/surge/history')
    expect(wrapper.text()).toContain('https://sub.example.com/loon/history')
    expect(wrapper.text()).toContain('https://sub.example.com/smart/history')
  })

  it('disables reveal actions for a non-recoverable share', async () => {
    api.request.mockResolvedValueOnce([{ ...summary, allow_clash: true, recoverable: false }])
    const wrapper = mountShares()
    await flushPromises()

    expect(buttonWithText(wrapper, '查看全部链接')).toBeUndefined()
  })

  it('refreshes automatically displayed historical links with the list', async () => {
    api.request
      .mockResolvedValueOnce([{
        ...summary,
        allow_clash: true,
        recoverable: true,
        urls: { raw: 'https://sub.example.com/raw/history' },
      }])
      .mockResolvedValueOnce([{
        ...summary,
        allow_clash: true,
        recoverable: true,
        urls: { raw: 'https://sub.example.com/raw/refreshed' },
      }])
    const wrapper = mountShares()
    await flushPromises()

    expect(wrapper.text()).toContain('https://sub.example.com/raw/history')

    await buttonWithText(wrapper, '刷新列表').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('https://sub.example.com/raw/history')
    expect(wrapper.text()).toContain('https://sub.example.com/raw/refreshed')
  })

  it('copies a historical link on HTTP when the Clipboard API is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand })
    api.request.mockResolvedValueOnce([{
      ...summary,
      recoverable: true,
      urls: { raw: 'http://nas.example/raw/history' },
    }])
    const wrapper = mountShares()
    await flushPromises()

    await buttonWithText(wrapper, '复制').trigger('click')
    await flushPromises()

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(messages.success).toHaveBeenCalledWith('链接已复制')
    delete document.execCommand
  })

  it('forces raw permission when clash is enabled and reveals only the created links', async () => {
    api.request
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({
        id: summary.id,
        raw_url: 'https://sub.example.com/raw/one-time',
        clash_url: 'https://sub.example.com/clash/one-time',
        expires_at: summary.expires_at,
      })
      .mockResolvedValueOnce([summary])
    const wrapper = mountShares()
    await flushPromises()

    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    await checkboxes[0].setValue(false)
    await checkboxes[1].setValue(true)
    expect(checkboxes[0].element.checked).toBe(true)
    expect(checkboxes[0].element.disabled).toBe(true)

    await wrapper.get('input[placeholder="例如：小林的路由器"]').setValue('  小林  ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(2, '/api/admin/shares', {
      method: 'POST',
      body: {
        label: '小林',
        days: 365,
        allow_raw: true,
        allow_clash: true,
      },
    })
    expect(wrapper.get('[data-testid="secret-reveal"]').text()).toContain('/raw/one-time')
    expect(wrapper.get('[data-testid="secret-reveal"]').text()).toContain('/clash/one-time')
  })

  it('requires a separate confirmation step before renewing', async () => {
    api.request.mockResolvedValueOnce([summary]).mockResolvedValueOnce({}).mockResolvedValueOnce([summary])
    const wrapper = mountShares()
    await flushPromises()

    await buttonWithText(wrapper, '续期').trigger('click')
    expect(api.request).toHaveBeenCalledTimes(1)
    expect(buttonWithText(wrapper, '确认续期')).toBeTruthy()

    await buttonWithText(wrapper, '确认续期').trigger('click')
    await flushPromises()
    expect(api.request).toHaveBeenNthCalledWith(
      2,
      `/api/admin/shares/${summary.id}/renew`,
      { method: 'POST', body: { days: 365 } },
    )
  })

  it('uses explicit Chinese labels for destructive confirmations', async () => {
    api.request.mockResolvedValueOnce([summary])
    const wrapper = mountShares()
    await flushPromises()

    const confirmations = wrapper.findAllComponents(PopconfirmStub)
    expect(confirmations.map((item) => item.attributes('positive-text'))).toEqual([
      '确认撤销',
      '确认轮换',
      '确认删除',
    ])
    expect(confirmations.every((item) => item.attributes('negative-text') === '取消')).toBe(true)
  })

  it('disables actions that cannot make a revoked share usable', async () => {
    api.request.mockResolvedValueOnce([{ ...summary, revoked: true }])
    const wrapper = mountShares()
    await flushPromises()

    expect(buttonWithText(wrapper, '续期').attributes('disabled')).toBeDefined()
    expect(buttonWithText(wrapper, '撤销').attributes('disabled')).toBeDefined()
    expect(buttonWithText(wrapper, '轮换密钥').attributes('disabled')).toBeDefined()
    expect(buttonWithText(wrapper, '删除记录').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('已撤销记录只能删除')
    expect(wrapper.text()).not.toContain('请先续期，再轮换密钥')
  })

  it('associates visible share labels with the real Naive UI inputs', async () => {
    api.request.mockResolvedValueOnce([summary])
    const wrapper = mountSharesWithNaive()
    await flushPromises()

    expect(wrapper.get('label[for="share-label"]').text()).toContain('朋友备注')
    expect(wrapper.get('#share-label').element.tagName).toBe('INPUT')
    expect(wrapper.get('label[for="share-days"]').text()).toContain('有效天数')
    expect(wrapper.get('#share-days').element.tagName).toBe('INPUT')

    await buttonWithText(wrapper, '续期').trigger('click')
    expect(wrapper.get(`label[for="renew-${summary.id}"]`).text()).toContain('续期天数')
    expect(wrapper.get(`#renew-${summary.id}`).element.tagName).toBe('INPUT')
    wrapper.unmount()
  })

  it('does not report an empty list when the initial request fails', async () => {
    api.request.mockRejectedValueOnce(new Error('network unavailable'))
    const wrapper = mountShares()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('network unavailable')
    expect(wrapper.text()).not.toContain('还没有分享链接')
  })
})
