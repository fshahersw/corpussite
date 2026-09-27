import fs from 'node:fs';
import assert from 'node:assert/strict';
import { handleNavigation } from './navigation-api.mjs';
const root = '_transfer_scratch/supabase_export/';
const contexts = Object.fromEntries(fs.readFileSync(root + 'navigation/contexts.jsonl', 'utf8').trim().split('\n').map(line => {
  const row = JSON.parse(line); return [row.key, row.data];
}));
const patch = root + 'contexts.coverage_order.jsonl';
if (fs.existsSync(patch)) for (const line of fs.readFileSync(patch, 'utf8').trim().split('\n')) {
  const row = JSON.parse(line); contexts[row.key] = row.data;
}
const datasets = new Map();
function dataset(id) {
  if (!datasets.has(id)) datasets.set(id, {
    metadata: JSON.parse(fs.readFileSync(root + 'navigation/' + id + '.dataset.json')),
    rows: fs.readFileSync(root + 'navigation/' + id + '.jsonl', 'utf8').trim().split('\n').map(line => JSON.parse(line))
  });
  return datasets.get(id);
}
const context = {
  async context(key) { return contexts[key] == null ? null : structuredClone(contexts[key]); },
  async dataset(id) { return { ready: true, metadata: dataset(id).metadata }; },
  async query({ datasets: ids, filters = {}, limit, offset, sort = 'ordinal' }) {
    const rows = dataset(ids[0]).rows.filter(row => Object.entries(filters).every(([key, wanted]) => {
      const have = key === '__ids' ? [row.id] : Array.isArray(row.filters[key]) ? row.filters[key] : [row.filters[key]];
      return (Array.isArray(wanted) ? wanted : [wanted]).some(value => have.includes(value));
    }));
    rows.sort((a,b) => sort === 'rank' ? a.filters.__rank-b.filters.__rank : sort === 'title' ? a.title.localeCompare(b.title) : a.ordinal-b.ordinal);
    return { total: rows.length, items: rows.slice(offset,offset+limit).map(row => structuredClone(row.item)), limit, offset };
  }
};
const fixtures = JSON.parse(fs.readFileSync(root + 'navigation-review-fixtures.json'));
const results = [];
for (const fixture of fixtures) {
  try {
    const value = await handleNavigation(fixture.path, fixture.params, context);
    const actual = { total: value.total, ids: (value.items ?? []).map(row => String(row.provision_id || row.record_id || row.id)) };
    for (const key of ['page','page_size','limit','by_source_tier']) if (key in fixture.expected) actual[key] = value[key];
    assert.deepEqual(actual, fixture.expected);
    results.push({ path: fixture.path, params: fixture.params, passed: true });
  } catch (error) {
    results.push({ path: fixture.path, params: fixture.params, passed: false, message: error.message.slice(0,2500) });
  }
}
const report = { status: results.every(row => row.passed) ? 'passed' : 'failed', checked_at: new Date().toISOString(), results };
fs.writeFileSync('reports/release_county_data_20260927/navigation_review_parity.json', JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
if (report.status !== 'passed') process.exitCode = 1;
