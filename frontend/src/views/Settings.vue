<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'

import { api } from '../api.js'
import { clearSession, session } from '../session.js'


const router = useRouter()
const message = useMessage()
const loading = ref(true)
const loaded = ref(false)
const saving = ref(false)
const changingCredentials = ref(false)
const testingUpstream = ref(false)
const savingAirportCredentials = ref(false)
const testingOpenClash = ref(false)
const savingOpenClashSecret = ref(false)
const error = ref('')
const credentialError = ref('')
const airportCredentialError = ref('')
const openclashError = ref('')
const upstreamStatus = ref(null)
const upstreamTestResult = ref(null)
const openclashTestResult = ref(null)
const openclashSecret = ref('')
const openclashSecretConfigured = ref(false)
const publicRiskOpen = ref(false)
const publicRiskChecked = ref(false)
const publicAcknowledged = ref(false)
const accessModeOptions = [
  { label: '仅局域网', value: 'lan' },
  { label: '公网（需要 Lucky）', value: 'public' },
]
const form = reactive({
  refresh_interval_minutes: 60,
  access_mode: 'lan',
  lan_base_url: '',
  public_base_url: '',
  converter_enabled: false,
  openclash_enabled: false,
  openclash_api_url: '',
  openclash_provider: '',
  health_enabled: false,
  health_interval_seconds: 600,
  health_timeout_seconds: 5,
  health_refresh_enabled: false,
  health_refresh_online_ratio: 0.5,
  health_refresh_cooldown_minutes: 10,
})
const original = reactive({ ...form })
const credentials = reactive({
  current_password: '',
  new_username: session.username || '',
  new_password: '',
  confirmation: '',
})
const airportCredentials = reactive({ username: '', password: '', password_configured: false })

const baseUrlChanged = computed(
  () => form.lan_base_url !== original.lan_base_url
    || form.public_base_url !== original.public_base_url,
)

function settingsFields(payload) {
  return {
    refresh_interval_minutes: payload.refresh_interval_minutes,
    access_mode: payload.access_mode,
    lan_base_url: payload.lan_base_url || '',
    public_base_url: payload.public_base_url || '',
    converter_enabled: Boolean(payload.converter_enabled),
    openclash_enabled: Boolean(payload.openclash_enabled),
    openclash_api_url: payload.openclash_api_url || '',
    openclash_provider: payload.openclash_provider || '',
    health_enabled: Boolean(payload.health_enabled),
    health_interval_seconds: payload.health_interval_seconds || 600,
    health_timeout_seconds: payload.health_timeout_seconds || 5,
    health_refresh_enabled: Boolean(payload.health_refresh_enabled),
    health_refresh_online_ratio: payload.health_refresh_online_ratio ?? 0.5,
    health_refresh_cooldown_minutes: payload.health_refresh_cooldown_minutes || 10,
  }
}

function applySettings(payload) {
  const fields = settingsFields(payload)
  Object.assign(form, fields)
  Object.assign(original, fields)
  publicAcknowledged.value = false
  publicRiskChecked.value = false
}

async function load() {
  loading.value = true
  try {
    const [settingsPayload, statusPayload, airportPayload, openclashPayload] = await Promise.all([
      api.request('/api/admin/settings'),
      api.request('/api/admin/upstream/status'),
      api.request('/api/admin/upstream/credentials'),
      api.request('/api/admin/openclash/credentials'),
    ])
    applySettings(settingsPayload)
    upstreamStatus.value = statusPayload
    if (airportPayload) {
      airportCredentials.username = airportPayload.username || ''
      airportCredentials.password = ''
      airportCredentials.password_configured = Boolean(airportPayload.password_configured)
    }
    openclashSecretConfigured.value = Boolean(openclashPayload?.configured)
    openclashSecret.value = ''
    loaded.value = true
    error.value = ''
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
  }
}

