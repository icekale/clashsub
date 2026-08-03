<script setup>
import { onMounted, ref } from 'vue'

import { api } from '../api.js'


const lines = ref([])
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  try {
    const payload = await api.request('/api/admin/logs?limit=200')
    lines.value = Array.isArray(payload.lines) ? payload.lines : []
    error.value = ''
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-title">
    <div>
      <h1>运行日志</h1>
      <p>查看后端已脱敏的最近 200 条运行事件，不包含实时推送。</p>
    </div>
    <n-button
      data-testid="refresh-logs"
      secondary
      :loading="loading"
      @click="load"
    >
      刷新日志
    </n-button>
  </div>

  <n-alert v-if="error" type="error" title="无法读取运行日志" class="section-block">
    {{ error }} 请检查连接后手动刷新。
  </n-alert>

  <div v-if="loading && !lines.length" class="loading-panel" aria-label="正在加载运行日志">
    <n-skeleton text :repeat="6" />
  </div>
  <section v-else-if="!error && !lines.length" class="operation-panel empty-state" aria-live="polite">
    <strong>暂时没有运行日志</strong>
    <span>服务产生新的刷新或管理事件后，这里会显示已脱敏记录。</span>
  </section>
  <section v-else-if="lines.length" class="log-panel" aria-labelledby="recent-log-heading">
    <div class="log-toolbar">
      <h2 id="recent-log-heading">最近事件</h2>
      <span>{{ lines.length }} 条 · 后端已脱敏</span>
    </div>
    <pre class="log-view" tabindex="0" aria-label="已脱敏运行日志">{{ lines.join('\n') }}</pre>
  </section>
</template>
