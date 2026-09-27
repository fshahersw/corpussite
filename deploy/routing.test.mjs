import test from 'node:test';
import assert from 'node:assert/strict';
import { checkAccess, backendRequest, isArchivePath } from './routing.mjs';

const env = { CORPUS_BACKEND_ORIGIN: 'https://archive.example', CORPUS_BACKEND_TOKEN: 'test-token-'.repeat(4), CORPUS_SITE_PASSWORD: 'test-password-long-enough' };
test('API, document and portrait paths route to the archive', () => {
  for (const p of ['/api/counties', '/files/123', '/judge-images/id', '/supplement-files/docs/id']) assert.equal(isArchivePath(p), true);
  assert.equal(isArchivePath('/app.js'), false);
});
test('rewrite preserves query and sends only the configured server token', () => {
  const req = new Request('https://site.example/api/counties?state=Montana&q=a%26b', { headers: { authorization: 'private-browser-value', cookie: 'session=secret', 'x-corpus-token': 'forged', range: 'bytes=0-99' } });
  const out = backendRequest(req, env);
  assert.equal(out.url.href, 'https://archive.example/api/counties?state=Montana&q=a%26b');
  assert.equal(out.headers.get('x-corpus-token'), env.CORPUS_BACKEND_TOKEN);
  assert.equal(out.headers.get('range'), 'bytes=0-99');
  assert.equal(out.headers.has('cookie'), false);
  assert.equal(out.headers.has('authorization'), false);
});
test('missing or unsafe origins fail closed without reflecting secrets', async () => {
  for (const origin of [undefined, 'http://archive.example', 'https://secret@archive.example', 'https://archive.example/path']) {
    const response = backendRequest(new Request('https://site.example/api/summary'), { ...env, CORPUS_BACKEND_ORIGIN: origin });
    assert.equal(response.status, 503);
    assert.equal((await response.text()).includes(env.CORPUS_BACKEND_TOKEN), false);
  }
});
test('hosted archive is private and requires configured browser authentication', async () => {
  assert.equal((await checkAccess(new Request('https://site.example/'), {})).status, 503);
  assert.equal((await checkAccess(new Request('https://site.example/'), env)).status, 401);
  const req = new Request('https://site.example/', { headers: { authorization: `Basic ${btoa(`reader:${env.CORPUS_SITE_PASSWORD}`)}` } });
  assert.equal(await checkAccess(req, env), null);
});
test('browser passwords use UTF-8 consistently', async () => {
  const local = { ...env, CORPUS_SITE_PASSWORD: 'private-long-pass-\u2603\u4f60' };
  const encoded = Buffer.from(`reader:${local.CORPUS_SITE_PASSWORD}`, 'utf8').toString('base64');
  const req = new Request('https://site.example/', { headers: { authorization: `Basic ${encoded}` } });
  assert.equal(await checkAccess(req, local), null);
});
