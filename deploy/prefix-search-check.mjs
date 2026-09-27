/** Generates read-only PostgreSQL regression checks from the exact migration expression. */
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

export const migrationURL=new URL('../supabase/migrations/20260927211217_corpus_prefix_lexeme_parity.sql',import.meta.url);
export const cases=[
  ['decimal citation','314.80','21 CFR 314.80',true],
  ['wrong decimal citation','314.81','21 CFR 314.80',false],
  ['full CFR citation','21 CFR 314.80','21 CFR 314.80',true],
  ['dotted USC abbreviation','42 U.S.C. 1983','42 U.S.C. § 1983',true],
  ['hyphen prefix on spaced words','drug-induced inj','drug induced injury',true],
  ['hyphen prefix on hyphenated words','drug-induced inj','drug-induced injury',true],
  ['publisher section range','112.48-112.49','112.48-112.49',true],
  ['ordinary prefix','aspir','aspirin',true],
  ['operators cannot turn AND into OR','court | judge','court only',false],
  ['literal operator punctuation','court & judge','court judge',true],
  ['hyphenated number','COVID-19','COVID-19',true],
  ['slash token','foo/bar','foo/bar',true],
  ['empty query tokens','!!!','anything',false],
  ['apostrophe prefix',"o'br","O'Brien",true],
  ['file citation','c:\\files\\law.pdf','c:\\files\\law.pdf',true],
  ['unicode prefix','naïve','naïveté',true],
  ['syntax injection is inert',"' & ! | :*",'irrelevant',false],
  ['native sixteen-token cap','one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen missing','one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen',true],
];

const quote=value=>`'${String(value).replaceAll("'","''")}'`;
export function verificationSQL() {
  const migration=readFileSync(migrationURL,'utf8');
  const block=migration.match(/-- prefix-query:start\s*([\s\S]+?)\s*-- prefix-query:end/)?.[1];
  if(!block)throw new Error('Migration prefix-query markers missing');
  const expression=block.replace(/\binto terms\b/,'AS parsed_query').replace(/\bp_q\b/g,'sample.query').replace(/;\s*$/,'');
  const rows=cases.map(([name,q,doc,expected])=>`(${quote(name)},${quote(q)},${quote(doc)},${expected})`).join(',\n');
  return `WITH samples(name,query,document,expected) AS (VALUES\n${rows}\n)\nSELECT name, query, expected,\n  to_tsvector('simple',sample.document) @@ parsed.parsed_query AS actual,\n  (to_tsvector('simple',sample.document) @@ parsed.parsed_query) = expected AS passed,\n  parsed.parsed_query::text AS parsed_query\nFROM samples sample CROSS JOIN LATERAL (\n${expression}\n) parsed;\n`;
}

if(process.argv[1] && fileURLToPath(import.meta.url)===process.argv[1])process.stdout.write(verificationSQL());
