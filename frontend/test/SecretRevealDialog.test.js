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

function clickRadio(id) {
  document.querySelector(`#${id}`).click()
}

function fieldValue(id) {
  return document.querySelector(`textarea#${id}`).value
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

  it('offers visual parameter switches that only rewrite converter links', async () => {
    mountDialog({
      show: true,
      urls: {
        raw: 'https://sub.example/raw/one-time',
        clashHa: 'https://sub.example/clash-ha/one-time',
        clash: 'https://sub.example/clash/one-time',
        smart: 'https://sub.example/smart/one-time',
      },
    })

    expect(fieldValue('one-time-clash-url')).toBe('https://sub.example/clash/one-time')
    expect(document.body.textContent).toContain('转换参数')

    clickRadio('param-udp-true')
    await flushPromises()
    expect(fieldValue('one-time-clash-url')).toBe('https://sub.example/clash/one-time?udp=true')
    expect(fieldValue('subscription-smart-url')).toBe('https://sub.example/smart/one-time?udp=true')
    expect(fieldValue('one-time-raw-url')).toBe('https://sub.example/raw/one-time')
    expect(fieldValue('subscription-clash-ha-url')).toBe('https://sub.example/clash-ha/one-time')

    clickRadio('param-udp-false')
    clickRadio('param-ver-3')
    await flushPromises()
    expect(fieldValue('one-time-clash-url')).toBe('https://sub.example/clash/one-time?udp=false&ver=3')
  })

  it('resets every switch back to 默认', async () => {
    mountDialog({
      show: true,
      urls: { clash: 'https://sub.example/clash/one-time' },
    })

    clickRadio('param-emoji-true')
    await flushPromises()
    expect(fieldValue('one-time-clash-url')).toContain('emoji=true')

    const reset = [...document.querySelectorAll('button')].find((button) => button.textContent.includes('恢复默认'))
    await reset.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(fieldValue('one-time-clash-url')).toBe('https://sub.example/clash/one-time')
    expect(document.querySelector('#param-emoji-default').checked).toBe(true)
  })

  it('hides the parameter panel when only raw links are available', () => {
    mountDialog({ show: true, urls: { raw: 'https://sub.example/raw/one-time' } })

    expect(document.body.textContent).not.toContain('转换参数')
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
