import assert from 'node:assert/strict';
import test from 'node:test';
import { handleNavigation } from './navigation-api.mjs';

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
