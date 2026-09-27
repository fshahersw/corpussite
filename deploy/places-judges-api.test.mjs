import assert from 'node:assert/strict';
import test from 'node:test';
import { handlePlacesJudges, judgeSearch, personSearch } from './places-judges-api.mjs';

function context(dataset, listing, rows, extra = {}) {
  const calls = [];
  return { calls,
    async dataset(id) { assert.equal(id, dataset); return { ready: true, metadata: { listing, filter_index: rows, ...extra } }; },
    async query(args) {
      calls.push(args);
      const selected = rows.filter(row => Object.entries(args.filters).every(([key, value]) => {
        const source = Array.isArray(row.filters[key]) ? row.filters[key] : [row.filters[key]];
        return (Array.isArray(value) ? value : [value]).some(item => source.includes(item));
      }));
      return { items: selected.slice(args.offset, args.offset + args.limit).map(row => ({ id: row.id })), total: selected.length, limit: args.limit, offset: args.offset };
    },
    async detail(id, datasets) { calls.push({ id, datasets }); return rows.some(row => row.id === id) ? { id, structured: { available: true } } : null; }
  };
}

const judges = [
  { id: 'j1', search: 'alice smith montana', aliases: [], filters: { id: 'j1', state: ['Montana'], court: ['A Court'], system: ['state'], has: ['details', 'photo'], status: ['active'], role: ['judge'], president: [] } },
  { id: 'j2', search: 'mary casey rodgers florida', aliases: ['M. Casey Rodgers'], filters: { id: 'j2', state: ['Florida'], court: ['B Court'], system: ['federal'], has: ['details', 'analysis', 'reports'], status: ['senior'], role: ['judge'], president: ['George W. Bush'] } },
  { id: 'j3', search: 'bob smith montana', aliases: [], filters: { id: 'j3', state: ['Montana'], court: ['A Court'], system: ['state'], has: [], status: [], role: [], president: [] } }
];
const listing = { states: ['Florida', 'Montana'], systems: ['federal', 'state'], courts: [{ value: 'A Court' }, { value: 'B Court' }], structured_facets: { role: [['judge', 2]] } };

test('judge native photo membership, case-insensitive state, and facets are preserved', async () => {
  const ctx = context('judges', listing, judges);
  const result = await handlePlacesJudges('/api/judges', { state: 'montana', has: 'photo' }, ctx);
  assert.equal(result.total, 1);
  assert.equal(result.items[0].id, 'j1');
  assert.deepEqual(result.courts, [{ value: 'A Court', label: 'A Court', count: 1 }]);
  assert.equal(ctx.calls[0].sort, 'rank');
});

test('judge substring/printed alias search uses exact saved membership instead of FTS', async () => {
  const ctx = context('judges', listing, judges);
  const result = await handlePlacesJudges('/api/judges', { q: 'M. Casey', sort: 'name' }, ctx);
  assert.equal(result.total, 1);
  assert.equal(result.items[0].id, 'j2');
  assert.equal(ctx.calls[0].q, '');
  assert.equal(ctx.calls[0].sort, 'title');
  assert.equal(judgeSearch(judges[0], 'ali smi'), true);
});

test('judge details resolve only recorded native-id aliases', async () => {
  const ctx = context('judges', listing, judges, { id_aliases: { 'entity:one': 'j1' } });
  assert.equal((await handlePlacesJudges('/api/judge', { id: 'entity:one' }, ctx)).id, 'j1');
  assert.equal((await handlePlacesJudges('/api/judge', { id: 'Alice Smith' }, ctx)).status, 404);
});

test('county name/FIPS search and source availability use exact inventory memberships', async () => {
  const rows = [
    { id: '06059', name: 'Orange County', filters: { id: '06059', state: 'CA', availability: ['local_resources', 'saved_information'] } },
    { id: '12095', name: 'Orange County', filters: { id: '12095', state: 'FL', availability: ['no_local_resources'] } }
  ];
  const ctx = context('counties', { states: ['CA', 'FL'], availabilities: [{ value: 'local_resources' }, { value: 'no_local_resources' }] }, rows);
  const result = await handlePlacesJudges('/api/counties', { q: '06059' }, ctx);
  assert.equal(result.total, 1);
  assert.equal(result.items[0].id, '06059');
  assert.equal(result.availabilities[0].count, 1);
  assert.equal(result.availabilities[1].count, 0);
});

test('people aliases are opt-in, court filtering is native, and name search matches token prefixes', async () => {
  const rows = [
    { id: '1', search: 'Mary Johnson mary-johnson', filters: { id: '1', court: ['ca9'], is_alias: '0' } },
    { id: '2', search: 'M. Johnson', filters: { id: '2', court: ['ca9'], is_alias: '1' } }
  ];
  const ctx = context('people', { ready: true, courts: [{ id: 'ca9', name: 'Ninth Circuit' }] }, rows);
  assert.equal((await handlePlacesJudges('/api/people', { court: 'ca9' }, ctx)).total, 1);
  assert.equal((await handlePlacesJudges('/api/people', { court: 'ca9', include_aliases: '1' }, ctx)).total, 2);
  assert.equal((await handlePlacesJudges('/api/people', { q: 'mar joh' }, ctx)).total, 1);
  assert.equal(personSearch(rows[0], 'son'), false);
});

test('unpublished directory and invalid people IDs fail closed', async () => {
  const ctx = { dataset: async () => ({ ready: false }) };
  assert.equal((await handlePlacesJudges('/api/judges', {}, ctx)).status, 503);
  const people = context('people', {}, []);
  assert.equal((await handlePlacesJudges('/api/person', { id: '../private' }, people)).status, 404);
  assert.equal(await handlePlacesJudges('/api/other', {}, {}), null);
});
