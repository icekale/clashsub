<script setup>
import { reactive, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'

import { api } from '../api.js'
import SecretRevealDialog from '../components/SecretRevealDialog.vue'
import { buildShareRequest, CLASH_SHARE_KINDS, copyText, kindLabel, statusLabel } from '../shareView.js'


const message = useMessage()
const items = ref([])
const loading = ref(true)
const refreshing = ref(false)
const submitting = ref(false)
const error = ref('')
const actionKey = ref('')
const renewingId = ref('')
const renewDays = ref(365)
const revealedLinks = reactive({})
const linkErrors = reactive({})
const form = reactive({ label: '', days: 365, allowRaw: true, allowClash: false })
const reveal = reactive({ show: false, urls: {} })

watch(
  () => form.allowClash,
  (enabled) => {
    if (enabled) form.allowRaw = true
  },
)

function formatDate(value) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value * 1000))
}

function statusType(item) {
  if (item.revoked) return 'error'
  if (item.expired) return 'warning'
  return 'success'
}

function showOneTimeLinks(payload) {
  reveal.show = true
  reveal.urls = {
    raw: payload.raw_url,
    clash: payload.clash_url || '',
    clashHa: payload.clash_ha_url || '',
    surge: payload.surge_url || '',
    loon: payload.loon_url || '',
    smart: payload.smart_url || '',
  }
}

function setRevealVisibility(visible) {
  reveal.show = visible
  if (!visible) reveal.urls = {}
}

function clearRevealedLinks(shareId) {
  if (shareId) {
    delete revealedLinks[shareId]
    delete linkErrors[shareId]
    return
  }
  Object.keys(revealedLinks).forEach((id) => delete revealedLinks[id])
  Object.keys(linkErrors).forEach((id) => delete linkErrors[id])
}

async function fetchAllLinks(item) {
  const kinds = item.allow_clash
    ? CLASH_SHARE_KINDS
    : item.allow_raw ? ['raw'] : []
  const results = await Promise.allSettled(kinds.map(async (kind) => [
    kind,
    (await api.request(`/api/admin/shares/${item.id}/reveal`, {
      method: 'POST',
      body: { kind },
    })).url,
  ]))
  const successful = results.filter((result) => result.status === 'fulfilled').map((result) => result.value)
  if (successful.length) {
    revealedLinks[item.id] = {
      ...(revealedLinks[item.id] || {}),
      ...Object.fromEntries(successful),
    }
  }
  linkErrors[item.id] = successful.length !== results.length
  return linkErrors[item.id]
}

async function revealAll(item) {
  actionKey.value = `${item.id}:reveal:all`
  try {
    if (await fetchAllLinks(item)) message.error('部分链接加载失败，请重试')
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    actionKey.value = ''
  }
}

async function copyLink(url) {
  if (await copyText(url)) message.success('链接已复制')
  else message.error('复制失败，请手动复制')
}

