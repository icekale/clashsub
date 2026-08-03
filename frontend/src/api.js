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
