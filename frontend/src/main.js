import { createApp } from 'vue'
import {
  create,
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NConfigProvider,
  NDataTable,
  NDialogProvider,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NLayout,
  NLayoutContent,
  NLayoutFooter,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NMessageProvider,
  NModal,
  NPopconfirm,
  NSelect,
  NSkeleton,
  NSpace,
  NSpin,
  NStatistic,
  NSwitch,
  NTag,
} from 'naive-ui'

import App from './App.vue'
import router from './router.js'
import './styles.css'


const naive = create({
  components: [
    NAlert,
    NButton,
    NCard,
    NCheckbox,
    NConfigProvider,
    NDataTable,
    NDialogProvider,
    NDrawer,
    NDrawerContent,
    NForm,
    NFormItem,
    NInput,
    NInputNumber,
    NLayout,
    NLayoutContent,
    NLayoutFooter,
    NLayoutHeader,
    NLayoutSider,
    NMenu,
    NMessageProvider,
    NModal,
    NPopconfirm,
    NSelect,
    NSkeleton,
    NSpace,
    NSpin,
    NStatistic,
    NSwitch,
    NTag,
  ],
})

createApp(App).use(router).use(naive).mount('#app')
