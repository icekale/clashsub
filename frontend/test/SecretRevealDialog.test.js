import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SecretRevealDialog from '../src/components/SecretRevealDialog.vue'


function mountDialog(props) {
  return mount(SecretRevealDialog, {
    props,
    attachTo: document.body,
    global: {
      stubs: {
        NModal: { props: ['show'], template: '<div v-if="show"><slot /></div>' },
        'n-modal': { props: ['show'], template: '<div v-if="show"><slot /></div>' },
      },
    },
  })
}

describe('SecretRevealDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('keeps one-time links selectable and explains the loss boundary', () => {
    mountDialog({
      show: true,
      urls: { raw: 'https://sub.example/raw/one-time' },
    })
    expect(document.querySelector('textarea').value).toContain('/raw/one-time')
    expect(document.body.textContent).toContain('有效期内可以从分享记录中再次查看')
  })

  it('adds a separately labeled field when an OpenClash URL is available', () => {
    mountDialog({
      show: true,
      urls: {
        raw: 'https://sub.example/raw/one-time',
        clash: 'https://sub.example/clash/one-time',
      },
    })

    expect(document.querySelectorAll('textarea')).toHaveLength(2)
    expect(document.body.textContent).toContain('原始订阅')
    expect(document.body.textContent).toContain('OpenClash 转换')
  })

  it('adds a separately labeled field for the health-filtered Clash URL', () => {
    mountDialog({
      show: true,
      urls: {
        raw: 'https://sub.example/raw/one-time',
        clashHa: 'https://sub.example/clash-ha/one-time',
      },
    })

    expect(document.body.textContent).toContain('仅健康节点')
    expect(document.querySelector('textarea#subscription-clash-ha-url').value).toContain('/clash-ha/one-time')
  })

  it('stays open and selects the text when Clipboard API copying fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('permission denied')) },
    })
    const select = vi.spyOn(HTMLTextAreaElement.prototype, 'select')
    const wrapper = mountDialog({
      show: true,
      urls: { raw: 'https://sub.example/raw/one-time' },
    })

    const copyButton = [...document.querySelectorAll('button')].find((button) => button.textContent.includes('复制原始链接'))
    await copyButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(document.querySelector('[role="dialog"]')).toBeTruthy()
    expect(wrapper.emitted('update:show')).toBeUndefined()
    expect(select).toHaveBeenCalled()
    expect(document.body.textContent).toContain('自动复制失败，链接已选中，请手动复制')
  })
})
