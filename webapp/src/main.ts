import { createApp } from "vue"
import { createPinia } from "pinia"
import { createRouter, createWebHistory } from "vue-router"

import App from "./App.vue"
import "./styles/app.css"
import { applyTint, readSavedTint } from "@/composables/useTint"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/",         redirect: "/queue" },
    { path: "/queue",    name: "queue",    component: () => import("./pages/QueuePage.vue") },
    { path: "/book/:id", name: "book",     component: () => import("./pages/BookPage.vue") },
    { path: "/sources",  name: "sources",  component: () => import("./pages/SourcesPage.vue") },
    { path: "/scrapers", name: "scrapers", component: () => import("./pages/ScrapersPage.vue") },
    { path: "/mirrors",  name: "mirrors",  component: () => import("./pages/MirrorsPage.vue") },
    { path: "/scoring",  name: "scoring",  component: () => import("./pages/ScoringPage.vue") },
    { path: "/schedule", name: "schedule", component: () => import("./pages/SchedulePage.vue") },
    { path: "/settings", name: "settings", component: () => import("./pages/SettingsPage.vue") },
    { path: "/logs",     name: "logs",     component: () => import("./pages/LogsPage.vue") },
    { path: "/setup",    name: "setup",    component: () => import("./pages/SetupPage.vue") },
    { path: "/lib",      redirect: "/library" },
    { path: "/library",  name: "library",  component: () => import("./pages/LibraryPage.vue") },
  ],
})

// Phase 6t.2: apply saved tint hue before mounting so the first paint
// is already in the user's chosen accent.
applyTint(readSavedTint())

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount("#app")
