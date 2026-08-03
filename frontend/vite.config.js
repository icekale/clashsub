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
