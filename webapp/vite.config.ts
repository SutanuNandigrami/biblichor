import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"
import tailwindcss from "@tailwindcss/vite"
import { VitePWA } from "vite-plugin-pwa"
import path from "node:path"

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: [
        "favicon.ico",
        "favicon.svg",
        "apple-touch-icon-180x180.png",
        "pwa-icon-source.svg",
      ],
      workbox: {
        cleanupOutdatedCaches: true,
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/ws/, /^\/healthz/],
      },
      manifest: {
        name: "biblichor",
        short_name: "biblichor",
        description:
          "Self-hosted Goodreads -> Anna -> Kindle automation + BookOrbit library",
        theme_color: "#0a0a0a",
        background_color: "#fafafa",
        display: "standalone",
        orientation: "any",
        start_url: "/queue",
        scope: "/",
        icons: [
          { src: "pwa-64x64.png", sizes: "64x64", type: "image/png" },
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          {
            src: "pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8090",
      "/ws": { target: "ws://localhost:8090", ws: true },
      "/healthz": "http://localhost:8090",
    },
  },
})
