<script setup>
import { computed, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'

import { api } from '../api.js'
import ConvertParamPicker from './ConvertParamPicker.vue'
import SecretRevealDialog from './SecretRevealDialog.vue'
import {
  activeParams,
  buildShareRequest,
  CONVERT_PARAMS,
  CONVERTER_KINDS,
  copyText,
  downloadFilename,
  shareDialogUrls,
} from '../shareView.js'


const KINDS = CONVERTER_KINDS.filter((kind) => kind !== 'smart')
const KIND_OPTIONS = KINDS.map((kind) => ({
  value: kind,
  label: { clash: 'Clash', quanx: 'Quantumult X', singbox: 'sing-box' }[kind] || kind[0].toUpperCase() + kind.slice(1),
}))

const message = useMessage()
const kind = ref('clash')
const params = reactive(Object.fromEntries(CONVERT_PARAMS.map((param) => [param.key, ''])))
const body = ref('')
const previewing = ref(false)
// 保存成订阅源才拿得到客户端可用的链接，所以备注和天数跟分享页一样带默认值。
const saveLabel = ref('')
const saveDays = ref(365)
const saving = ref(false)
const error = ref('')
const reveal = reactive({ show: false, urls: {} })

const suffix = computed(() => downloadFilename(kind.value).split('.').pop())
const activeCount = computed(() => Object.keys(activeParams(params)).length)

async function preview() {
  previewing.value = true
  error.value = ''
  try {
    const payload = await api.request('/api/admin/converter/preview', {
      method: 'POST',
      body: { kind: kind.value, params: activeParams(params) },
    })
    body.value = payload.body || ''
    message.success(`已生成 ${kind.value} 转换结果`)
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    previewing.value = false
  }
}

function download() {
  if (!body.value) return
  const blob = new Blob([body.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = downloadFilename(kind.value, 'clashsub')
  link.click()
  URL.revokeObjectURL(url)
}

async function saveAsShare() {
  saving.value = true
  error.value = ''
  try {
    const created = await api.request('/api/admin/shares', {
      method: 'POST',
      body: buildShareRequest({
        label: saveLabel.value.trim() || `转换订阅 ${new Date().toLocaleDateString('zh-CN')}`,
        days: saveDays.value || 365,
        allowRaw: true,
        allowClash: true,
      }),
    })
    reveal.urls = shareDialogUrls(created)
    reveal.show = true
    saveLabel.value = ''
    message.success('订阅源已保存，复制链接给客户端即可')
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    saving.value = false
  }
}

async function copyPreview() {
  const ok = await copyText(body.value)
  message[ok ? 'success' : 'error'](ok ? '转换结果已复制' : '自动复制失败，请手动选择文本')
}
</script>

<template>
  <div class="converter-console">
    <p class="converter-console-hint">
      用面板里配置的机场订阅源当场转换，预设与分享链接完全一致；参数只作用在这次预览上。
    </p>

    <n-form label-placement="top" class="converter-console-form" @submit.prevent="preview">
      <n-form-item label="目标格式" :label-props="{ for: 'converter-kind' }">
        <n-select id="converter-kind" v-model:value="kind" :options="KIND_OPTIONS" />
      </n-form-item>
      <n-form-item label="保存备注" :label-props="{ for: 'converter-share-label' }">
        <n-input
          id="converter-share-label"
          v-model:value="saveLabel"
          maxlength="128"
          placeholder="例如：书房路由器"
        />
      </n-form-item>
      <n-form-item label="有效天数" :label-props="{ for: 'converter-share-days' }">
        <n-input-number
          id="converter-share-days"
          v-model:value="saveDays"
          :min="1"
          :max="3650"
        />
      </n-form-item>
    </n-form>

    <convert-param-picker
      :model-value="params"
      hint="参数只作用在这次预览与下载上；分享链接的参数由客户端订阅地址里的查询串决定。"
    />

    <div class="settings-actions">
      <n-button type="primary" :loading="previewing" @click="preview">预览转换结果</n-button>
      <n-button secondary :disabled="!body" @click="download">下载 {{ suffix }} 文件</n-button>
      <n-button secondary :disabled="!body" @click="copyPreview">复制转换结果</n-button>
      <n-button secondary :loading="saving" @click="saveAsShare">保存为订阅源并获取链接</n-button>
    </div>

    <p v-if="error" class="converter-console-error" role="alert">{{ error }}</p>

    <template v-if="body">
      <div class="converter-console-meta">
        <span>{{ kind }} · {{ body.length }} 字符 · 生效参数 {{ activeCount }} 个</span>
        <span v-if="kind === 'surge' || kind === 'surfboard'">
          Surge/Surfboard 预览里的 MANAGED-CONFIG 指向内部地址，正式使用请保存为订阅源
        </span>
      </div>
      <pre class="converter-preview">{{ body }}</pre>
    </template>
  </div>

  <secret-reveal-dialog v-model:show="reveal.show" :urls="reveal.urls" />
</template>
