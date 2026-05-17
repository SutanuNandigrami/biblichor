/** Thin fetch wrapper that always JSON-decodes. Throws ApiError on non-2xx. */
export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `HTTP ${status}`)
    this.status = status
    this.body = body
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const r = await fetch(path, { ...init, headers })
  const text = await r.text()
  let body: unknown = text
  try { body = text ? JSON.parse(text) : null } catch {}
  if (!r.ok) {
    const msg = (body && typeof body === 'object' && 'detail' in (body as any))
      ? String((body as any).detail)
      : `HTTP ${r.status}`
    throw new ApiError(r.status, body, msg)
  }
  return body as T
}
