import assert from 'node:assert/strict';
import test from 'node:test';
import { handlePlacesJudges, judgeSearch, personSearch, validatedPortraitIds } from './places-judges-api.mjs';
import portraitMembership from './judge-portrait-membership.json' with { type: 'json' };

function context(dataset, listing, rows, extra = {}) {
  const calls = [];
  return { calls,
    async dataset(id) { assert.equal(id, dataset); return { ready: true, metadata: { listing, filter_index: rows, ...extra } }; },
    async query(args) {
      calls.push(args);
      const selected = rows.filter(row => Object.entries(args.filters).every(([key, value]) => {
        const source = key === '__ids' ? [row.id] : Array.isArray(row.filters[key]) ? row.filters[key] : [row.filters[key]];
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
test('derived portrait membership is pinned and validates exact allowed route and profile id pairs', () => {
  assert.match(portraitMembership.source_export_sha256, /^[a-f0-9]{64}$/);
  assert.match(portraitMembership.source_descriptor_sha256, /^[a-f0-9]{64}$/);
  assert.equal(portraitMembership.count, portraitMembership.entries.length);
  assert.equal(new Set(portraitMembership.entries.map(([id]) => id)).size, portraitMembership.count);
  assert.ok(portraitMembership.entries.every(([id, route]) => /^[a-f0-9]{32}$/.test(id) && /^\/(judge-images|supplement-files\/judge_portraits)\//.test(route)));
  const [[first, route], [other]] = portraitMembership.entries;
  const meta = { export_jsonl_sha256: portraitMembership.source_export_sha256, validated_portrait_routes: [route], filter_index: [{ id: first }, { id: other }] };
  assert.deepEqual([...validatedPortraitIds(meta)], [first]);
  assert.equal(validatedPortraitIds({ ...meta, export_jsonl_sha256: 'different' }), null);
  assert.equal(validatedPortraitIds({ ...meta, validated_portrait_routes: null }), null);
  assert.deepEqual([...validatedPortraitIds({ ...meta, filter_index: [{ id: other }] })], []);
});
test('verified portrait overlay replaces stale has-photo flags and intersects other filters and name search', async () => {
  const [a, b, c] = portraitMembership.entries.slice(0, 3);
  const rows = [
    { ...judges[0], id: a[0], filters: { ...judges[0].filters, id: a[0], has: ['details'] } },
    { ...judges[1], id: b[0], filters: { ...judges[1].filters, id: b[0], has: ['details'] } },
    { ...judges[2], id: c[0], filters: { ...judges[2].filters, id: c[0], has: ['photo'] } }
  ];
  const meta = { export_jsonl_sha256: portraitMembership.source_export_sha256, validated_portrait_routes: [a[1], b[1]] };
  const ctx = context('judges', listing, rows, meta);
  const all = await handlePlacesJudges('/api/judges', { has: 'photo' }, ctx);
  assert.equal(all.total, 2);assert.deepEqual(all.items.map(row => row.id), [a[0], b[0]]);
  assert.deepEqual(all.courts.map(row => [row.value, row.count]), [['A Court', 1], ['B Court', 1]]);
  assert.equal(ctx.calls[0].filters.has, undefined);assert.deepEqual(ctx.calls[0].filters.__ids, [a[0], b[0]]);
  const filtered = await handlePlacesJudges('/api/judges', { has: 'photo', state: 'montana', q: 'alice', status: 'active' }, ctx);
  assert.equal(filtered.total, 1);assert.equal(filtered.items[0].id, a[0]);
  assert.deepEqual(filtered.courts, [{ value: 'A Court', label: 'A Court', count: 1 }]);
  assert.equal((await handlePlacesJudges('/api/judges', { has: 'photo', q: 'unrecorded' }, ctx)).total, 0);
  assert.equal((await handlePlacesJudges('/api/judges', { has: 'reports' }, ctx)).total, 0, 'Other feature flags must retain their saved native membership');
});
