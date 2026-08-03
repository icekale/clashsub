import { reactive } from 'vue'

import { api } from './api.js'


export const session = reactive({ ready: false, authenticated: false, username: '' })


export async function restoreSession() {
  try {
    const payload = await api.request('/api/auth/session')
    api.setCsrf(payload.csrf_token)
    Object.assign(session, { ready: true, authenticated: true, username: payload.username })
  } catch (_) {
    api.setCsrf('')
    Object.assign(session, { ready: true, authenticated: false, username: '' })
  }
}


export function acceptLogin(payload) {
  api.setCsrf(payload.csrf_token)
  Object.assign(session, { ready: true, authenticated: true, username: payload.username })
}


export function clearSession() {
  api.setCsrf('')
  Object.assign(session, { ready: true, authenticated: false, username: '' })
}


window.addEventListener('clashsub:unauthorized', clearSession)
