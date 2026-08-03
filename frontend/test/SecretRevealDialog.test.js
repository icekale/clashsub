import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SecretRevealDialog from '../src/components/SecretRevealDialog.vue'


describe('SecretRevealDialog', () => {
  afterEach(() => vi.restoreAllMocks())

  it('keeps one-time links selectable and explains the loss boundary', () => {
    const wrapper = mount(SecretRevealDialog, {
      props: {
        show: true,
        rawUrl: 'https://sub.example/raw/one-time',
        clashUrl: '',
      },
    })
    expect(wrapper.get('textarea').element.value).toContain('/raw/one-time')
    expect(wrapper.text()).toContain('有效期内可以从分享记录中再次查看')
  })

  it('adds a separately labeled field when an OpenClash URL is available', () => {
    const wrapper = mount(SecretRevealDialog, {
      props: {
        show: true,
        rawUrl: 'https://sub.example/raw/one-time',
        clashUrl: 'https://sub.example/clash/one-time',
      },
    })

    expect(wrapper.findAll('textarea')).toHaveLength(2)
    expect(wrapper.text()).toContain('原始订阅')
    expect(wrapper.text()).toContain('OpenClash 转换')
  })

  it('keeps Tab focus inside the one-time secret dialog', async () => {
    const wrapper = mount(SecretRevealDialog, {
      attachTo: document.body,
      props: {
        show: true,
        rawUrl: 'https://sub.example/raw/one-time',
      },
    })
    await flushPromises()
    const buttons = wrapper.findAll('button')
    const firstButton = buttons[0]
    const lastButton = buttons[buttons.length - 1]

    lastButton.element.focus()
    await lastButton.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(firstButton.element)

    firstButton.element.focus()
    await firstButton.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(lastButton.element)
    wrapper.unmount()
  })

  it('restores focus to the trigger after the dialog closes', async () => {
    const trigger = document.createElement('button')
    trigger.textContent = '轮换密钥'
    document.body.append(trigger)
    trigger.focus()
    const wrapper = mount(SecretRevealDialog, {
      attachTo: document.body,
      props: {
        show: false,
        rawUrl: 'https://sub.example/raw/one-time',
      },
    })

    await wrapper.setProps({ show: true })
    await flushPromises()
    expect(document.activeElement).not.toBe(trigger)
    await wrapper.setProps({ show: false })
    await flushPromises()

    expect(document.activeElement).toBe(trigger)
    wrapper.unmount()
    trigger.remove()
  })

  it('stays open and selects the text when Clipboard API copying fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('permission denied')) },
    })
    const select = vi.spyOn(HTMLTextAreaElement.prototype, 'select')
    const wrapper = mount(SecretRevealDialog, {
      props: {
        show: true,
        rawUrl: 'https://sub.example/raw/one-time',
      },
    })

    const copyButton = wrapper.findAll('button').find((button) => button.text().includes('复制原始链接'))
    await copyButton.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.emitted('update:show')).toBeUndefined()
    expect(select).toHaveBeenCalled()
    expect(wrapper.text()).toContain('自动复制失败，链接已选中，请手动复制')
  })
})
