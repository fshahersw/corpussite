import fs from 'node:fs';
import assert from 'node:assert/strict';
import { resolveCourt } from './related-api.mjs';
const directory = '_transfer_scratch/supabase_export/related/';
const contexts = fs.readFileSync(directory + 'contexts.jsonl', 'utf8').trim().split('\n').map(line => JSON.parse(line));
const data = contexts.find(row => row.key === 'related:court-matcher').data;
const cases = JSON.parse(fs.readFileSync(directory + 'matcher-parity.json'));
// Compile every saved pattern, not merely the patterns reached by the samples.
for (const row of data.patterns) new RegExp(row.pattern, 'i');
for (const sample of cases) assert.deepEqual(resolveCourt(data, sample.q), sample.expected, sample.q);
const result = { status: 'passed', patterns: data.patterns.length, native_parity_cases: cases.length, checked_at: new Date().toISOString() };
fs.writeFileSync('reports/release_county_data_20260927/related_matcher_parity.json', JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
