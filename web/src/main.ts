import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

import MonitorPage from './pages/MonitorPage.vue'
import ConfigPage from './pages/ConfigPage.vue'
import DataPage from './pages/DataPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/monitor' },
    { path: '/monitor', component: MonitorPage },
    { path: '/config', component: ConfigPage },
    { path: '/database', component: DataPage },
  ]
})

createApp(App).use(router).mount('#app')
