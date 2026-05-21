// webapp/src/composables/useTint.ts
//
// Phase 6t.2: writes the --tint-h CSS variable on <html> so all
// oklch-keyed tokens respond. Persists across reloads in localStorage.

const STORAGE_KEY = "biblichor.tint-h"

export function applyTint(hue: number): void {
  const safe = Math.max(0, Math.min(360, Math.round(hue)))
  document.documentElement.style.setProperty("--tint-h", String(safe))
  try {
    localStorage.setItem(STORAGE_KEY, String(safe))
  } catch {
    /* localStorage unavailable (private mode); silently no-op */
  }
}

export function readSavedTint(defaultHue = 265): number {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === null) return defaultHue
    const n = Number(v)
    return Number.isFinite(n) ? n : defaultHue
  } catch {
    return defaultHue
  }
}

export function useTint() {
  return { applyTint, readSavedTint }
}
