export function backendBase(): string {
  const base = process.env.RAG_API_BASE
  if (!base) {
    throw new Error('Missing RAG_API_BASE env var')
  }
  return base
}

export function proxyHeaders(req: Request): Record<string, string> {
  const headers: Record<string, string> = {}
  const token = process.env.RAG_AUTH_TOKEN
  if (token) headers['Authorization'] = `Bearer ${token}`
  const cookie = req.headers.get('cookie')
  if (cookie) headers['cookie'] = cookie
  return headers
}

export function collectSetCookies(res: Response): string[] {
  const anyHeaders = res.headers as unknown as { raw?: () => Record<string, string[]> }
  const raw = anyHeaders.raw?.()
  if (raw && raw['set-cookie']) return raw['set-cookie']
  const single = res.headers.get('set-cookie')
  return single ? [single] : []
}
