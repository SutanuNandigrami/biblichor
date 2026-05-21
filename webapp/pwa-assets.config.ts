// webapp/pwa-assets.config.ts
// Phase 6t.3: regenerate icon set with `npx pwa-assets-generator`.
import { defineConfig, minimal2023Preset } from "@vite-pwa/assets-generator/config"

export default defineConfig({
  headLinkOptions: { preset: "2023" },
  preset: minimal2023Preset,
  images: ["public/pwa-icon-source.svg"],
})
