const archivePrefixes = ['/api/', '/files/', '/bulk-files/', '/bulk-metadata/', '/recovery-metadata/',
  '/agency-files/', '/mdl-files/', '/supplement-files/', '/source-assets/', '/library-assets/', '/judge-images/'];

export function isArchivePath(path) {
  return archivePrefixes.some(prefix => path.startsWith(prefix));
}

function unavailable(message) {
  return Response.json({ error: message }, { status: 503, headers: { 'cache-control': 'no-store' } });
}

export function backendRequest(request, env) {
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response('Read-only archive', { status: 405 });
  let origin;
  try {
    origin = new URL(env.CORPUS_BACKEND_ORIGIN);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) throw Error();
  } catch { return unavailable('Configure CORPUS_BACKEND_ORIGIN as the HTTPS origin of the persistent archive server.'); }
  const token = env.CORPUS_BACKEND_TOKEN;
  if (!token || token.length < 32) return unavailable('The archive backend connection is not configured.');
  const incoming = new URL(request.url);
  const target = new URL(origin);
  target.pathname = incoming.pathname;
  target.search = incoming.search;
  // Forward only representation/range headers. Browser credentials and caller-supplied tokens never pass through.
  const headers = new Headers();
  for (const name of ['accept', 'accept-encoding', 'range', 'if-range', 'if-none-match', 'if-modified-since']) {
    if (request.headers.has(name)) headers.set(name, request.headers.get(name));
  }
  headers.set('x-corpus-token', token);
  return { url: target, headers };
}
