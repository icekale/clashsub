<script setup>
import { NButton } from 'naive-ui'

import { CONVERT_PARAMS, paramChoices } from '../shareView.js'


// 参数三态单选：“默认”不传参、其余为显式覆盖。值直接挂在父级传进来的对象上，
// 这样分享弹窗和转换面板可以共用同一套勾选 UI。
const props = defineProps({
  modelValue: { type: Object, required: true },
  hint: {
    type: String,
    default:
      '只影响转换类链接（Clash、Surge、Loon、Quantumult X、Surfboard、sing-box、智能）；' +
      '原始订阅与仅健康节点链接不会带上这些参数。',
  },
})

function reset() {
  for (const param of CONVERT_PARAMS) props.modelValue[param.key] = ''
}
</script>

<template>
  <fieldset class="convert-params">
    <legend>转换参数</legend>
    <p class="convert-params-hint">{{ hint }}</p>
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
        <select
          v-if="param.select"
          :id="`param-${param.key}`"
          v-model="modelValue[param.key]"
          class="param-select"
        >
          <option
            v-for="choice in paramChoices(param)"
            :key="choice.value || 'default'"
            :value="choice.value"
          >
            {{ choice.label }}
          </option>
        </select>
        <div v-else class="param-choices">
          <label
            v-for="choice in paramChoices(param)"
            :key="choice.value || 'default'"
            class="param-choice"
            :class="{ 'param-choice-risk': param.risk && choice.value === 'true' }"
          >
            <input
              :id="`param-${param.key}-${choice.value || 'default'}`"
              v-model="modelValue[param.key]"
              type="radio"
              :name="`param-${param.key}`"
              :value="choice.value"
            />
            <span>{{ choice.label }}</span>
          </label>
        </div>
      </div>
    </div>
    <n-button quaternary size="small" @click="reset">恢复默认</n-button>
  </fieldset>
</template>