async function load() {
  // 首次加载显示骨架屏；已有数据时刷新只转按钮，避免整页闪烁/滚动跳动。
  if (!items.value.length) {
    loading.value = true
  } else {
    refreshing.value = true
  }
  clearRevealedLinks()
  try {
    items.value = await api.request('/api/admin/shares')
    error.value = ''
    for (const item of items.value) {
      if (item.urls && Object.keys(item.urls).length) {
        revealedLinks[item.id] = { ...item.urls }
      }
    }
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function createShare() {
  const payload = buildShareRequest(form)
  if (!payload.label) {
    message.warning('请先填写朋友备注')
    return
  }
  submitting.value = true
  try {
    const created = await api.request('/api/admin/shares', { method: 'POST', body: payload })
    showOneTimeLinks(created)
    form.label = ''
    await load()
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    submitting.value = false
  }
}

async function runAction(item, action, body, successText) {
  actionKey.value = `${item.id}:${action}`
  try {
    const result = await api.request(`/api/admin/shares/${item.id}/${action}`, {
      method: 'POST',
      ...(body ? { body } : {}),
    })
    if (action === 'rotate') showOneTimeLinks(result)
    clearRevealedLinks(item.id)
    message.success(successText)
    await load()
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    actionKey.value = ''
  }
}

async function confirmRenew(item) {
  await runAction(item, 'renew', { days: Number(renewDays.value || 365) }, '到期时间已更新')
  renewingId.value = ''
}

async function deleteShare(item) {
  actionKey.value = `${item.id}:delete`
  try {
    await api.request(`/api/admin/shares/${item.id}`, { method: 'DELETE' })
    clearRevealedLinks(item.id)
    message.success('分享记录已删除')
    await load()
  } catch (requestError) {
    message.error(requestError.message)
  } finally {
    actionKey.value = ''
  }
}

load()
</script>

<template>
  <div class="page-title">
    <div>
      <h1>分享链接</h1>
      <p>每位朋友使用独立密钥；链接在到期、撤销、删除或轮换前都可以重复查看。</p>
    </div>
    <n-button secondary :loading="loading || refreshing" @click="load">刷新列表</n-button>
  </div>

  <n-alert v-if="error" type="error" title="无法读取分享记录" class="section-block">
    {{ error }}
  </n-alert>

  <section class="operation-panel share-create-panel" aria-labelledby="new-share-heading">
    <div class="panel-heading">
      <div>
        <h2 id="new-share-heading">新建分享</h2>
        <p>默认有效 365 天；开启 OpenClash 转换会同时开启原始订阅。</p>
      </div>
    </div>
    <form class="share-form" @submit.prevent="createShare">
      <n-form-item label="朋友备注" :label-props="{ for: 'share-label' }">
        <n-input
          v-model:value="form.label"
          :input-props="{ id: 'share-label' }"
          maxlength="128"
          placeholder="例如：小林的路由器"
        />
      </n-form-item>
      <n-form-item label="有效天数" :label-props="{ for: 'share-days' }">
        <n-input-number
          v-model:value="form.days"
          :input-props="{ id: 'share-days' }"
          :min="1"
          :max="3650"
        />
      </n-form-item>
      <div class="permission-fields">
        <n-checkbox v-model:checked="form.allowRaw" :disabled="form.allowClash">允许原始订阅</n-checkbox>
        <n-checkbox v-model:checked="form.allowClash">允许 OpenClash 转换</n-checkbox>
      </div>
      <n-button type="primary" attr-type="submit" :loading="submitting">创建并显示链接</n-button>
    </form>
  </section>

  <section class="share-section" aria-labelledby="share-list-heading">
    <div class="share-section-heading">
      <h2 id="share-list-heading">现有记录</h2>
      <span>{{ items.length }} 条</span>
    </div>

    <div v-if="loading" class="loading-panel"><n-skeleton text :repeat="4" /></div>
    <div v-else-if="!error && !items.length" class="operation-panel empty-state">
      <strong>还没有分享链接</strong>
      <span>在上方填写备注并创建；关闭一次性窗口前请立即保存链接。</span>
    </div>
    <div v-else class="share-list">
      <article v-for="item in items" :key="item.id" class="share-record">
        <div class="share-record-main">
          <div class="share-identity">
            <div>
              <h3>{{ item.label }}</h3>
              <code>{{ item.id }}</code>
            </div>
            <n-tag :type="statusType(item)" size="small">{{ statusLabel(item) }}</n-tag>
          </div>
          <dl class="share-meta">
            <div><dt>密钥</dt><dd>{{ item.recoverable ? '链接可恢复' : '历史链接不可恢复' }}</dd></div>
            <div><dt>到期</dt><dd>{{ formatDate(item.expires_at) }}</dd></div>
            <div><dt>权限</dt><dd>{{ item.allow_clash ? '原始 + OpenClash' : item.allow_raw ? '仅原始' : '未授权' }}</dd></div>
            <div><dt>访问</dt><dd>{{ item.access_count }} 次</dd></div>
          </dl>
        </div>

        <div v-if="revealedLinks[item.id]" class="historical-links" :data-testid="`historical-links-${item.id}`">
          <div v-for="(url, kind) in revealedLinks[item.id]" :key="kind" class="historical-link-row">
            <span class="historical-link-kind">{{ kindLabel(kind) }}</span>
            <code>{{ url }}</code>
            <n-button text size="small" @click="copyLink(url)">复制</n-button>
          </div>
        </div>
        <span v-if="linkErrors[item.id]" class="action-hint">
          {{ revealedLinks[item.id] ? '部分链接加载失败，可点击“重新获取链接”。' : '链接加载失败，可点击“查看全部链接”重试。' }}
        </span>

        <div v-if="renewingId === item.id && !item.revoked" class="renew-row">
          <label :for="`renew-${item.id}`">从现在起续期天数</label>
          <n-input-number
            v-model:value="renewDays"
            :input-props="{ id: `renew-${item.id}` }"
            :min="1"
            :max="3650"
          />
          <n-button type="primary" :loading="actionKey === `${item.id}:renew`" @click="confirmRenew(item)">确认续期</n-button>
          <n-button quaternary @click="renewingId = ''">取消</n-button>
        </div>

        <div class="share-actions">
          <n-button
            v-if="item.recoverable"
            secondary
            size="small"
            :disabled="item.revoked || item.expired"
            :loading="actionKey === `${item.id}:reveal:all`"
            @click="revealAll(item)"
          >{{ revealedLinks[item.id] ? '重新获取链接' : '查看全部链接' }}</n-button>
          <n-button
            secondary
            size="small"
            :disabled="item.revoked"
            @click="renewingId = item.id; renewDays = 365"
          >
            续期
          </n-button>
          <n-popconfirm
            positive-text="确认撤销"
            negative-text="取消"
            :positive-button-props="{ type: 'warning' }"
            @positive-click="runAction(item, 'revoke', null, '分享已撤销')"
          >
            <template #trigger>
              <n-button secondary size="small" :disabled="item.revoked">撤销</n-button>
            </template>
            撤销后，这条链接会立即返回 404。
          </n-popconfirm>
          <n-popconfirm
            positive-text="确认轮换"
            negative-text="取消"
            @positive-click="runAction(item, 'rotate', null, '密钥已轮换')"
          >
            <template #trigger>
              <n-button secondary size="small" :disabled="item.revoked || item.expired">轮换密钥</n-button>
            </template>
            旧链接会立即失效，新链接仍可在有效期内查看。
          </n-popconfirm>
          <n-popconfirm
            positive-text="确认删除"
            negative-text="取消"
            :positive-button-props="{ type: 'error' }"
            @positive-click="deleteShare(item)"
          >
            <template #trigger>
              <n-button tertiary type="error" size="small">删除记录</n-button>
            </template>
            删除不可恢复；已保存的链接会立即失效。
          </n-popconfirm>
          <span v-if="item.revoked" class="action-hint">已撤销记录只能删除。</span>
          <span v-else-if="item.expired" class="action-hint">请先续期，再轮换密钥。</span>
        </div>
      </article>
    </div>
  </section>

  <SecretRevealDialog
    :show="reveal.show"
    :urls="reveal.urls"
    @update:show="setRevealVisibility"
  />
</template>
