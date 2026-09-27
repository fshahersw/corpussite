import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { handlePlacesJudges } from './places-judges-api.mjs';
import { handleReferencesMdl } from './references-mdl-api.mjs';
const folder = path.resolve('_transfer_scratch/supabase_export');
const cache = new Map();
function load(dataset) {
  if (!cache.has(dataset)) cache.set(dataset, {
    metadata: JSON.parse(fs.readFileSync(path.join(folder, dataset + '.dataset.json'))),
    rows: fs.readFileSync(path.join(folder, dataset + '.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line))
  });
  return cache.get(dataset);
}
const context = {
  async dataset(id) { return { ready: true, metadata: load(id).metadata }; },
  async detail(id, datasets) { return load(datasets[0]).rows.find(row => row.id === id)?.detail ?? null; },
  async query({ datasets, filters, limit, offset, sort }) {
    let rows = load(datasets[0]).rows.filter(row => Object.entries(filters).every(([key, wanted]) => {
      const have = Array.isArray(row.filters[key]) ? row.filters[key] : [row.filters[key]];
      return (Array.isArray(wanted) ? wanted : [wanted]).some(value => have.includes(value));
    }));
    rows.sort((a, b) => sort === 'title' ? a.title.toLowerCase().localeCompare(b.title.toLowerCase()) : a.filters.__rank - b.filters.__rank);
    return { total: rows.length, items: rows.slice(offset, offset + limit).map(row => row.item), limit, offset };
  }
};
const cases = JSON.parse(fs.readFileSync(path.join(folder, 'native-parity-cases.json')));
let passed = 0;
for (const sample of cases) {
  const value = await handlePlacesJudges(sample.path, sample.params, context) ?? await handleReferencesMdl(sample.path, sample.params, context);
  const actual = { total: value.total, ids: (value.items ?? value.results ?? []).map(row => String(row.id || row.geoid || row.mdl_number)) };
  for (const key of ['courts', 'availabilities', 'facets']) if (key in sample.expected) actual[key] = value[key];
  assert.deepEqual(actual, sample.expected, sample.path + ' ' + JSON.stringify(sample.params));
  passed++;
}
const result = { status: 'passed', cases: passed, verified_at: new Date().toISOString() };
fs.writeFileSync(path.resolve('reports/release_county_data_20260927/native_parity.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
