import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ConverterConsole from '../src/components/ConverterConsole.vue'
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

const createdShare = {
  id: '11111111-1111-1111-1111-111111111111',
  raw_url: 'http://testserver/raw/token-value',
  clash_url: 'http://testserver/clash/token-value',
  clash_ha_url: 'http://testserver/clash-ha/token-value',
  surge_url: 'http://testserver/surge/token-value',
  loon_url: '',
  quanx_url: '',
  surfboard_url: '',
  singbox_url: '',
  smart_url: 'http://testserver/smart/token-value',
  expires_at: 1_900_000_000,
}

function mountConsole() {
  return mount(ConverterConsole, { attachTo: document.body, global: { stubs: viewStubs } })
}

function buttonByText(text) {
  return [...document.querySelectorAll('button')].find((button) => button.textContent.includes(text))
}

describe('ConverterConsole', () => {
  beforeEach(() => {
    api.request.mockReset()
    mocks.success.mockReset()
    mocks.error.mockReset()
    document.body.innerHTML = ''
  })

  it('previews the configured upstream with the picked parameters', async () => {
    api.request.mockResolvedValue({ kind: 'clash', body: 'proxy-providers: {}\n' })
    const wrapper = mountConsole()

    expect(wrapper.text()).toContain('预设与分享链接完全一致')
    buttonByText('预览转换结果').click()
    await flushPromises()

    expect(api.request).toHaveBeenLastCalledWith('/api/admin/converter/preview', {
      method: 'POST',
      body: { kind: 'clash', params: {} },
    })
    expect(wrapper.get('.converter-preview').text()).toContain('proxy-providers')

    await wrapper.get('#param-udp-true').setValue(true)
    await wrapper.get('#converter-kind').setValue('singbox')
    buttonByText('预览转换结果').click()
    await flushPromises()

    expect(api.request).toHaveBeenLastCalledWith('/api/admin/converter/preview', {
      method: 'POST',
      body: { kind: 'singbox', params: { udp: 'true' } },
    })
    // 下载按钮跟着目标格式走：sing-box 是 JSON，Clash 是 YAML。
    expect(buttonByText('下载 json 文件')).toBeTruthy()
  })

  it('saves the conversion as a new subscription with copyable links', async () => {
    api.request.mockResolvedValueOnce(createdShare)
    const wrapper = mountConsole()

    await wrapper.get('#converter-share-label').setValue('书房路由器')
    await wrapper.get('#converter-share-days').setValue('30')
    buttonByText('保存为订阅源并获取链接').click()
    await flushPromises()

    expect(api.request).toHaveBeenCalledWith('/api/admin/shares', {
      method: 'POST',
      body: { label: '书房路由器', days: 30, allow_raw: true, allow_clash: true },
    })
    // 保存后直接弹出链接窗口（真实 n-modal 挂在 body 上），没有链接的字段不展示。
    expect(document.body.textContent).toContain('分享链接')
    expect(document.querySelector('#subscription-surge-url').value).toBe(
      'http://testserver/surge/token-value',
    )
    expect(document.querySelector('#subscription-loon-url')).toBeNull()
  })

  it('surfaces converter failures instead of reporting success', async () => {
    api.request.mockRejectedValue(new Error('converter unavailable'))
    mountConsole()

    buttonByText('预览转换结果').click()
    await flushPromises()

    expect(document.querySelector('.converter-console-error').textContent).toBe('converter unavailable')
    expect(mocks.success).not.toHaveBeenCalled()
    expect(document.querySelector('.converter-preview')).toBeNull()
  })
})
