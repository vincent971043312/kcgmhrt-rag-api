import { NextResponse } from 'next/server'
import { backendBase, proxyHeaders, collectSetCookies } from '../../_utils'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(req: Request, { params }: { params: { filename: string } }) {
  const base = backendBase()
  const filename = params.filename
  const target = `${base}/doc/${encodeURIComponent(filename)}`

  const r = await fetch(target, {
    headers: proxyHeaders(req),
    cache: 'no-store',
  })

  const headers = new Headers()
  const type = r.headers.get('content-type')
  if (type) headers.set('content-type', type)
  const disposition = r.headers.get('content-disposition')
  if (disposition) headers.set('content-disposition', disposition)

  for (const cookie of collectSetCookies(r)) {
    headers.append('set-cookie', cookie)
  }

  if (!headers.has('content-type')) {
    headers.set('content-type', 'application/octet-stream')
  }

  return new NextResponse(r.body, {
    status: r.status,
    headers,
  })
}
