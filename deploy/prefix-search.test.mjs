import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {migrationURL,verificationSQL,cases} from './prefix-search-check.mjs';

const migrations=new URL('../supabase/migrations/',import.meta.url);
const currentName=migrationURL.pathname.split('/').at(-1);
const previousName=readdirSync(migrations).filter(n=>n.endsWith('.sql')&&n<currentName).sort().reverse().find(n=>/create or replace function public\.corpus_query\(/.test(readFileSync(new URL(n,migrations),'utf8')));
const previous=readFileSync(new URL(previousName,migrations),'utf8').replaceAll('\r\n','\n');
const current=readFileSync(migrationURL,'utf8').replaceAll('\r\n','\n');

test('query-only patch preserves the latest preceding filters, ordering and execution grants',()=>{
  assert.equal(previousName,'20260927205809_corpus_exact_id_filters.sql');
  assert.equal(current.split('  with matched as',2)[1],previous.split('  with matched as',2)[1]);
  for(const key of ['__ids','__contains','__county','__date_any','__date_type','__dfrom','__dto'])assert.ok(current.includes(`'${key}'`),`${key} must survive function replacement`);
  assert.match(current,/security invoker set search_path = ''/);
  assert.doesNotMatch(current,/security definer/i);
});
test('regression SELECT is generated from the exact migrated expression without mutating remote data',()=>{
  const query=verificationSQL();assert.equal(cases.length,18);
  assert.match(query,/ts_debug\('simple'::regconfig, left\(sample.query,300\)\)/);
  assert.match(query,/CROSS JOIN LATERAL/);assert.match(query,/AS parsed_query/);
  assert.doesNotMatch(query,/\b(?:CREATE|ALTER|INSERT|DELETE|UPDATE|DROP)\s/i);
  assert.doesNotMatch(query,/\binto terms\b/);
});
