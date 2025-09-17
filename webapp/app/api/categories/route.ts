import { NextResponse } from 'next/server'
import { backendBase, proxyHeaders, collectSetCookies } from '../_utils'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(req: Request) {
  const base = backendBase()
  const r = await fetch(`${base}/categories`, {
    headers: proxyHeaders(req),
    cache: 'no-store',
  })
  const text = await r.text()
  let res: NextResponse
  try {
    res = NextResponse.json(JSON.parse(text), { status: r.status })
  } catch {
    res = new NextResponse(text, { status: r.status })
  }
  for (const cookie of collectSetCookies(r)) {
    res.headers.append('set-cookie', cookie)
  }
  return res
}