async function saveOpenClashSecret() {
  openclashError.value = ''
  if (!openclashSecret.value.trim()) {
    openclashError.value = '请输入 OpenClash API 密钥。'
    return
  }
  savingOpenClashSecret.value = true
  try {
    await api.request('/api/admin/openclash/credentials', {
      method: 'PUT',
      body: { secret: openclashSecret.value },
    })
    openclashSecret.value = ''
    openclashSecretConfigured.value = true
    message.success('OpenClash API 密钥已保存（加密存储）')
  } catch (requestError) {
    openclashError.value = requestError.message
    message.error(requestError.message)
  } finally {
    savingOpenClashSecret.value = false
  }
}

async function testOpenClash() {
  openclashError.value = ''
  const apiUrl = form.openclash_api_url.trim()
  const secret = openclashSecret.value.trim()
  if (!apiUrl) {
    openclashError.value = '请先填写 OpenClash API 地址。'
    return
  }
  if (!secret) {
    openclashError.value = '请先输入 OpenClash API 密钥再测试。'
    return
  }
  testingOpenClash.value = true
  openclashTestResult.value = null
  try {
    const result = await api.request('/api/admin/openclash/test', {
      method: 'POST',
      body: { api_url: apiUrl, secret },
    })
    openclashTestResult.value = result
    message.success(`OpenClash 连接成功（${result.version || '未知版本'}）`)
  } catch (requestError) {
    openclashError.value = requestError.message
    message.error(requestError.message)
  } finally {
    testingOpenClash.value = false
  }
}

async function saveAirportCredentials() {
  airportCredentialError.value = ''
  const username = airportCredentials.username.trim()
  if (!username) {
    airportCredentialError.value = '请输入机场用户名。'
    return
  }
  savingAirportCredentials.value = true
  try {
    const body = { username }
    if (airportCredentials.password) body.password = airportCredentials.password
    const result = await api.request('/api/admin/upstream/credentials', {
      method: 'PUT',
      body,
    })
    airportCredentials.username = username
    airportCredentials.password = ''
    airportCredentials.password_configured = true
    message.success(`机场凭据验证成功，已获取 ${result.node_count} 个节点`)
    upstreamTestResult.value = {
      ok: true,
      node_count: result.node_count,
      error_category: null,
      expires_at: null,
    }
  } catch (requestError) {
    airportCredentialError.value = requestError.message
    message.error(requestError.message)
  } finally {
    savingAirportCredentials.value = false
  }
}

async function testUpstream() {
  if (!upstreamStatus.value?.protocol_configured) return
  testingUpstream.value = true
  upstreamTestResult.value = null
  try {
    const result = await api.request('/api/admin/upstream/test', { method: 'POST' })
    upstreamTestResult.value = result
    message[result.ok ? 'success' : 'warning'](
      result.ok ? '机场连接测试成功' : '机场连接测试失败',
    )
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    testingUpstream.value = false
  }
}

function requestAccessMode(value) {
  if (value === 'public' && form.access_mode !== 'public') {
    publicRiskChecked.value = false
    publicRiskOpen.value = true
    return
  }
  form.access_mode = value
  if (value !== 'public') publicAcknowledged.value = false
}

function cancelPublicMode() {
  publicRiskOpen.value = false
  publicRiskChecked.value = false
}

function confirmPublicMode() {
  if (!publicRiskChecked.value) return
  form.access_mode = 'public'
  publicAcknowledged.value = true
  publicRiskOpen.value = false
}

async function redirectToLogin() {
  clearSession()
  await router.replace('/login')
}

async function saveSettings() {
  saving.value = true
  error.value = ''
  try {
    const result = await api.request('/api/admin/settings', {
      method: 'PUT',
      body: {
        ...settingsFields(form),
        public_acknowledged: form.access_mode === 'public'
          && original.access_mode !== 'public'
          && publicAcknowledged.value,
      },
    })
    if (result.reauthenticate) {
      await redirectToLogin()
      return
    }
    applySettings(result)
    message.success('运行设置已保存')
  } catch (requestError) {
    error.value = requestError.message
    message.error(requestError.message)
  } finally {
    saving.value = false
  }
}

