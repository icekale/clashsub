export function createApiClient(fetchImpl = fetch, onUnauthorized = () => {}) {
  let csrf = ''

  return {
    setCsrf(value) { csrf = value || '' },
    getCsrf() { return csrf },

    async request(path, options = {}) {
      const method = options.method || 'GET'
      const headers = {
        Accept: 'application/json',
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      }
      if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrf) {
        headers['X-CSRF-Token'] = csrf
      }
      const response = await fetchImpl(path, {
        ...options,
        method,
        credentials: 'same-origin',
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
      })
      const payload = await response.json().catch(() => ({}))
      if (response.status === 401) {
        csrf = ''
        onUnauthorized()
      }
      if (!response.ok) {
        // 403 + 已携带 CSRF：令牌可能已被其他标签页/重新登录轮换，重新同步一次并重试。
        if (response.status === 403 && csrf && !options.__csrfRetried) {
          const sessionResponse = await fetchImpl('/api/auth/session', {
            method: 'GET',
            credentials: 'same-origin',
          })
          if (sessionResponse.ok) {
            const sessionPayload = await sessionResponse.json().catch(() => ({}))
            if (sessionPayload.csrf_token) {
              csrf = sessionPayload.csrf_token
              return this.request(path, { ...options, __csrfRetried: true })
            }
          }
        }
        throw new Error(payload.detail || payload.message || `请求失败 (${response.status})`)
      }
      return payload
    },
  }
}

export const api = createApiClient(
  fetch,
  () => window.dispatchEvent(new Event('clashsub:unauthorized')),
)
