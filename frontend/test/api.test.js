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

  it('403 with a stale CSRF re-syncs the token and retries once', async () => {
    const calls = []
    const fetchImpl = async (url, options) => {
      calls.push([url, options])
      if (url === '/api/auth/session') {
        return { ok: true, status: 200, json: async () => ({ csrf_token: 'fresh-token' }) }
      }
      if (calls.filter(([u]) => u === '/api/admin/shares').length === 1) {
        return { ok: false, status: 403, json: async () => ({ detail: 'invalid CSRF token' }) }
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }) }
    }
    const api = createApiClient(fetchImpl)
    api.setCsrf('stale-token')
    const result = await api.request('/api/admin/shares', { method: 'POST', body: { label: 'friend' } })
    expect(result).toEqual({ ok: true })
    const mutationCalls = calls.filter(([u]) => u === '/api/admin/shares')
    expect(mutationCalls).toHaveLength(2)
    expect(mutationCalls[0][1].headers['X-CSRF-Token']).toBe('stale-token')
    expect(mutationCalls[1][1].headers['X-CSRF-Token']).toBe('fresh-token')
    expect(api.getCsrf()).toBe('fresh-token')
  })

  it('403 retry does not loop when the server keeps rejecting', async () => {
    const calls = []
    const fetchImpl = async (url, options) => {
      calls.push([url, options])
      if (url === '/api/auth/session') {
        return { ok: true, status: 200, json: async () => ({ csrf_token: 'fresh-token' }) }
      }
      return { ok: false, status: 403, json: async () => ({ detail: 'invalid CSRF token' }) }
    }
    const api = createApiClient(fetchImpl)
    api.setCsrf('stale-token')
    await expect(api.request('/api/admin/shares', { method: 'POST', body: {} })).rejects.toThrow('invalid CSRF token')
    expect(calls.filter(([u]) => u === '/api/admin/shares')).toHaveLength(2)
  })
})
