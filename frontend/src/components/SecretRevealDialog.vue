<script setup>
import { nextTick, ref, watch } from 'vue'
import { NButton } from 'naive-ui'


const props = defineProps({
  show: { type: Boolean, required: true },
  rawUrl: { type: String, required: true },
  clashUrl: { type: String, default: '' },
  surgeUrl: { type: String, default: '' },
  loonUrl: { type: String, default: '' },
  smartUrl: { type: String, default: '' },
})
const emit = defineEmits(['update:show'])
const dialog = ref(null)
const rawField = ref(null)
const clashField = ref(null)
const surgeField = ref(null)
const loonField = ref(null)
const smartField = ref(null)
const closeButton = ref(null)
const copyStatus = ref('')
const focusReturnTarget = ref(null)

function close() {
  emit('update:show', false)
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

async function copy(value, field) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable')
    await navigator.clipboard.writeText(value)
    copyStatus.value = '链接已复制。'
  } catch (_) {
    selectField(field)
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
        <n-button ref="closeButton" quaternary aria-label="关闭一次性链接" @click="close">关闭</n-button>
      </div>

      <div class="secret-field">
        <label for="one-time-raw-url">原始订阅</label>
        <textarea
          id="one-time-raw-url"
          ref="rawField"
          :value="rawUrl"
          readonly
          rows="3"
          @focus="$event.target.select()"
        />
        <div class="secret-field-actions">
          <n-button secondary @click="selectField(rawField)">选择文本</n-button>
          <n-button type="primary" @click="copy(rawUrl, rawField)">复制原始链接</n-button>
        </div>
      </div>

      <div v-if="clashUrl" class="secret-field">
        <label for="one-time-clash-url">OpenClash 转换</label>
        <textarea
          id="one-time-clash-url"
          ref="clashField"
          :value="clashUrl"
          readonly
          rows="3"
          @focus="$event.target.select()"
        />
        <div class="secret-field-actions">
          <n-button secondary @click="selectField(clashField)">选择文本</n-button>
          <n-button type="primary" @click="copy(clashUrl, clashField)">复制转换链接</n-button>
        </div>
      </div>

      <div v-if="surgeUrl" class="secret-field">
        <label for="subscription-surge-url">Surge 订阅</label>
        <textarea id="subscription-surge-url" ref="surgeField" :value="surgeUrl" readonly rows="3" @focus="$event.target.select()" />
        <div class="secret-field-actions">
          <n-button secondary @click="selectField(surgeField)">选择文本</n-button>
          <n-button type="primary" @click="copy(surgeUrl, surgeField)">复制 Surge 链接</n-button>
        </div>
      </div>

      <div v-if="loonUrl" class="secret-field">
        <label for="subscription-loon-url">Loon 订阅</label>
        <textarea id="subscription-loon-url" ref="loonField" :value="loonUrl" readonly rows="3" @focus="$event.target.select()" />
        <div class="secret-field-actions">
          <n-button secondary @click="selectField(loonField)">选择文本</n-button>
          <n-button type="primary" @click="copy(loonUrl, loonField)">复制 Loon 链接</n-button>
        </div>
      </div>

      <div v-if="smartUrl" class="secret-field">
        <label for="subscription-smart-url">智能订阅</label>
        <textarea id="subscription-smart-url" ref="smartField" :value="smartUrl" readonly rows="3" @focus="$event.target.select()" />
        <div class="secret-field-actions">
          <n-button secondary @click="selectField(smartField)">选择文本</n-button>
          <n-button type="primary" @click="copy(smartUrl, smartField)">复制智能链接</n-button>
        </div>
      </div>

      <p class="copy-status" aria-live="polite">{{ copyStatus }}</p>
    </section>
  </div>
</template>
