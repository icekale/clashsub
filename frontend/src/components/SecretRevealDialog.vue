<script setup>
import { computed, ref } from 'vue'
import { NButton, NModal } from 'naive-ui'

import { copyText } from '../shareView.js'


const FIELDS = [
  { key: 'raw', label: '原始订阅', id: 'one-time-raw-url', copy: '复制原始链接' },
  { key: 'clash', label: 'OpenClash 转换', id: 'one-time-clash-url', copy: '复制转换链接' },
  { key: 'clashHa', label: '仅健康节点', id: 'subscription-clash-ha-url', copy: '复制健康节点链接' },
  { key: 'surge', label: 'Surge 订阅', id: 'subscription-surge-url', copy: '复制 Surge 链接' },
  { key: 'loon', label: 'Loon 订阅', id: 'subscription-loon-url', copy: '复制 Loon 链接' },
  { key: 'smart', label: '智能订阅', id: 'subscription-smart-url', copy: '复制智能链接' },
]

const props = defineProps({
  show: { type: Boolean, required: true },
  urls: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:show'])
const fieldEls = ref({})
const copyStatus = ref('')
const fields = computed(() =>
  FIELDS.filter((field) => props.urls[field.key]).map((field) => ({
    ...field,
    value: props.urls[field.key],
  })),
)

function close() {
  emit('update:show', false)
}

async function copy(field) {
  const ok = await copyText(field.value)
  copyStatus.value = ok ? '链接已复制。' : '自动复制失败，链接已选中，请手动复制。'
  if (!ok) fieldEls.value[field.key]?.select()
}
</script>

<template>
  <n-modal :show="show" @update:show="emit('update:show', $event)">
    <section
      class="secret-dialog"
      role="dialog"
      aria-labelledby="secret-dialog-title"
      aria-describedby="secret-dialog-warning"
    >
      <div class="secret-dialog-heading">
        <div>
          <h2 id="secret-dialog-title">分享链接</h2>
          <p id="secret-dialog-warning">链接在有效期内可以从分享记录中再次查看。</p>
        </div>
        <n-button quaternary aria-label="关闭链接窗口" @click="close">关闭</n-button>
      </div>

      <div v-for="field in fields" :key="field.key" class="secret-field">
        <label :for="field.id">{{ field.label }}</label>
        <textarea
          :id="field.id"
          :ref="(el) => { fieldEls[field.key] = el }"
          :value="field.value"
          readonly
          rows="3"
          @focus="$event.target.select()"
        />
        <div class="secret-field-actions">
          <n-button type="primary" @click="copy(field)">{{ field.copy }}</n-button>
        </div>
      </div>

      <p class="copy-status" aria-live="polite">{{ copyStatus }}</p>
    </section>
  </n-modal>
</template>
