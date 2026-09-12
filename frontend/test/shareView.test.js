import { describe, expect, it } from 'vitest'

import {
  activeParams,
  buildShareRequest,
  downloadFilename,
  kindLabel,
  paramChoices,
  paramsQuery,
  shareDialogUrls,
  statusLabel,
  withParams,
} from '../src/shareView.js'


describe('share view helpers', () => {
  it('share request defaults to 365 days and clash forces raw', () => {
    expect(buildShareRequest({ label: ' friend ', days: '', allowRaw: false, allowClash: true })).toEqual({
      label: 'friend',
      days: 365,
      allow_raw: true,
      allow_clash: true,
    })
  })

  it('labels clash-ha as health-filtered, not smart UA routing', () => {
    expect(kindLabel('clash-ha')).toBe('健康节点')
    expect(kindLabel('smart')).toBe('智能')
  })

  it('status is not conveyed by color alone', () => {
    expect(statusLabel({ revoked: true, expired: false })).toBe('已撤销')
    expect(statusLabel({ revoked: false, expired: true })).toBe('已过期')
    expect(statusLabel({ revoked: false, expired: false })).toBe('有效')
  })

  it('keeps the parameter object in sync with the query string', () => {
    expect(activeParams({ emoji: '', udp: 'true', ver: ' 4 ' })).toEqual({ udp: 'true', ver: '4' })
    expect(paramsQuery({ emoji: '', udp: 'true', ver: ' 4 ' })).toBe('udp=true&ver=4')
    expect(activeParams(null)).toEqual({})
  })

  it('names downloads by target format', () => {
    expect(downloadFilename('clash', 'upstream')).toBe('upstream-clash.yaml')
    expect(downloadFilename('singbox')).toBe('clashsub-singbox.json')
    expect(downloadFilename('surge')).toBe('clashsub-surge.conf')
  })

  it('maps a created share onto the link dialog fields', () => {
    expect(
      shareDialogUrls({ raw_url: 'r', clash_url: 'c', surge_url: 's', smart_url: 'm' }),
    ).toEqual({
      raw: 'r',
      clash: 'c',
      clashHa: '',
      surge: 's',
      loon: '',
      quanx: '',
      surfboard: '',
      singbox: '',
      smart: 'm',
    })
  })

  it('keeps the parameter query in whitelist order and drops 默认 values', () => {
    expect(paramsQuery({})).toBe('')
    expect(paramsQuery({ udp: '', tfo: 'true', ver: '3', emoji: 'false' })).toBe(
      'emoji=false&tfo=true&ver=3',
    )
    expect(paramsQuery({ ver: ' 2 ' })).toBe('ver=2')
  })

  it('appends parameters to a copyable link without touching plain urls', () => {
    expect(withParams('https://sub.example/clash/token', {})).toBe('https://sub.example/clash/token')
    expect(withParams('https://sub.example/clash/token', { udp: 'true' })).toBe(
      'https://sub.example/clash/token?udp=true',
    )
    expect(withParams('https://sub.example/clash/token?a=1', { udp: 'false' })).toBe(
      'https://sub.example/clash/token?a=1&udp=false',
    )
    expect(withParams('', { udp: 'true' })).toBe('')
  })

  it('offers three states for switches and 默认 plus versions for ver', () => {
    expect(paramChoices({ key: 'udp' }).map((choice) => [choice.value, choice.label])).toEqual([
      ['', '默认'],
      ['true', '开'],
      ['false', '关'],
    ])
    expect(paramChoices({ key: 'ver', choices: ['2', '3', '4'] }).map((choice) => choice.value)).toEqual([
      '',
      '2',
      '3',
      '4',
    ])
  })
})
