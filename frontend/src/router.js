import { createRouter, createWebHistory } from 'vue-router'

import { restoreSession, session } from './session.js'


const routes = [
  {
    path: '/login',
    component: () => import('./views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('./layouts/AdminLayout.vue'),
    children: [
      { path: '', redirect: '/overview' },
      { path: 'overview', component: () => import('./views/Overview.vue') },
      { path: 'shares', component: () => import('./views/Shares.vue') },
      { path: 'settings', component: () => import('./views/Settings.vue') },
      { path: 'logs', component: () => import('./views/Logs.vue') },
      { path: ':pathMatch(.*)*', redirect: '/overview' },
    ],
  },
]

const router = createRouter({ history: createWebHistory('/app/'), routes })

router.beforeEach(async (to) => {
  if (!session.ready) await restoreSession()
  if (!to.meta.public && !session.authenticated) {
    return { path: '/login', query: { next: to.fullPath } }
  }
  if (to.path === '/login' && session.authenticated) return '/overview'
})

export default router
