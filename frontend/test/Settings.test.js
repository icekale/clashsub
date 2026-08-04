import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  create,
  NAlert,
  NButton,
  NCheckbox,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSkeleton,
  NSwitch,
} from 'naive-ui'

import Settings from '../src/views/Settings.vue'
import { api } from '../src/api.js'
import { clearSession } from '../src/session.js'
import { viewStubs } from './viewStubs.js'


const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../src/api.js', () => ({ api: { request: vi.fn() } }))
vi.mock('../src/session.js', () => ({
  clearSession: vi.fn(),
  session: { username: 'admin' },
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal()),
  useMessage: () => ({ success: mocks.success, error: mocks.error, warning: vi.fn() }),
}))

const loadedSettings = {
  refresh_interval_minutes: 10,
  access_mode: 'lan',
  lan_base_url: 'http://nas.lan:18080',
  public_base_url: 'https://sub.example.com',
  converter_enabled: false,
  openclash_enabled: false,
  openclash_api_url: '',
  openclash_provider: '',
  health_enabled: false,
  health_interval_seconds: 600,
  health_timeout_seconds: 5,
  health_refresh_enabled: false,
  health_refresh_online_ratio: 0.5,
  health_refresh_cooldown_minutes: 10,
}

const loadedUpstreamStatus = {
  protocol_configured: true,
  api_base_url: 'https://panel.example.test/api/v1',
  email_configured: true,
  password_configured: true,
  fallback_configured: true,
}

const loadedAirportCredentials = {
  username: 'member@example.test',
  password_configured: true,
}

function mockInitialLoad(status = loadedUpstreamStatus) {
  api.request
    .mockResolvedValueOnce(loadedSettings)
    .mockResolvedValueOnce(status)
    .mockResolvedValueOnce(loadedAirportCredentials)
    .mockResolvedValueOnce({ configured: false })
}

function mountSettings() {
  return mount(Settings, { global: { stubs: viewStubs } })
}

const naive = create({
  components: [
    NAlert,
    NButton,
    NCheckbox,
    NForm,
    NFormItem,
    NInput,
    NInputNumber,
    NModal,
    NSelect,
    NSkeleton,
    NSwitch,
  ],
})

function mountSettingsWithNaive() {
  return mount(Settings, { global: { plugins: [naive] } })
}

function buttonWithText(wrapper, text) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

