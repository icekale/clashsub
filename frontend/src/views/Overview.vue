<script setup>
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'

import { api } from '../api.js'


const data = ref(null)
const health = ref(null)
const error = ref('')
const loading = ref(true)
const refreshing = ref(false)
const checkingHealth = ref(false)
const message = useMessage()

const state = computed(() => {
  if (!data.value?.has_cache) {
    return {
      type: 'error',
      label: '无可用缓存',
      detail: '首次有效刷新尚未完成，分享订阅暂时返回 503。',
    }
  }
  if (data.value.stale) {
    return {
      type: 'warning',
      label: '缓存陈旧但仍可用',
      detail: '最近一次上游刷新失败，当前仍在提供最后有效内容。',
    }
  }
  return {
    type: 'success',
    label: '缓存健康',
    detail: '最近一次上游刷新成功，分享订阅可正常使用。',
  }
})

function formatDate(value) {
  if (!value) return '尚无记录'
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(new Date(value * 1000))
}

function formatAge(value) {
  if (!value) return '—'
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - value))
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  return `${Math.floor(seconds / 86400)} 天前`
}

function formatSource(value) {
  if (value === 'protocol') return 'V2Board 协议'
  if (value === 'fallback') return '备用订阅'
  return '尚无记录'
}

async function load() {
  loading.value = true
  try {
    const [overview, healthPayload] = await Promise.all([
      api.request('/api/admin/overview'),
      api.request('/api/admin/health'),
    ])
    data.value = overview
    health.value = healthPayload
    error.value = ''
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
  }
}

async function runHealthNow() {
  checkingHealth.value = true
  try {
    await api.request('/api/admin/health/check', { method: 'POST' })
    message.success('节点健康检查已完成')
    await load()
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    checkingHealth.value = false
  }
}

function formatLatency(value) {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)} ms`
}

function formatCheckedAt(value) {
  if (!value) return '尚未检查'
  return formatAge(value)
}

const offlineNodes = computed(() => (health.value?.nodes || []).filter(node => !node.ok))

async function refreshNow() {
  refreshing.value = true
  try {
    const result = await api.request('/api/admin/upstream/refresh', { method: 'POST' })
    message[result.updated ? 'success' : 'warning'](
      result.updated ? '订阅缓存已更新' : '上游刷新失败，继续使用旧缓存',
    )
    await load()
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-title">
    <div>
      <h1>运行概览</h1>
      <p>确认缓存是否可用，以及最近一次刷新发生了什么。</p>
    </div>
    <n-button type="primary" :loading="refreshing" @click="refreshNow">立即刷新</n-button>
  </div>

  <n-alert v-if="error" type="error" title="无法读取运行状态" class="section-block">
    {{ error }}
  </n-alert>

  <div v-else-if="loading" class="loading-panel" aria-label="正在加载运行状态">
    <n-skeleton text :repeat="2" />
    <n-skeleton height="124px" :sharp="false" />
  </div>

  <template v-else-if="data">
    <n-alert :type="state.type" :title="state.label" class="section-block" aria-live="polite">
      {{ state.detail }}
    </n-alert>

    <section class="operation-panel" aria-labelledby="cache-facts-heading">
      <div class="panel-heading">
        <div>
          <h2 id="cache-facts-heading">缓存事实</h2>
          <p>这里不展示上游地址或任何分享密钥。</p>
        </div>
        <n-tag :type="data.converter_enabled ? 'info' : 'default'">
          在线转换：{{ data.converter_enabled ? '已开启' : '已关闭' }}
        </n-tag>
      </div>

      <dl class="fact-grid">
        <div>
          <dt>节点数量</dt>
          <dd>{{ data.node_count }}</dd>
        </div>
        <div>
          <dt>内容格式</dt>
          <dd>{{ data.content_format || '尚未识别' }}</dd>
        </div>
        <div>
          <dt>最近成功来源</dt>
          <dd>{{ formatSource(data.last_success_source) }}</dd>
        </div>
        <div>
          <dt>连续失败</dt>
          <dd>{{ data.consecutive_failures }} 次</dd>
        </div>
        <div>
          <dt>缓存年龄</dt>
          <dd>{{ formatAge(data.last_success_at) }}</dd>
        </div>
      </dl>

      <div class="timeline-facts">
        <div>
          <span>最近成功</span>
          <strong>{{ formatDate(data.last_success_at) }}</strong>
        </div>
        <div>
          <span>最近尝试</span>
          <strong>{{ formatDate(data.last_attempt_at) }}</strong>
        </div>
        <div>
          <span>最近协议登录</span>
          <strong>{{ formatDate(data.protocol_last_login_at) }}</strong>
        </div>
        <div>
          <span>最近协议订阅</span>
          <strong>{{ formatDate(data.protocol_last_subscribe_at) }}</strong>
        </div>
        <div>
          <span>协议订阅到期</span>
          <strong>{{ formatDate(data.protocol_subscription_expires_at) }}</strong>
        </div>
        <div v-if="data.protocol_last_error_category">
          <span>协议错误类别</span>
          <strong>{{ data.protocol_last_error_category }}</strong>
        </div>
        <div v-if="data.last_error">
          <span>最近错误</span>
          <strong>{{ data.last_error }}</strong>
        </div>
      </div>
    </section>

    <section class="operation-panel" aria-labelledby="health-facts-heading">
      <div class="panel-heading">
        <div>
          <h2 id="health-facts-heading">节点健康</h2>
          <p>探测结果来自 ClashSub 自身的连通性检查（TCP/TLS 握手）。</p>
        </div>
        <n-button secondary :loading="checkingHealth" @click="runHealthNow">
          立即检查
        </n-button>
      </div>

      <n-alert v-if="health && !health.enabled" type="warning" class="section-block">
        健康检查当前未开启，请在设置页启用。未检查时会按全部可用处理，不会误伤节点。
      </n-alert>

      <template v-if="health">
        <dl class="fact-grid">
          <div>
            <dt>在线节点</dt>
            <dd>{{ health.online }} / {{ health.total }}</dd>
          </div>
          <div>
            <dt>最近检查</dt>
            <dd>{{ formatCheckedAt(health.checked_at) }}</dd>
          </div>
          <div>
            <dt>检查间隔</dt>
            <dd>
              {{ health.enabled ? `${health.interval_seconds} 秒` : '未开启' }}
              <template v-if="health.enabled && health.night_enabled">
                （夜间 {{ health.night_start_hour }}:00–{{ health.night_end_hour }}:00 为
                {{ health.night_interval_seconds }} 秒）
              </template>
            </dd>
          </div>
        </dl>

        <div v-if="offlineNodes.length" class="offline-list" aria-label="离线节点列表">
          <h3>最近检查失败的节点（{{ offlineNodes.length }}）</h3>
          <ul>
            <li v-for="node in offlineNodes" :key="node.name">
              <span>{{ node.name }}</span>
              <small>{{ formatCheckedAt(node.checked_at) }}</small>
            </li>
          </ul>
        </div>
        <p v-else-if="health.total > 0" class="health-ok-note">最近检查未发现离线节点。</p>
      </template>
    </section>
  </template>
</template>
