import { next, rewrite } from '@vercel/functions';
import { isArchivePath } from './deploy/routing.mjs';

export const config = { matcher: '/:path*' };

export default async function middleware(request) {
  const incoming = new URL(request.url);
  if (!isArchivePath(incoming.pathname) || incoming.pathname==='/api/archive') return next();
  const headers = new Headers(request.headers);
  headers.set('x-corpus-route',incoming.pathname+incoming.search);
  return rewrite(new URL('/api/archive',request.url), { request: { headers } });
}
