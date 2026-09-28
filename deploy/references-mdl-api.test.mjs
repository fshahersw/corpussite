import assert from 'node:assert/strict';
import test from 'node:test';
import { handleReferencesMdl } from './references-mdl-api.mjs';

const rows = [
  { id: '1', search: 'case alpha mdl 1', filters: { status: 'pending', court: ['nysd', 'NYS'.toLowerCase()], circuit: '2', litigation_type: 'products', judge_resolved: 'true', entity_id: ['e1'], cl_person_id: '70' },
    item: { id: 'mdl-1', mdl_number: 1, title: 'Alpha', actions_pending: 20, circuit: '2', litigation_type: 'Products', cl_court_id: 'nysd', judge_link_basis: 'native_id' } },
  { id: '2', search: 'case beta mdl 2', filters: { status: 'pending', court: ['flnd'], circuit: '11', litigation_type: 'products', judge_resolved: 'false', entity_id: [], cl_person_id: '' },
    item: { id: 'mdl-2', mdl_number: 2, title: 'Beta', actions_pending: 50, circuit: '11', litigation_type: 'Products', cl_court_id: 'flnd' } },
  { id: '3', search: 'case old mdl 3', filters: { status: 'terminated', court: ['nysd'], circuit: '2', litigation_type: 'other', judge_resolved: 'true', entity_id: ['e1'], cl_person_id: '70' },
    item: { id: 'mdl-3', mdl_number: 3, title: 'Old', actions_pending: null, circuit: '2', litigation_type: 'Other', cl_court_id: 'nysd' } }
];
function context(index = rows, listing = {}) {
  return { async dataset() { return { ready: true, metadata: { filter_index: index, listing: { available: true, as_of: '2026-09-01', ...listing }, registry_summary: { counts: { mdls_pending: 2 } } } }; },
    async query({ filters }) { return { items: [...index].reverse().filter(r => filters.id.includes(r.id)).map(r => ({ id: r.id })) }; },
    async detail(id) { return index.some(r => r.id === id) ? { id, provenance: { captured_at: 'saved date' } } : null; } };
}
test('MDL defaults pending, sorts actual report counts and clamps pagination', async () => {
  const value = await handleReferencesMdl('/api/mdls', { page: '99', limit: '1' }, context());
  assert.equal(value.total, 2); assert.equal(value.page, 2); assert.equal(value.results[0].mdl_number, 1);
  assert.deepEqual(value.facets.court, [['flnd', 1], ['nysd', 1]]);
});
test('MDL filters remain scoped to exact recorded geography and judge IDs', async () => {
  const filtered = await handleReferencesMdl('/api/mdls', { court: 'NYS', min_pending: '10', judge_resolved: 'yes' }, context());
  assert.equal(filtered.total, 1); assert.equal(filtered.results[0].mdl_number, 1);
  const judge = await handleReferencesMdl('/api/mdls/for-judge', { entity_id: 'e1' }, context());
  assert.equal(judge.total, 2); assert.equal(judge.basis, 'native_id');
  const person = await handleReferencesMdl('/api/mdls/for-person', { cl_person_id: '70' }, context());
  assert.equal(person.total, 2); assert.equal(person.basis, 'cl_assigned_to_id');
});
test('MDL detail, summary, missing IDs and unhandled paths preserve contracts', async () => {
  assert.equal((await handleReferencesMdl('/api/mdl', { number: '1' }, context())).id, '1');
  assert.equal((await handleReferencesMdl('/api/mdl', { number: 'no' }, context())).status, 404);
  assert.equal((await handleReferencesMdl('/api/mdls/summary', {}, context())).counts.mdls_pending, 2);
  assert.equal(await handleReferencesMdl('/api/unknown', {}, context()), null);
});
const mdlOverlays = {
  state_proceedings: 'state_proceedings', appearances: 'mdl_appearances',
  docket_documents: 'mdl_docket_documents', docket_activity: 'mdl_docket_activity',
  cases: 'mdl_case_inventory', counsel_directory: 'counsel_directory',
  verdict_reports: 'verdict_reports', expert_rulings: 'expert_rulings'
};
function mdlDetailContext(detail, published = []) {
  return { ...context(),
    async dataset(id) { return id === 'mdls' || published.includes(id) ? { ready: true } : { ready: false }; },
    async detail() { return detail; }
  };
}

