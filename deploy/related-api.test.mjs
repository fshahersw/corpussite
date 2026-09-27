import assert from 'node:assert/strict';
import test from 'node:test';
import { findCourtIds, handleRelated, resolveCourt } from './related-api.mjs';
const courts = {
  district: { id: 'district', name: 'United States District Court', cited_as: 'D. Test' },
  bankruptcy: { id: 'bankruptcy', name: 'Bankruptcy Court', cited_as: '' },
  parent: { id: 'parent', name: 'Appeals Court', cited_as: '' },
  child: { id: 'child', name: 'First Appeals Court', cited_as: '' }
};
const data = { available: true, courts,
  patterns: [
    { pattern: 'Test Court', id: 'district', type: 'trial' }, { pattern: '(Bankruptcy )?Test Court', id: 'bankruptcy', type: 'bankruptcy' },
    { pattern: '(First )?Appeals Court', id: 'parent', type: 'appellate' }, { pattern: 'First Appeals Court', id: 'child', type: 'appellate', parent: 'parent' }
  ], native_courts: Object.values(courts).map(row => ({ ...row, type: row.id === 'bankruptcy' ? 'bankruptcy' : 'trial', parent: row.id === 'child' ? 'parent' : null })) };
test('court resolver preserves exact citations and bankruptcy distinction', () => {
  assert.deepEqual(resolveCourt(data, 'D. Test').results.map(r => r.id), ['district']);
  assert.deepEqual(resolveCourt(data, 'Test Court').results.map(r => r.id), ['district']);
  assert.deepEqual(resolveCourt(data, 'Bankruptcy Test Court').results.map(r => r.id), ['bankruptcy']);
  assert.deepEqual(findCourtIds(data, 'First Appeals Court', false), ['child']);
  assert.equal(resolveCourt(data, 'unknown').results.length, 0);
});
test('related handlers never invent relationships for absent IDs', async () => {
  const ctx = { async context(key) { return key === 'related:citations:status' ? { available: true } : null; } };
  assert.deepEqual(await handleRelated('/api/citations/record', { id: 'missing' }, ctx), { available: true, total: 0, results: [] });
  assert.deepEqual(await handleRelated('/api/blocks', { court: 'unknown' }, ctx), { court_documents: null, urls: null });
  assert.deepEqual(await handleRelated('/api/urls/block', { agency: 'unknown' }, ctx), { block: null });
  assert.equal(await handleRelated('/api/unhandled', {}, ctx), null);
});