async function changeCredentials() {
  credentialError.value = ''
  if (!credentials.current_password || !credentials.new_username || !credentials.new_password) {
    credentialError.value = '请填写当前密码、新用户名和新密码。'
    return
  }
  if (credentials.new_password !== credentials.confirmation) {
    credentialError.value = '两次输入的新密码不一致。'
    return
  }

  changingCredentials.value = true
  try {
    await api.request('/api/auth/credentials', {
      method: 'PUT',
      body: {
        current_password: credentials.current_password,
        new_username: credentials.new_username.trim(),
        new_password: credentials.new_password,
      },
    })
    Object.assign(credentials, {
      current_password: '',
      new_username: '',
      new_password: '',
      confirmation: '',
    })
    await redirectToLogin()
  } catch (requestError) {
    credentialError.value = requestError.message
  } finally {
    changingCredentials.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-title">
    <div>
      <h1>设置</h1>
      <p>配置刷新周期、访问范围、转换能力和管理员凭据。</p>
    </div>
    <n-button secondary :loading="loading" @click="load">重新读取</n-button>
  </div>

  <n-alert v-if="error" type="error" title="设置操作未完成" class="section-block">
    {{ error }}
  </n-alert>

  <div v-if="loading" class="loading-panel" aria-label="正在加载设置">
    <n-skeleton text :repeat="4" />
  </div>

  <div v-else-if="loaded" class="settings-stack">
    <section class="operation-panel" aria-labelledby="runtime-settings-heading">
      <div class="panel-heading">
        <div>
          <h2 id="runtime-settings-heading">运行与访问</h2>
          <p>Lucky 代理配置与这里的公网开关彼此独立；订阅仅在客户端请求且缓存超过间隔时自动拉取，另有每日兜底刷新。</p>
        </div>
      </div>

      <n-form :model="form" label-placement="top" class="settings-form-grid">
        <n-form-item
          label="按需刷新间隔（分钟）"
          path="refresh_interval_minutes"
          :label-props="{ for: 'settings-refresh-interval' }"
        >
          <n-input-number
            v-model:value="form.refresh_interval_minutes"
            :input-props="{ id: 'settings-refresh-interval' }"
            :min="1"
            :max="1440"
          />
        </n-form-item>
        <n-form-item
          label="访问模式"
          path="access_mode"
          :label-props="{ for: 'settings-access-mode' }"
        >
          <n-select
            data-testid="access-mode"
            :value="form.access_mode"
            :options="accessModeOptions"
            :input-props="{ id: 'settings-access-mode' }"
            filterable
            @update:value="requestAccessMode"
          />
        </n-form-item>
        <n-form-item
          label="局域网 Base URL"
          path="lan_base_url"
          :label-props="{ for: 'settings-lan-base-url' }"
        >
          <n-input
            v-model:value="form.lan_base_url"
            data-testid="lan-base-url"
            :input-props="{ id: 'settings-lan-base-url' }"
            placeholder="http://nas.lan:18080"
            maxlength="2048"
          />
        </n-form-item>
        <n-form-item
          label="公网 Base URL"
          path="public_base_url"
          :label-props="{ for: 'settings-public-base-url' }"
        >
          <n-input
            v-model:value="form.public_base_url"
            data-testid="public-base-url"
            :input-props="{ id: 'settings-public-base-url' }"
            placeholder="https://sub.example.com"
            maxlength="2048"
          />
        </n-form-item>
      </n-form>

      <div class="settings-switch-row">
        <div>
          <strong>全局订阅转换</strong>
          <span>转换会把原始订阅地址发送给已配置的转换服务，并生成 Clash、Surge、Loon 格式。</span>
        </div>
        <n-switch v-model:value="form.converter_enabled" aria-label="全局订阅转换" />
      </div>

      <n-alert
        v-if="baseUrlChanged"
        type="warning"
        title="Base URL 变更不会改写已分发链接"
        class="base-url-warning"
      >
        数据库只保存密钥哈希，旧链接无法恢复。朋友可只替换已保存 URL 的 origin；
        否则请由管理员轮换并重新分发密钥。
      </n-alert>

      <div class="settings-actions">
        <n-button type="primary" :loading="saving" @click="saveSettings">保存运行设置</n-button>
      </div>
    </section>

    <section class="operation-panel" aria-labelledby="upstream-settings-heading">
      <div class="panel-heading">
        <div>
          <h2 id="upstream-settings-heading">机场订阅源</h2>
          <p>连接配置只从 Docker Secret 读取，此处仅展示脱敏状态。</p>
        </div>
        <n-button
          secondary
          :loading="testingUpstream"
          :disabled="!upstreamStatus?.protocol_configured"
          @click="testUpstream"
        >
          测试机场连接
        </n-button>
      </div>

      <dl v-if="upstreamStatus" class="fact-grid">
        <div>
          <dt>API Base URL</dt>
          <dd>{{ upstreamStatus.api_base_url || '未配置' }}</dd>
        </div>
        <div>
          <dt>协议状态</dt>
          <dd>{{ upstreamStatus.protocol_configured ? '协议配置完整' : '协议配置不完整' }}</dd>
        </div>
        <div>
          <dt>邮箱 Secret</dt>
          <dd>{{ upstreamStatus.email_configured ? '邮箱 Secret 已配置' : '邮箱 Secret 未配置' }}</dd>
        </div>
        <div>
          <dt>密码 Secret</dt>
          <dd>{{ upstreamStatus.password_configured ? '密码 Secret 已配置' : '密码 Secret 未配置' }}</dd>
        </div>
        <div>
          <dt>备用订阅</dt>
          <dd>{{ upstreamStatus.fallback_configured ? '备用订阅已配置' : '备用订阅未配置' }}</dd>
        </div>
      </dl>

      <div v-if="upstreamTestResult" class="timeline-facts" aria-live="polite">
        <div>
          <span>连接测试</span>
          <strong>{{ upstreamTestResult.ok ? '协议连接成功' : '协议连接失败' }}</strong>
        </div>
        <div v-if="upstreamTestResult.error_category">
          <span>失败类别</span>
          <strong>{{ upstreamTestResult.error_category }}</strong>
        </div>
        <div v-if="upstreamTestResult.node_count">
          <span>节点数量</span>
          <strong>{{ upstreamTestResult.node_count }}</strong>
        </div>
      </div>

      <n-form
        :model="airportCredentials"
        label-placement="top"
        class="airport-credentials-form"
        @submit.prevent="saveAirportCredentials"
      >
        <n-form-item label="机场用户名" :label-props="{ for: 'airport-username' }">
          <n-input
            v-model:value="airportCredentials.username"
            :input-props="{ id: 'airport-username', autocomplete: 'username' }"
            maxlength="320"
            placeholder="输入机场用户名"
          />
        </n-form-item>
        <n-form-item label="机场密码" :label-props="{ for: 'airport-password' }">
          <n-input
            v-model:value="airportCredentials.password"
            type="password"
            :input-props="{ id: 'airport-password', autocomplete: 'current-password' }"
            maxlength="1024"
            placeholder="留空表示沿用当前密码"
            show-password-on="mousedown"
          />
        </n-form-item>
        <div class="credential-actions">
          <n-button type="primary" attr-type="submit" :loading="savingAirportCredentials">
            验证并保存机场凭据
          </n-button>
        </div>
      </n-form>
      <p v-if="airportCredentialError" class="form-error credential-error" role="alert">
        {{ airportCredentialError }}
      </p>
    </section>

    <section class="operation-panel" aria-labelledby="openclash-settings-heading">
      <div class="panel-heading">
        <div>
          <h2 id="openclash-settings-heading">OpenClash 联动与节点健康</h2>
          <p>上游刷新成功后自动推送 OpenClash 重新拉取 provider；健康检查会定期探测节点并生成仅含可用节点的订阅。</p>
        </div>
      </div>

      <n-alert v-if="openclashError" type="error" class="section-block">
        {{ openclashError }}
      </n-alert>

      <div class="settings-switch-row">
        <div>
          <strong>OpenClash 自动推送</strong>
          <span>每次 ClashSub 成功刷新机场订阅后，调用 OpenClash API 强制刷新 provider，节点列表即时更新。</span>
        </div>
        <n-switch v-model:value="form.openclash_enabled" aria-label="OpenClash 自动推送" />
      </div>

      <n-form :model="form" label-placement="top" class="settings-form-grid">
        <n-form-item
          label="OpenClash API 地址"
          :label-props="{ for: 'settings-openclash-api-url' }"
        >
          <n-input
            v-model:value="form.openclash_api_url"
            :input-props="{ id: 'settings-openclash-api-url' }"
            placeholder="http://192.168.1.1:9090"
            maxlength="2048"
          />
        </n-form-item>
        <n-form-item
          label="Provider 名称"
          :label-props="{ for: 'settings-openclash-provider' }"
        >
          <n-input
            v-model:value="form.openclash_provider"
            :input-props="{ id: 'settings-openclash-provider' }"
            placeholder="Provider_988009"
            maxlength="256"
          />
        </n-form-item>
        <n-form-item
          label="API 密钥"
          :label-props="{ for: 'settings-openclash-secret' }"
        >
          <n-input
            v-model:value="openclashSecret"
            type="password"
            :input-props="{ id: 'settings-openclash-secret', autocomplete: 'new-password' }"
            placeholder="留空沿用已保存的密钥"
            show-password-on="mousedown"
            maxlength="256"
          />
        </n-form-item>
      </n-form>

      <div class="settings-actions">
        <n-button
          secondary
          :loading="testingOpenClash"
          @click="testOpenClash"
        >
          测试连接
        </n-button>
        <n-button
          secondary
          :loading="savingOpenClashSecret"
          @click="saveOpenClashSecret"
        >
          {{ openclashSecretConfigured ? '更新密钥' : '保存密钥' }}
        </n-button>
      </div>

      <div v-if="openclashTestResult" class="timeline-facts" aria-live="polite">
        <div>
          <span>OpenClash 连接</span>
          <strong>成功（{{ openclashTestResult.version || '未知版本' }}）</strong>
        </div>
      </div>

      <n-divider />

      <div class="settings-switch-row">
        <div>
          <strong>节点健康检查</strong>
          <span>按间隔探测节点连通性（TCP/TLS 握手），结果用于 WebUI 展示和“仅健康节点”订阅过滤。</span>
        </div>
        <n-switch v-model:value="form.health_enabled" aria-label="节点健康检查" />
      </div>

      <div class="settings-switch-row">
        <div>
          <strong>节点大面积不可用时自动刷新缓存</strong>
          <span>健康检查发现在线节点比例低于阈值时，自动重新激活并拉取机场订阅，刷新成功后立即推送 OpenClash。</span>
        </div>
        <n-switch v-model:value="form.health_refresh_enabled" aria-label="不可用时自动刷新缓存" />
      </div>

      <n-form :model="form" label-placement="top" class="settings-form-grid">
        <n-form-item
          label="检查间隔（秒）"
          :label-props="{ for: 'settings-health-interval' }"
        >
          <n-input-number
            v-model:value="form.health_interval_seconds"
            :input-props="{ id: 'settings-health-interval' }"
            :min="30"
            :max="86400"
          />
        </n-form-item>
        <n-form-item
          label="探测超时（秒）"
          :label-props="{ for: 'settings-health-timeout' }"
        >
          <n-input-number
            v-model:value="form.health_timeout_seconds"
            :input-props="{ id: 'settings-health-timeout' }"
            :min="1"
            :max="30"
          />
        </n-form-item>
        <n-form-item
          label="自动刷新阈值（在线比例）"
          :label-props="{ for: 'settings-health-refresh-ratio' }"
        >
          <n-input-number
            v-model:value="form.health_refresh_online_ratio"
            :input-props="{ id: 'settings-health-refresh-ratio' }"
            :min="0.1"
            :max="1"
            :step="0.05"
          />
        </n-form-item>
        <n-form-item
          label="自动刷新冷却（分钟）"
          :label-props="{ for: 'settings-health-refresh-cooldown' }"
        >
          <n-input-number
            v-model:value="form.health_refresh_cooldown_minutes"
            :input-props="{ id: 'settings-health-refresh-cooldown' }"
            :min="1"
            :max="1440"
          />
        </n-form-item>
      </n-form>

      <div class="settings-actions">
        <n-button type="primary" :loading="saving" @click="saveSettings">保存运行设置</n-button>
      </div>
    </section>

    <section class="operation-panel" aria-labelledby="credential-settings-heading">
      <div class="panel-heading">
        <div>
          <h2 id="credential-settings-heading">管理员凭据</h2>
          <p>更新成功会撤销全部登录会话，并要求使用新凭据重新登录。</p>
        </div>
      </div>

      <n-form
        :model="credentials"
        label-placement="top"
        class="credentials-form-grid"
        @submit.prevent="changeCredentials"
      >
          <n-form-item
            label="当前密码"
            path="current_password"
            :label-props="{ for: 'admin-current-password' }"
          >
            <n-input
              v-model:value="credentials.current_password"
              data-testid="current-password"
              type="password"
              :input-props="{ id: 'admin-current-password', autocomplete: 'current-password' }"
              show-password-on="mousedown"
              maxlength="1024"
              placeholder="输入当前密码"
            />
          </n-form-item>
          <n-form-item
            label="新用户名"
            path="new_username"
            :label-props="{ for: 'admin-new-username' }"
          >
            <n-input
              v-model:value="credentials.new_username"
              data-testid="new-username"
              :input-props="{ id: 'admin-new-username', autocomplete: 'username' }"
              maxlength="128"
              placeholder="输入新用户名"
            />
          </n-form-item>
          <n-form-item
            label="新密码"
            path="new_password"
            :label-props="{ for: 'admin-new-password' }"
          >
            <n-input
              v-model:value="credentials.new_password"
              data-testid="new-password"
              type="password"
              :input-props="{ id: 'admin-new-password', autocomplete: 'new-password' }"
              show-password-on="mousedown"
              maxlength="1024"
              placeholder="输入新密码"
            />
          </n-form-item>
          <n-form-item
            label="再次输入新密码"
            path="confirmation"
            :label-props="{ for: 'admin-confirm-password' }"
          >
            <n-input
              v-model:value="credentials.confirmation"
              data-testid="confirm-password"
              type="password"
              :input-props="{ id: 'admin-confirm-password', autocomplete: 'new-password' }"
              show-password-on="mousedown"
              maxlength="1024"
              placeholder="再次输入新密码"
            />
          </n-form-item>
        <div class="credential-actions">
          <n-button type="primary" attr-type="submit" :loading="changingCredentials">
            更新管理员凭据
          </n-button>
        </div>
      </n-form>
      <p v-if="credentialError" class="form-error credential-error" role="alert">
        {{ credentialError }}
      </p>
    </section>
  </div>

  <n-modal
    v-model:show="publicRiskOpen"
    :mask-closable="false"
    :close-on-esc="false"
  >
    <section
      class="public-risk-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="public-risk-heading"
    >
      <h2 id="public-risk-heading">确认公网暴露边界</h2>
      <p>应用不会自动配置 Lucky。继续前请确认以下三项事实：</p>
      <ol class="risk-facts">
        <li>Lucky 必须已代理公网域名，并正确终止 HTTPS。</li>
        <li>当前管理员凭据会暴露到互联网，需按公网凭据保护。</li>
        <li>Lucky access log 可能记录包含分享 token 的路径。</li>
      </ol>
      <n-checkbox v-model:checked="publicRiskChecked" class="public-risk-check">
        我已核对以上三项，并接受公网暴露风险。
      </n-checkbox>
      <div class="public-risk-actions">
        <n-button secondary @click="cancelPublicMode">保持局域网模式</n-button>
        <n-button
          type="warning"
          :disabled="!publicRiskChecked"
          @click="confirmPublicMode"
        >
          确认切换到公网
        </n-button>
      </div>
    </section>
  </n-modal>
</template>
