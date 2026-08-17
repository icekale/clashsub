<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { NButton } from 'naive-ui'


const FIELDS = [
  { key: 'raw', prop: 'rawUrl', label: '原始订阅', id: 'one-time-raw-url', copy: '复制原始链接' },
  { key: 'clash', prop: 'clashUrl', label: 'OpenClash 转换', id: 'one-time-clash-url', copy: '复制转换链接' },
  { key: 'clashHa', prop: 'clashHaUrl', label: '仅健康节点', id: 'subscription-clash-ha-url', copy: '复制健康节点链接' },
  { key: 'surge', prop: 'surgeUrl', label: 'Surge 订阅', id: 'subscription-surge-url', copy: '复制 Surge 链接' },
  { key: 'loon', prop: 'loonUrl', label: 'Loon 订阅', id: 'subscription-loon-url', copy: '复制 Loon 链接' },
  { key: 'smart', prop: 'smartUrl', label: '智能订阅', id: 'subscription-smart-url', copy: '复制智能链接' },
]

const props = defineProps({
  show: { type: Boolean, required: true },
  rawUrl: { type: String, required: true },
  clashUrl: { type: String, default: '' },
  clashHaUrl: { type: String, default: '' },
  surgeUrl: { type: String, default: '' },
  loonUrl: { type: String, default: '' },
  smartUrl: { type: String, default: '' },
})
const emit = defineEmits(['update:show'])
const dialog = ref(null)
const fieldEls = ref({})
const closeButton = ref(null)
const copyStatus = ref('')
const focusReturnTarget = ref(null)
const fields = computed(() =>
  FIELDS.filter((field) => props[field.prop]).map((field) => ({
    ...field,
    value: props[field.prop],
  })),
)

function close() {
  emit('update:show', false)
}

function bindField(key) {
  return (el) => {
    fieldEls.value[key] = el
  }
}

function selectField(field) {
  field?.focus()
  field?.select()
}

function trapFocus(event) {
  if (event.key !== 'Tab') return
  const focusable = Array.from(dialog.value?.querySelectorAll(
    'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
  ) || [])
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

async function copy(field) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable')
    await navigator.clipboard.writeText(field.value)
    copyStatus.value = '链接已复制。'
  } catch (_) {
    selectField(fieldEls.value[field.key])
    copyStatus.value = '自动复制失败，链接已选中，请手动复制。'
  }
}

watch(
  () => props.show,
  async (visible) => {
    copyStatus.value = ''
    if (visible) {
      focusReturnTarget.value = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
      await nextTick()
      closeButton.value?.$el?.focus()
    } else if (focusReturnTarget.value) {
      const target = focusReturnTarget.value
      focusReturnTarget.value = null
      await nextTick()
      if (target.isConnected) target.focus()
    }
  },
  { immediate: true },
)
</script>

<template>
  <div v-if="show" class="secret-dialog-backdrop" @keydown.esc="close">
    <section
      ref="dialog"
      class="secret-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="secret-dialog-title"
      aria-describedby="secret-dialog-warning"
      @keydown="trapFocus"
    >
      <div class="secret-dialog-heading">
        <div>
          <h2 id="secret-dialog-title">分享链接</h2>
          <p id="secret-dialog-warning">链接在有效期内可以从分享记录中再次查看。</p>
        </div>
        <n-button ref="closeButton" quaternary aria-label="关闭链接窗口" @click="close">关闭</n-button>
      </div>

      <div v-for="field in fields" :key="field.key" class="secret-field">
        <label :for="field.id">{{ field.label }}</label>
        <textarea
          :id="field.id"
          :ref="bindField(field.key)"
          :value="field.value"
          readonly
          rows="3"
          @focus="$event.target.select()"
        />
        <div class="secret-field-actions">
          <n-button secondary @click="selectField(fieldEls[field.key])">选择文本</n-button>
          <n-button type="primary" @click="copy(field)">{{ field.copy }}</n-button>
        </div>
      </div>

      <p class="copy-status" aria-live="polite">{{ copyStatus }}</p>
    </section>
  </div>
</template>
