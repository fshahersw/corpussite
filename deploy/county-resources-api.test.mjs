import test from 'node:test';
import assert from 'node:assert/strict';
import { handleCountyResources } from './county-resources-api.mjs';
const index = [
  { id: 'a', state: 'CA', county_geoids: ['06059'], title: 'Fee waiver', county: 'Orange County', resource_type: 'court_form', availability: 'saved' },
  { id: 'b', state: 'CA', county_geoids: ['06059'], title: 'Local directory', county: 'Orange County', resource_type: 'source_directory', availability: 'linked' },
  { id: 'c', state: 'MT', county_geoids: ['30001'], title: 'Rules', county: 'Beaverhead', resource_type: 'local_rule', availability: 'saved' }
];
const context = {
  async dataset() { return { ready: true, metadata: { filter_index: index, listing: { facets: {
    resource_types: ['court_form','source_directory','local_rule'].map(value => ({ value, count: 99 })),
    availability: ['saved','linked'].map(value => ({ value, count: 99 })) } } } }; },
  async query({ filters }) { return { items: index.filter(row => filters.__ids.includes(row.id)) }; }
};
test('county default hides directories and scopes facets by state/county', async () => {
  const result = await handleCountyResources('/api/county-litigation', { state: 'ca', geoid: '06059' }, context);
  assert.equal(result.total, 1); assert.equal(result.limit, 24); assert.equal(result.items[0].id, 'a');
  assert.deepEqual(result.facets.resource_types.map(row => [row.value,row.count]), [['court_form',1],['source_directory',1]]);
});
test('county category alias and venue substring use only saved index fields', async () => {
  const result = await handleCountyResources('/api/county-litigation', { category: 'source_directory', q: 'orange' }, context);
  assert.equal(result.total, 1); assert.equal(result.items[0].id, 'b');
  assert.equal((await handleCountyResources('/api/county-litigation', { q: 'unrecorded' }, context)).total, 0);
});
