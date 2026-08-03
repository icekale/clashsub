<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api.js'
import { acceptLogin } from '../session.js'


const route = useRoute()
const router = useRouter()
const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')

function safeDestination(value) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
    ? value
    : '/overview'
}

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const payload = await api.request('/api/auth/login', {
      method: 'POST',
      body: { username: form.username, password: form.password },
    })
    acceptLogin(payload)
    await router.replace(safeDestination(route.query.next))
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-shell">
    <section class="login-context" aria-labelledby="login-product-name">
      <div class="login-brand-row">
        <div class="product-mark product-mark-large" aria-hidden="true">C</div>
        <div>
          <p class="login-kicker">NAS 管理工具</p>
          <h1 id="login-product-name">订阅缓存</h1>
        </div>
      </div>
      <p class="login-intro">
        守住最后一次有效订阅，并把朋友链接的期限、权限和撤销状态放在一个地方。
      </p>
      <p class="login-boundary">
        管理界面默认仅允许局域网访问；开启公网前仍需单独配置 Lucky。
      </p>
    </section>

    <section class="login-panel" aria-labelledby="login-heading">
      <div class="login-panel-heading">
        <h2 id="login-heading">管理员登录</h2>
        <p>使用部署时设置的独立管理凭据。</p>
      </div>
      <n-form :model="form" label-placement="top" size="large" @submit.prevent="submit">
          <n-form-item label="用户名" path="username">
            <n-input
              v-model:value="form.username"
              autocomplete="username"
              maxlength="128"
              placeholder="输入用户名"
              autofocus
            />
          </n-form-item>
          <n-form-item label="密码" path="password">
            <n-input
              v-model:value="form.password"
              type="password"
              show-password-on="mousedown"
              autocomplete="current-password"
              maxlength="1024"
              placeholder="输入密码"
            />
          </n-form-item>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <n-button
            type="primary"
            attr-type="submit"
            block
            :loading="loading"
            :disabled="!form.username || !form.password"
          >
            登录
          </n-button>
      </n-form>
    </section>
  </main>
</template>