describe('Settings', () => {
  beforeEach(() => {
    api.request.mockReset()
    clearSession.mockReset()
    mocks.replace.mockReset()
    mocks.success.mockReset()
    mocks.error.mockReset()
  })

  it('shows all public risks before switching and sends the acknowledgement', async () => {
    mockInitialLoad()
    api.request.mockResolvedValueOnce({
      ...loadedSettings,
      access_mode: 'public',
      reauthenticate: true,
    })
    const wrapper = mountSettings()
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(1, '/api/admin/settings')
    await wrapper.get('[data-testid="access-mode"]').setValue('public')

    const dialog = wrapper.get('.public-risk-dialog')
    expect(dialog.text()).toContain('Lucky 必须已代理公网域名')
    expect(dialog.text()).toContain('当前管理员凭据会暴露到互联网')
    expect(dialog.text()).toContain('Lucky access log 可能记录包含分享 token 的路径')
    expect(buttonWithText(dialog, '确认切换到公网').attributes('disabled')).toBeDefined()

    await dialog.get('input[type="checkbox"]').setValue(true)
    await buttonWithText(dialog, '确认切换到公网').trigger('click')
    await buttonWithText(wrapper, '保存运行设置').trigger('click')
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(5, '/api/admin/settings', {
      method: 'PUT',
      body: {
        ...loadedSettings,
        access_mode: 'public',
        public_acknowledged: true,
      },
    })
    expect(clearSession).toHaveBeenCalledOnce()
    expect(mocks.replace).toHaveBeenCalledWith('/login')
  })

  it('warns that changed Base URLs cannot be reconstructed from hashes', async () => {
    mockInitialLoad()
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.get('[data-testid="lan-base-url"]').setValue('http://new-nas.lan:18080')

    const warning = wrapper.get('.base-url-warning')
    expect(warning.text()).toContain('数据库只保存密钥哈希，旧链接无法恢复')
    expect(warning.text()).toContain('只替换已保存 URL 的 origin')
    expect(warning.text()).toContain('轮换并重新分发密钥')
  })

  it('keeps passwords empty, verifies confirmation, then clears the session', async () => {
    mockInitialLoad()
    api.request.mockResolvedValueOnce({ reauthenticate: true })
    const wrapper = mountSettings()
    await flushPromises()

    const currentPassword = wrapper.get('[data-testid="current-password"]')
    const newPassword = wrapper.get('[data-testid="new-password"]')
    const confirmation = wrapper.get('[data-testid="confirm-password"]')
    expect(currentPassword.element.value).toBe('')
    expect(newPassword.element.value).toBe('')
    expect(confirmation.element.value).toBe('')
    expect(currentPassword.attributes('placeholder')).toBe('输入当前密码')
    expect(wrapper.get('[data-testid="new-username"]').attributes('placeholder')).toBe('输入新用户名')
    expect(newPassword.attributes('placeholder')).toBe('输入新密码')
    expect(confirmation.attributes('placeholder')).toBe('再次输入新密码')

    await currentPassword.setValue('current-secret')
    await wrapper.get('[data-testid="new-username"]').setValue('next-admin')
    await newPassword.setValue('next-secret')
    await confirmation.setValue('does-not-match')
    await wrapper.get('.credentials-form-grid').trigger('submit')
    expect(wrapper.text()).toContain('两次输入的新密码不一致')
    expect(api.request).toHaveBeenCalledTimes(4)

    await confirmation.setValue('next-secret')
    await wrapper.get('.credentials-form-grid').trigger('submit')
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(5, '/api/auth/credentials', {
      method: 'PUT',
      body: {
        current_password: 'current-secret',
        new_username: 'next-admin',
        new_password: 'next-secret',
      },
    })
    expect(clearSession).toHaveBeenCalledOnce()
    expect(mocks.replace).toHaveBeenCalledWith('/login')
  })

  it('does not expose default settings as loaded values after the initial request fails', async () => {
    api.request
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce(loadedUpstreamStatus)
    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('network unavailable')
    expect(wrapper.find('[data-testid="access-mode"]').exists()).toBe(false)
  })

  it('associates visible settings labels and autocomplete with real Naive UI inputs', async () => {
    mockInitialLoad()
    const wrapper = mountSettingsWithNaive()
    await flushPromises()

    const fields = [
      ['settings-refresh-interval', '按需刷新间隔'],
      ['settings-access-mode', '访问模式'],
      ['settings-lan-base-url', '局域网 Base URL'],
      ['settings-public-base-url', '公网 Base URL'],
      ['admin-current-password', '当前密码'],
      ['admin-new-username', '新用户名'],
      ['admin-new-password', '新密码'],
      ['admin-confirm-password', '再次输入新密码'],
    ]
    for (const [id, labelText] of fields) {
      expect(wrapper.get(`label[for="${id}"]`).text()).toContain(labelText)
      expect(wrapper.get(`#${id}`).element.tagName).toBe('INPUT')
    }
    expect(wrapper.get('#admin-current-password').attributes('autocomplete')).toBe('current-password')
    expect(wrapper.get('#admin-new-password').attributes('autocomplete')).toBe('new-password')
    expect(wrapper.get('#admin-confirm-password').attributes('autocomplete')).toBe('new-password')
    wrapper.unmount()
  })

  it('submits credential changes from the real Naive UI form', async () => {
    mockInitialLoad()
    api.request.mockResolvedValueOnce({ reauthenticate: true })
    const wrapper = mountSettingsWithNaive()
    await flushPromises()

    expect(wrapper.findAll('form')).toHaveLength(5)
    await wrapper.get('#admin-current-password').setValue('current-secret')
    await wrapper.get('#admin-new-username').setValue('next-admin')
    await wrapper.get('#admin-new-password').setValue('next-secret')
    await wrapper.get('#admin-confirm-password').setValue('next-secret')
    await wrapper.get('form.credentials-form-grid').trigger('submit')
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(5, '/api/auth/credentials', {
      method: 'PUT',
      body: {
        current_password: 'current-secret',
        new_username: 'next-admin',
        new_password: 'next-secret',
      },
    })
    expect(clearSession).toHaveBeenCalledOnce()
    expect(mocks.replace).toHaveBeenCalledWith('/login')
    wrapper.unmount()
  })

  it('loads redacted airport status concurrently and tests the protocol connection', async () => {
    let resolveSettings
    const pendingSettings = new Promise((resolve) => { resolveSettings = resolve })
    const statusWithUnexpectedSecrets = {
      ...loadedUpstreamStatus,
      email: 'member@example.test',
      password: 'airport-password',
      subscription_url: 'https://sub.example.test/path?token=hidden-token',
      token: 'hidden-token',
    }
    api.request
      .mockReturnValueOnce(pendingSettings)
      .mockResolvedValueOnce(statusWithUnexpectedSecrets)
      .mockResolvedValueOnce(loadedAirportCredentials)
      .mockResolvedValueOnce({ configured: false })
      .mockResolvedValueOnce({ ok: true, error_category: null, expires_at: 1_900_000_000 })

    const wrapper = mountSettings()
    await Promise.resolve()
    const callsBeforeSettingsResolution = api.request.mock.calls.slice()
    resolveSettings(loadedSettings)
    await flushPromises()

    expect(callsBeforeSettingsResolution).toEqual([
      ['/api/admin/settings'],
      ['/api/admin/upstream/status'],
      ['/api/admin/upstream/credentials'],
      ['/api/admin/openclash/credentials'],
    ])
    expect(wrapper.text()).toContain('机场订阅源')
    expect(wrapper.text()).toContain('https://panel.example.test/api/v1')
    expect(wrapper.text()).toContain('协议配置完整')
    expect(wrapper.text()).toContain('邮箱 Secret 已配置')
    expect(wrapper.text()).toContain('密码 Secret 已配置')
    expect(wrapper.text()).toContain('备用订阅已配置')
    expect(wrapper.text()).not.toContain('member@example.test')
    expect(wrapper.text()).not.toContain('airport-password')
    expect(wrapper.text()).not.toContain('hidden-token')
    expect(wrapper.text()).not.toContain('订阅 URL')
    expect(wrapper.text()).not.toContain('Token')

    await buttonWithText(wrapper, '测试机场连接').trigger('click')
    await flushPromises()

    expect(api.request).toHaveBeenCalledWith('/api/admin/upstream/test', { method: 'POST' })
    expect(wrapper.text()).toContain('协议连接成功')
  })

  it('shows the sanitized captcha category returned by a failed protocol test', async () => {
    mockInitialLoad()
    api.request.mockResolvedValueOnce({
      ok: false,
      error_category: 'captcha_required',
      expires_at: null,
    })
    const wrapper = mountSettings()
    await flushPromises()

    await buttonWithText(wrapper, '测试机场连接').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('协议连接失败')
    expect(wrapper.text()).toContain('captcha_required')
  })

  it('disables protocol testing when the protocol configuration is incomplete', async () => {
    mockInitialLoad({
      protocol_configured: false,
      api_base_url: null,
      email_configured: false,
      password_configured: false,
      fallback_configured: true,
    })
    const wrapper = mountSettings()
    await flushPromises()

    const testButton = buttonWithText(wrapper, '测试机场连接')
    expect(wrapper.text()).toContain('协议配置不完整')
    expect(testButton.attributes('disabled')).toBeDefined()
    expect(api.request).toHaveBeenCalledTimes(4)
  })

  it('loads airport credentials without prefilling the password and saves a validated candidate', async () => {
    api.request
      .mockResolvedValueOnce(loadedSettings)
      .mockResolvedValueOnce(loadedUpstreamStatus)
      .mockResolvedValueOnce({ username: 'member@example.test', password_configured: true })
      .mockResolvedValueOnce({ configured: false })
      .mockResolvedValueOnce({ ok: true, node_count: 49, error_category: null })
    const wrapper = mountSettingsWithNaive()
    await flushPromises()

    expect(wrapper.get('#airport-username').element.value).toBe('member@example.test')
    expect(wrapper.get('#airport-password').element.value).toBe('')
    await wrapper.get('#airport-username').setValue('updated@example.test')
    await wrapper.get('#airport-password').setValue('candidate-password')
    await wrapper.get('form.airport-credentials-form').trigger('submit')
    await flushPromises()

    expect(api.request).toHaveBeenNthCalledWith(5, '/api/admin/upstream/credentials', {
      method: 'PUT',
      body: { username: 'updated@example.test', password: 'candidate-password' },
    })
    expect(wrapper.get('#airport-password').element.value).toBe('')
    expect(wrapper.text()).toContain('49')
  })
})
