// 测试环境必须强制使用 vue 的非生产构建：若宿主环境导出 NODE_ENV=production
//（例如本机 shell），vue/@vue/* 会经各自 index.js 加载生产构建，其中
// transformVNodeArgs 为空实现，导致 @vue/test-utils 的 stub 机制静默失效
//（组件渲染为 <!----> 注释节点）。该赋值在 vitest 启动时生效并继承到所有 worker。
process.env.NODE_ENV = 'test'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/app/',
  plugins: [vue()],
  build: {
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router'],
          'naive-ui': ['naive-ui'],
        },
      },
    },
  },
  test: { environment: 'jsdom' },
})
