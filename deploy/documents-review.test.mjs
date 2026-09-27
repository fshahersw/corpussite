import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { documentFilters, handleDocuments } from './documents-api.mjs';

const config = { facets: { file_type: { pdf: 'PDF' }, review: { reviewed: 'Reviewed' } }, labels: { rules: 'Rules' },
  date_types: { saved: ['saved_lo', 'saved_hi', 'saved_at'], captured: ['saved_lo', 'saved_hi', 'captured_at'],
    effective: ['effective_lo', 'effective_hi', 'effective_from'] } };
test('document date filters reject impossible calendar dates like the native sidecar', () => {
  assert.equal(documentFilters({ date_type: 'effective', dfrom: '2026-02-30' }, config).valid, false);
  assert.equal(documentFilters({ date_type: 'effective', dto: '2026-13-01' }, config).valid, false);
  assert.equal(documentFilters({ date_type: 'effective', dfrom: '2024-02-29' }, config).valid, true);
});
test('date bounds retain imprecise-date intervals and hide no dates unless explicitly requested', () => {
  const value = documentFilters({ date_type: 'effective', dfrom: '2020-07-01', dto: '2021-03-01', undated: '0' }, config);
  assert.equal(value.params.date_lo, 'effective_lo'); assert.equal(value.params.date_hi, 'effective_hi');
  assert.equal(value.params.date_type, 'effective_from'); assert.equal(value.params.undated, '0');
  assert.deepEqual(value.filters.validity, ['ok', 'no_capture']);
});
test('local bare-ID display groups retain the full source list', async () => {
  const id = 'd7a78d17f5c4dd95b0e2f81b72c3a5e9';
  const group = { id, dataset: 'judge_entities', source_count: 1, source_records: [{ id: 'source-observation' }] };
  const context = {
    async rpc(name, params) { assert.equal(name, 'corpus_group_detail'); assert.equal(params.p_id, id); return group; },
    async detail() { return { id, dataset: 'judge_entities', source_records: [] }; }
  };
  const value = await handleDocuments('/api/record', { id }, context);
  assert.deepEqual(value.source_records, group.source_records);
});
test('full text requests return the untruncated body and preserve dedicated dataset scope', async () => {
  const text = 'Saved legal text.\n'.repeat(5000);
  const context = { async asset() { return null; }, async detail(id, datasets, options) {
    assert.equal(id, 'oul:known'); assert.deepEqual(datasets, ['open_us_law']); assert.equal(options.full, true);
    return { text, text_truncated: false };
  } };
  const value = await handleDocuments('/api/text', { id: 'oul:known' }, context);
  assert.equal(await value.text(), text);
});
test('document pagination moves from local grouped records into publisher ordinal order', async () => {
  const calls = [];
  const context = { async datasets() { return [{ id: 'open_us_law', ready: true }]; }, async context() { return config; },
    async rpc() { return { total: 2, source_total: 3, items: [] }; },
    async query(args) { calls.push(args); return { total: 9, items: [{ id: 'bulk-third' }] }; } };
  const result = await handleDocuments('/api/documents', { page: '3', limit: '2' }, context);
  assert.equal(calls[0].offset, 2); assert.equal(result.total, 11); assert.equal(result.source_total, 12);
});

function activeFunction(name) {
  let found;
  for (const file of fs.readdirSync('supabase/migrations').filter(file => file.endsWith('.sql')).sort()) {
    const text = fs.readFileSync('supabase/migrations/' + file, 'utf8');
    const regex = new RegExp('create(?:\\s+or\\s+replace)?\\s+function\\s+public\\.' + name + '\\s*\\([\\s\\S]*?\\$\\$([\\s\\S]*?)\\$\\$', 'gi');
    for (const match of text.matchAll(regex)) found = match[1];
  }
  assert.ok(found, name + ' migration exists');
  return found;
}
test('active grouped SQL scopes colliding preferred IDs and sources to main datasets', () => {
  const listing = activeFunction('corpus_documents_local');
  const groups = listing.match(/groups\s+as(?:\s+not\s+materialized)?\s*\(([\s\S]*?)\),\s*candidates/i)?.[1];
  assert.ok(groups && /r\.dataset\s*=\s*any/i.test(groups), 'preferred-record join must exclude new judges/people/other datasets');
  const detail = activeFunction('corpus_group_detail');
  const sources = detail.slice(detail.toLowerCase().indexOf('select jsonb_agg'));
  assert.match(sources, /r\.dataset\s*=\s*any/i, 'source_records must exclude colliding IDs outside the main catalog');
});
test('active grouped SQL uses source observations, not identity/member totals', () => {
  const listing = activeFunction('corpus_documents_local');
  assert.doesNotMatch(listing, /['"]source_count['"]\s*,\s*\([^)]*retained_members/i);
  assert.match(listing, /metadata\s*->>\s*'source_count'/i);
  const detail = activeFunction('corpus_group_detail');
  assert.doesNotMatch(detail, /['"]source_count['"]\s*,\s*g\.metadata\s*->\s*'retained_members'/i);
});
