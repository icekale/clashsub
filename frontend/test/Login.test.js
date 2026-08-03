import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { create, NButton, NForm, NFormItem, NInput } from 'naive-ui'

import Login from '../src/views/Login.vue'
import { api } from '../src/api.js'
import { acceptLogin } from '../src/session.js'


const mocks = vi.hoisted(() => ({ replace: vi.fn() }))

vi.mock('../src/api.js', () => ({ api: { request: vi.fn() } }))
vi.mock('../src/session.js', () => ({ acceptLogin: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { next: '/overview' } }),
  useRouter: () => ({ replace: mocks.replace }),
}))

const naive = create({ components: [NButton, NForm, NFormItem, NInput] })

describe('Login', () => {
  beforeEach(() => {
    api.request.mockReset()
    acceptLogin.mockReset()
    mocks.replace.mockReset()
  })

  it('submits credentials when the login button is clicked', async () => {
    api.request.mockResolvedValueOnce({ username: 'admin', csrf_token: 'csrf-token' })
    const wrapper = mount(Login, { global: { plugins: [naive] } })

    expect(wrapper.findAll('form')).toHaveLength(1)
    await wrapper.get('.n-input[autocomplete="username"] input').setValue('admin')
    await wrapper.get('.n-input[autocomplete="current-password"] input').setValue('password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.request).toHaveBeenCalledWith('/api/auth/login', {
      method: 'POST',
      body: { username: 'admin', password: 'password' },
    })
    expect(acceptLogin).toHaveBeenCalledWith({ username: 'admin', csrf_token: 'csrf-token' })
    expect(mocks.replace).toHaveBeenCalledWith('/overview')
  })
})
