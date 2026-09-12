<script setup>
import { computed, reactive, ref } from 'vue'
import { NButton, NModal } from 'naive-ui'

import { CONVERTER_KINDS, CONVERT_PARAMS, copyText, paramChoices, withParams } from '../shareView.js'


const FIELDS = [
  { key: 'raw', label: '原始订阅', id: 'one-time-raw-url', copy: '复制原始链接' },
  { key: 'clash', label: 'OpenClash 转换', id: 'one-time-clash-url', copy: '复制转换链接' },
  { key: 'clashHa', label: '仅健康节点', id: 'subscription-clash-ha-url', copy: '复制健康节点链接' },
  { key: 'surge', label: 'Surge 订阅', id: 'subscription-surge-url', copy: '复制 Surge 链接' },
  { key: 'loon', label: 'Loon 订阅', id: 'subscription-loon-url', copy: '复制 Loon 链接' },
  { key: 'quanx', label: 'Quantumult X 订阅', id: 'subscription-quanx-url', copy: '复制 Quantumult X 链接' },
  { key: 'surfboard', label: 'Surfboard 订阅', id: 'subscription-surfboard-url', copy: '复制 Surfboard 链接' },
  { key: 'singbox', label: 'sing-box 配置', id: 'subscription-singbox-url', copy: '复制 sing-box 链接' },
  { key: 'smart', label: '智能订阅', id: 'subscription-smart-url', copy: '复制智能链接' },
]

const props = defineProps({
  show: { type: Boolean, required: true },
  urls: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:show'])
const fieldEls = ref({})
const copyStatus = ref('')
// 每个参数三态单选：“默认”不传参、其余为显式覆盖；转换参数属于客户端偏好，开窗期间保留。
const params = reactive(Object.fromEntries(CONVERT_PARAMS.map((param) => [param.key, ''])))
const fields = computed(() =>
  FIELDS.filter((field) => props.urls[field.key]).map((field) => ({
    ...field,
    value: CONVERTER_KINDS.includes(field.key)
      ? withParams(props.urls[field.key], params)
      : props.urls[field.key],
  })),
)
const tunable = computed(() => fields.value.some((field) => CONVERTER_KINDS.includes(field.key)))

function resetParams() {
  for (const param of CONVERT_PARAMS) params[param.key] = ''
}

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

      <fieldset v-if="tunable" class="convert-params">
        <legend>转换参数</legend>
        <p class="convert-params-hint">
          只影响转换类链接（Clash、Surge、Loon、Quantumult X、Surfboard、sing-box、智能）；
          原始订阅与仅健康节点链接不会带上这些参数。
        </p>
        <div class="param-list">
          <div
            v-for="param in CONVERT_PARAMS"
            :key="param.key"
            class="param-row"
            role="group"
            :aria-labelledby="`param-${param.key}-label`"
          >
            <span :id="`param-${param.key}-label`" class="param-label">
              {{ param.label }}
              <small>{{ param.hint }}</small>
            </span>
            <div class="param-choices">
              <label
                v-for="choice in paramChoices(param)"
                :key="choice.value || 'default'"
                class="param-choice"
                :class="{ 'param-choice-risk': param.risk && choice.value === 'true' }"
              >
                <input
                  :id="`param-${param.key}-${choice.value || 'default'}`"
                  v-model="params[param.key]"
                  type="radio"
                  :name="`param-${param.key}`"
                  :value="choice.value"
                />
                <span>{{ choice.label }}</span>
              </label>
            </div>
          </div>
        </div>
        <n-button quaternary size="small" @click="resetParams">恢复默认</n-button>
      </fieldset>

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