test('MDL detail suppresses every held overlay and duplicated reader text while retaining registry context', async () => {
  const registry = { id: 'mdl:1', title: 'Published JPML matter', actions_pending: 20,
    court: { id: 'nysd' }, judge_links: [{ entity_id: 'e1' }],
    documents: [{ document_id: 'report-1' }], provenance: { publisher: 'JPML' },
    cl_links: { docket_id: 10 }, local_collections: [{ id: 'saved-mdl-1' }],
    reports: [{ document_id: 'report-1' }], summary: { mdl_number: 1 }, edges: [{ kind: 'reported_in' }] };
  const detail = { ...registry, ...Object.fromEntries(Object.keys(mdlOverlays).map(key => [key, { total: 1, marker: `held-${key}` }])),
    text: 'held-cases duplicate reader text', text_characters: 32, text_preview_characters: 32, text_truncated: true };
  const before = structuredClone(detail);
  const result = await handleReferencesMdl('/api/mdl', { number: '1' }, mdlDetailContext(detail));
  for (const key of Object.keys(mdlOverlays)) assert.equal(result[key], null, key);
  for (const [key, value] of Object.entries(registry)) assert.deepEqual(result[key], value, key);
  assert.equal(result.text, ''); assert.equal(result.text_characters, 0);
  assert.equal(result.text_preview_characters, 0); assert.equal(result.text_truncated, false);
  assert.doesNotMatch(JSON.stringify(result), /held-/);
  assert.deepEqual(detail, before, 'response filtering must not mutate the cached detail');
});

test('MDL overlays follow their owning dataset and fail closed for missing publication state', async () => {
  for (const [block, owner] of Object.entries(mdlOverlays)) {
    const detail = { id: 'mdl:1', [block]: { total: 1, marker: block } };
    const ready = await handleReferencesMdl('/api/mdl', { number: '1' }, mdlDetailContext(detail, [owner]));
    assert.deepEqual(ready[block], detail[block], block);
    const ctx = mdlDetailContext(detail);
    ctx.dataset = async id => id === 'mdls' || id === 'mdl_counsel' ? { ready: true } : null;
    const missing = await handleReferencesMdl('/api/mdl', { number: '1' }, ctx);
    assert.equal(missing[block], null, block);
  }
});

test('MDL detail keeps published overlays beside held ones and preserves fully published responses', async () => {
  const detail = { id: 'mdl:1', state_proceedings: { total: 1 }, cases: { total: 2 },
    counsel_directory: { total_firms: 3 }, expert_rulings: null, text: '', text_characters: 0 };
  const mixed = await handleReferencesMdl('/api/mdl', { number: '1' }, mdlDetailContext(detail, ['state_proceedings', 'counsel_directory']));
  assert.deepEqual(mixed.state_proceedings, detail.state_proceedings);
  assert.deepEqual(mixed.counsel_directory, detail.counsel_directory);
  assert.equal(mixed.cases, null); assert.equal(mixed.expert_rulings, null);
  const published = await handleReferencesMdl('/api/mdl', { number: '1' }, mdlDetailContext(detail, Object.values(mdlOverlays)));
  assert.deepEqual(published, detail);
});

test('source taxonomy combines exact tags and all search terms, keeping registry order', async () => {
  const sourceRows = [
    { id: 'b', search: 'civil court form', registry_order: 2, filters: { category: 'court_forms', jurisdiction: 'ca', has: ['saved'], api_bulk: '0' } },
    { id: 'a', search: 'civil court form', registry_order: 1, filters: { category: 'court_forms', jurisdiction: 'ca', has: ['saved'], api_bulk: '0' } },
    { id: 'c', search: 'criminal court rule', registry_order: 3, filters: { category: 'court_rules', jurisdiction: 'ca', has: ['links_only'], api_bulk: '0' } }
  ];
  const ctx = context(sourceRows, { facets: { jurisdictions: [{ value: 'ca', label: 'California' }] } });
  const result = await handleReferencesMdl('/api/sources', { jurisdiction: 'California', has: 'saved', q: 'civil form', order: 'registry' }, ctx);
  assert.equal(result.total, 2); assert.deepEqual(result.items.map(r => r.id), ['a', 'b']);
  const empty = await handleReferencesMdl('/api/sources', { category: 'uncategorized' }, ctx);
  assert.equal(empty.total, 0);
});
