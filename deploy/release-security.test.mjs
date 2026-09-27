import assert from 'node:assert/strict';
import test from 'node:test';
import middleware from '../middleware.js';
import archive from '../api/archive.js';
import { handleCloud } from './cloud-api.mjs';

const password = 'fixture-private-password-long-enough';
const authorization = 'Basic ' + Buffer.from('reader:' + password).toString('base64');

async function environment(values, operation) {
  const previous = Object.fromEntries(Object.keys(values).map(key => [key, process.env[key]]));
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
  try { return await operation(); }
  finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

function response() {
  return { headers: {}, statusCode: 0, body: null,
    setHeader(name, value) { this.headers[name.toLowerCase()] = value; },
    end(body) { this.body = body == null ? '' : Buffer.from(body).toString('utf8'); } };
}

test('direct API requests cannot bypass browser auth with a forged rewrite header', async () => {
  await environment({ CORPUS_SITE_PASSWORD: password, CORPUS_SUPABASE_SECRET_KEY: undefined }, async () => {
    const out = response();
    await archive({ method: 'GET', url: '/api/archive', headers: { 'x-corpus-route': '/api/health' } }, out);
    assert.equal(out.statusCode, 401);
    assert.match(out.headers['www-authenticate'], /^Basic /);
    assert.equal(out.headers['cache-control'], 'private, no-store');
    assert.equal(out.body.includes(password), false);
  });
});

test('middleware protects static pages and replaces incoming route hints on API rewrites', async () => {
  await environment({ CORPUS_SITE_PASSWORD: password }, async () => {
    const denied = await middleware(new Request('https://site.invalid/index.html'));
    assert.equal(denied.status, 401);
    const routed = await middleware(new Request('https://site.invalid/api/counties?state=MT', {
      headers: { authorization, 'x-corpus-route': '/files/forged' }
    }));
    assert.equal(routed.headers.get('x-middleware-rewrite'), 'https://site.invalid/api/archive');
    assert.equal(routed.headers.get('x-middleware-request-x-corpus-route'), '/api/counties?state=MT');
  });
});

test('missing private-site configuration fails closed before a data connection', async () => {
  await environment({ CORPUS_SITE_PASSWORD: undefined, CORPUS_SUPABASE_SECRET_KEY: undefined }, async () => {
    const out = response();
    await archive({ method: 'GET', url: '/api/health', headers: {} }, out);
    assert.equal(out.statusCode, 503);
    assert.match(out.body, /CORPUS_SITE_PASSWORD/);
  });
});

test('API connection failures never echo credentials or internal exception details', async () => {
  await environment({ CORPUS_SITE_PASSWORD: password, CORPUS_SUPABASE_SECRET_KEY: 'synthetic-server-secret' }, async () => {
    const originalFetch = globalThis.fetch, originalError = console.error;
    globalThis.fetch = async () => { throw new Error('synthetic-server-secret plus private connection details'); };
    const messages = [];
    console.error = (...args) => messages.push(args.join(' '));
    try {
      const out = response();
      await archive({ method: 'GET', url: '/api/health', headers: { authorization } }, out);
      assert.equal(out.statusCode, 503);
      assert.equal(out.headers['cache-control'], 'private, no-store');
      assert.equal(out.body.includes('synthetic-server-secret'), false);
      assert.equal(messages.some(value => value.includes('synthetic-server-secret')), false);
    } finally { globalThis.fetch = originalFetch; console.error = originalError; }
  });
});

test('release health refuses incomplete counts and unpublished datasets', async () => {
  const request = new Request('https://site.invalid/api/health');
  const context = { context: async () => ({ id: 'reviewed-release', validated: true, datasets: ['one'] }),
    datasets: async () => [{ id: 'one', ready: true, expected_records: 2, imported_records: 1 }] };
  assert.equal((await handleCloud(request, context)).ready, false);
  context.datasets = async () => [{ id: 'one', ready: false, expected_records: 2, imported_records: 2 }];
  assert.equal((await handleCloud(request, context)).ready, false);
  context.datasets = async () => [{ id: 'one', ready: true, expected_records: 2, imported_records: 2 }];
  context.context = async () => ({ id: 'reviewed-release', validated: false, datasets: ['one'] });
  assert.equal((await handleCloud(request, context)).ready, false);
});
