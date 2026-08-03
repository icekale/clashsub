import { defineComponent, ref } from 'vue'


export const ButtonStub = defineComponent({
  name: 'NButton',
  inheritAttrs: false,
  props: {
    attrType: { type: String, default: 'button' },
    disabled: Boolean,
    loading: Boolean,
  },
  emits: ['click'],
  template: `
    <button
      v-bind="$attrs"
      :type="attrType"
      :disabled="disabled || loading"
      @click="$emit('click', $event)"
    ><slot /></button>
  `,
})

export const InputStub = defineComponent({
  name: 'NInput',
  inheritAttrs: false,
  props: {
    value: { type: [String, Number], default: '' },
    type: { type: String, default: 'text' },
    disabled: Boolean,
  },
  emits: ['update:value'],
  template: `
    <input
      v-bind="$attrs"
      :type="type"
      :value="value"
      :disabled="disabled"
      @input="$emit('update:value', $event.target.value)"
    >
  `,
})

export const InputNumberStub = defineComponent({
  name: 'NInputNumber',
  inheritAttrs: false,
  props: {
    value: { type: Number, default: null },
    disabled: Boolean,
  },
  emits: ['update:value'],
  template: `
    <input
      v-bind="$attrs"
      type="number"
      :value="value ?? ''"
      :disabled="disabled"
      @input="$emit('update:value', $event.target.value === '' ? null : Number($event.target.value))"
    >
  `,
})

export const CheckboxStub = defineComponent({
  name: 'NCheckbox',
  inheritAttrs: false,
  props: {
    checked: Boolean,
    disabled: Boolean,
  },
  emits: ['update:checked'],
  template: `
    <label v-bind="$attrs">
      <input
        type="checkbox"
        :checked="checked"
        :disabled="disabled"
        @change="$emit('update:checked', $event.target.checked)"
      >
      <slot />
    </label>
  `,
})

export const SwitchStub = defineComponent({
  name: 'NSwitch',
  inheritAttrs: false,
  props: {
    value: Boolean,
    disabled: Boolean,
  },
  emits: ['update:value'],
  template: `
    <button
      v-bind="$attrs"
      type="button"
      role="switch"
      :aria-checked="String(value)"
      :disabled="disabled"
      @click="$emit('update:value', !value)"
    ><slot /></button>
  `,
})

export const SelectStub = defineComponent({
  name: 'NSelect',
  inheritAttrs: false,
  props: {
    value: { type: [String, Number], default: '' },
    options: { type: Array, default: () => [] },
    disabled: Boolean,
  },
  emits: ['update:value'],
  template: `
    <select
      v-bind="$attrs"
      :value="value"
      :disabled="disabled"
      @change="$emit('update:value', $event.target.value)"
    >
      <option v-for="option in options" :key="option.value" :value="option.value">
        {{ option.label }}
      </option>
    </select>
  `,
})

export const FormItemStub = defineComponent({
  name: 'NFormItem',
  props: { label: { type: String, default: '' } },
  template: '<label><span>{{ label }}</span><slot /></label>',
})

export const AlertStub = defineComponent({
  name: 'NAlert',
  props: { title: { type: String, default: '' } },
  template: '<section role="alert"><strong>{{ title }}</strong><slot /></section>',
})

export const ModalStub = defineComponent({
  name: 'NModal',
  props: { show: Boolean },
  emits: ['update:show'],
  template: '<div v-if="show" class="modal-stub"><slot /></div>',
})

export const PopconfirmStub = defineComponent({
  name: 'NPopconfirm',
  emits: ['positive-click'],
  setup() {
    return { open: ref(false) }
  },
  template: `
    <div class="popconfirm-stub">
      <div @click.capture="open = true"><slot name="trigger" /></div>
      <div v-if="open">
        <slot />
        <button type="button" class="popconfirm-positive" @click="$emit('positive-click')">
          确认操作
        </button>
      </div>
    </div>
  `,
})

const PassthroughStub = defineComponent({ template: '<div><slot /></div>' })

export const viewStubs = {
  NAlert: AlertStub,
  NButton: ButtonStub,
  NCheckbox: CheckboxStub,
  NForm: PassthroughStub,
  NFormItem: FormItemStub,
  NInput: InputStub,
  NInputNumber: InputNumberStub,
  NModal: ModalStub,
  NPopconfirm: PopconfirmStub,
  NSelect: SelectStub,
  NSkeleton: PassthroughStub,
  NSwitch: SwitchStub,
  NTag: PassthroughStub,
}
