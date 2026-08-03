<script setup>
import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api.js'
import { clearSession, session } from '../session.js'


const route = useRoute()
const router = useRouter()
const message = useMessage()
const drawerOpen = ref(false)
const loggingOut = ref(false)
const menuOptions = [
  { label: '运行概览', key: '/overview' },
  { label: '分享链接', key: '/shares' },
  { label: '设置', key: '/settings' },
  { label: '运行日志', key: '/logs' },
]
const activeKey = computed(() => menuOptions.find((item) => route.path.startsWith(item.key))?.key || '/overview')

function navigate(key) {
  drawerOpen.value = false
  router.push(key)
}

async function logout() {
  loggingOut.value = true
  try {
    await api.request('/api/auth/logout', { method: 'POST' })
    clearSession()
    await router.replace('/login')
  } catch (error) {
    message.error(error.message)
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <n-layout class="admin-shell">
    <n-layout-header bordered class="top-header">
      <div class="header-leading">
        <button
          class="mobile-menu-button"
          type="button"
          aria-label="打开导航"
          @click="drawerOpen = true"
        >
          <span aria-hidden="true">☰</span>
        </button>
        <div class="product-mark" aria-hidden="true">C</div>
        <div>
          <div class="brand-name">订阅缓存</div>
          <div class="brand-context">稳定订阅 · 本地优先</div>
        </div>
      </div>
      <div class="header-actions">
        <span class="signed-in-user">{{ session.username }}</span>
        <n-button text :loading="loggingOut" @click="logout">退出</n-button>
      </div>
    </n-layout-header>

    <n-layout has-sider class="admin-body">
      <n-layout-sider bordered :width="220" class="desktop-nav">
        <div class="navigation-label">管理</div>
        <n-menu :value="activeKey" :options="menuOptions" @update:value="navigate" />
        <div class="navigation-note">
          公网默认关闭。Lucky 的域名与证书需要单独配置。
        </div>
      </n-layout-sider>

      <n-layout class="main-column">
        <n-layout-content class="content-wrap">
          <main class="content-inner">
            <router-view />
          </main>
        </n-layout-content>
        <n-layout-footer class="app-footer">
          <span>最后有效缓存会在上游故障时继续提供服务</span>
          <span class="footer-mode">LAN first</span>
        </n-layout-footer>
      </n-layout>
    </n-layout>
  </n-layout>

  <n-drawer v-model:show="drawerOpen" placement="left" :width="280">
    <n-drawer-content title="订阅缓存" closable>
      <n-menu :value="activeKey" :options="menuOptions" @update:value="navigate" />
      <p class="drawer-note">公网默认关闭。Lucky 配置与本应用开关彼此独立。</p>
    </n-drawer-content>
  </n-drawer>
</template>
