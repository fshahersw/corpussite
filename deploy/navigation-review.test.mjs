import assert from 'node:assert/strict';
import test from 'node:test';
import { handleNavigation } from './navigation-api.mjs';
import { handleCloud } from './cloud-api.mjs';

test('recognized unpublished navigation contexts return 503 instead of falling through as unknown routes', async () => {
  const routes = [
    ['/api/coverage/matrix', 'coverage:matrix'],
    ['/api/coverage/venues', 'coverage:venues'],
    ['/api/trellis-coverage/summary', 'trellis:summary'],
    ['/api/trellis-coverage/progress', 'trellis:progress'],
    ['/api/resources/circuits', 'doj:circuits'],
    ['/api/collections', 'collections'],
    ['/api/supplements', 'supplements']
  ];
  for (const [path, key] of routes) {
    const values = { 'doj:states': { items: [] } };
    const ctx = { async context(name) { return values[name] ?? null; } };
    const request = new Request('https://archive.example' + path);
    const result = await handleCloud(request, ctx);
    assert.equal(result.status, 503, path);
    const body = await result.json();
    assert.equal(body.code, 'publication_pending', path);
    assert.equal(body.available, false, path);
    assert.match(body.error, /not been published yet/, path);
    const published = { available: true, source_as_of: '2026-09-01', items: [{ id: key }] };
    values[key] = published;
    assert.equal(await handleCloud(request, ctx), published, 'Published payload must remain unchanged: ' + path);
  }
});

test('unknown coverage routes still return 404', async () => {
  const ctx = { async context() { return null; } };
  const result = await handleCloud(new Request('https://archive.example/api/coverage/not-a-route'), ctx);
  assert.equal(result.status, 404);
  assert.equal((await result.json()).error, 'Unknown archive route');
});

test('collection defaults to the native 20-record page size', async () => {
  const ctx = { async context() { return { card: { id: 'library' }, rows: Array.from({ length: 40 }, (_, i) => ({ id: String(i) })) }; } };
  const result = await handleNavigation('/api/collection', { id: 'library' }, ctx);
  assert.equal(result.page_size, 20); assert.equal(result.items.length, 20);
});
test('unpublished DOJ context does not claim a ready empty directory', async () => {
  const ctx = { async context() { return null; } };
  const result = await handleNavigation('/api/resources/state', { state: 'CA' }, ctx);
  assert.equal(result.ready, false);
});
test('DOJ directory membership filters accept case-insensitive native yes/no', async () => {
  const rows = [
    { record_id: 'one', usps: 'CA', page_kind: 'state_resource_page', position: 1, directory_ref_ids: ['published'], not_in_source_directory: false, section_h2: 'Courts', section_h3: '', section_path: 'Courts' },
    { record_id: 'two', usps: 'CA', page_kind: 'state_resource_page', position: 2, directory_ref_ids: [], not_in_source_directory: true, section_h2: 'Courts', section_h3: '', section_path: 'Courts' }
  ];
  const values = { 'doj:resources': rows, 'doj:aliases': { ca: 'CA' }, 'doj:states': { items: [{ usps: 'CA' }] } };
  const ctx = { async context(key) { return values[key] ?? null; } };
  const result = await handleNavigation('/api/resources/state', { state: 'CA', in_directory: 'YES' }, ctx);
  assert.equal(result.total, 1); assert.equal(result.items[0].record_id, 'one');
});
test('coverage invalid state has no aggregate counts from other jurisdictions', async () => {
  const values = { 'state:aliases': { ca: 'CA' }, 'coverage:topics': { available: true, total: 2 }, 'coverage:topic_tiers': { '|sol': { publisher: 99 } } };
  const ctx = { async context(key) { return values[key] ?? null; }, async dataset() { return { ready: true }; }, async query() { return { total: 0, items: [] }; } };
  const result = await handleNavigation('/api/coverage/topics', { state: 'invalid', topic: 'sol' }, ctx);
  assert.equal(result.total, 0); assert.deepEqual(result.by_source_tier, {});
});
