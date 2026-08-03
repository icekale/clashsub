import { describe, expect, it } from 'vitest'
import { createApiClient } from '../src/api.js'

describe('API client', () => {
  it('mutations include credentials and CSRF while GET does not', async () => {
    const calls = []
    const fetchImpl = async (url, options) => {
      calls.push([url, options])
      return { ok: true, status: 200, json: async () => ({ ok: true }) }
    }
    const api = createApiClient(fetchImpl)
    api.setCsrf('csrf-value')
    await api.request('/api/admin/overview')
    await api.request('/api/admin/shares', { method: 'POST', body: { label: 'friend' } })
    expect(calls[0][1].credentials).toBe('same-origin')
    expect(calls[0][1].headers['X-CSRF-Token']).toBeUndefined()
    expect(calls[1][1].headers['X-CSRF-Token']).toBe('csrf-value')
  })

  it('401 clears CSRF and notifies the session layer', async () => {
    let expired = false
    const api = createApiClient(
      async () => ({ ok: false, status: 401, json: async () => ({ detail: 'unauthorized' }) }),
      () => { expired = true },
    )
    api.setCsrf('old')
    await expect(api.request('/api/admin/overview')).rejects.toThrow('unauthorized')
    expect(expired).toBe(true)
    expect(api.getCsrf()).toBe('')
  })
})
