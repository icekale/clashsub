import { describe, expect, it } from 'vitest'

import { buildShareRequest, statusLabel } from '../src/shareView.js'


describe('share view helpers', () => {
  it('share request defaults to 365 days and clash forces raw', () => {
    expect(buildShareRequest({ label: ' friend ', days: '', allowRaw: false, allowClash: true })).toEqual({
      label: 'friend',
      days: 365,
      allow_raw: true,
      allow_clash: true,
    })
  })

  it('status is not conveyed by color alone', () => {
    expect(statusLabel({ revoked: true, expired: false })).toBe('已撤销')
    expect(statusLabel({ revoked: false, expired: true })).toBe('已过期')
    expect(statusLabel({ revoked: false, expired: false })).toBe('有效')
  })
})
