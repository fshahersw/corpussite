"""Publish a county-local supplement only at a locked collection checkpoint.

The coordinated rebuild holds both collector locks before calling this script.
County entry/profile evidence remains a separate component from local resources.
"""
from pathlib import Path
from contextlib import ExitStack
import sys
import hashlib,importlib.util,json,shutil,sqlite3
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'delivery/focused_legal_corpus/county_local_resources'
REPORT=ROOT/'reports/counties/local_documents_20260914'
COLLECTIONS=('corpus/county_local_documents_20260914','corpus/county_local_rules_washington_20260914')
def capture_record(r):
    """Keep discovery hints, reviewed content and geographic evidence distinct."""
    raw=r['raw_evidence'];meta=r['metadata_evidence'];text=r['text_evidence']
    if not r['public_page_or_document_captured'] or not raw['exists'] or not raw['hash_matches'] or not meta['resource_binding_valid']:
        raise RuntimeError('Invalid downloaded capture: '+r['url'])
    if r['extraction_status'] in ('extracted','text_truncated') and not (text.get('expected_sha256_valid') and text['hash_matches']):
        raise RuntimeError('Unverified extracted text digest: '+r['url'])
    contexts=r['contexts'];review=r['semantic_review']
    return {'collection':r['collection'],'resource_key':r['resource_key'],'source_url':r['url'],
            'raw_sha256':raw['sha256'],'raw_path':raw['path'],
            'text_sha256':text['sha256'],'text_path':text['path'],
            'metadata_sha256':meta['sha256'],'metadata_path':meta['path'],
            'geoid_associations':sorted({x['jurisdiction']['geoid'] for x in contexts if x['jurisdiction'].get('geoid')}),
            'geoid_associations_status':'Source associations only; consult per-context geography and court-jurisdiction verification flags.',
            'contexts':contexts,'title':r['title'],'extraction_status':r['extraction_status'],
            'court_labels':sorted({x['court_label'] for x in contexts if isinstance(x.get('court_label'),str) and x['court_label'].strip()}),
            'detected_type':r['detected_type'],
            'discovery_categories':sorted({x['category'] for x in contexts if x.get('category')}),
            'reviewed_resource_kind':review['actual_resource_kind'] if review['reviewed'] else None,
            'semantic_review':review,'source_legal_currency_verified':False}
def build():
    for collection in COLLECTIONS:
        c=sqlite3.connect((ROOT/collection/'corpus.sqlite3').as_uri()+'?mode=ro',uri=True)
        try:last=c.execute('SELECT ended_at FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()
        finally:c.close()
        if last and last[0] is None:raise RuntimeError('Collector must finish before publishing: '+collection)
    src=ROOT/'scripts/update_county_local_documents_progress_20260914.py'
    spec=importlib.util.spec_from_file_location('county_local_progress',src);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.main()
    progress=json.loads((REPORT/'progress.json').read_text(encoding='utf-8'))
    if not progress['validation']['valid']:raise RuntimeError('County-local artifact validation failed')
    rows=[json.loads(x) for x in (REPORT/'resources.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    captures=[]
    for r in rows:
        if r['status']!='downloaded':continue
        captures.append(capture_record(r))
    OUT.mkdir(parents=True,exist_ok=True)
    for name in ['resources.jsonl','county_ledger.jsonl']:shutil.copyfile(REPORT/name,OUT/name)
    (OUT/'summary.json').write_text(json.dumps(progress,indent=2)+'\n',encoding='utf-8')
    (OUT/'captures.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in captures),encoding='utf-8')
    (OUT/'README.md').write_text('''# County local resources

This supplement stores local-resource captures separately from county entry pages. `county_ledger.jsonl` preserves all 3,144 county-equivalent GEOIDs as strings. `resources.jsonl` has every request outcome and verified original/text/metadata references. `captures.jsonl` contains downloaded records with their geographic association evidence; `summary.json` records the snapshot time and exact counts.

Filter by county GEOID, state, source category, court label where observed, download status and extraction status. Unassigned municipal/multi-county court documents remain unassigned. Candidate county website associations are labeled; a name match does not establish court jurisdiction. A court information page is not automatically a filing or rule document. Publisher versions and dates require content review before treating text as current law. Full county content is incomplete.

`discovery_categories` preserves selection hints. `reviewed_resource_kind` is populated only by the separately hash-bound `semantic_review`; unreviewed kinds remain null. `detected_type` describes file format, not legal content. Raw, extracted-text and metadata SHA-256 fields preserve each artifact identity. GEOID arrays are source associations with verification flags retained in `contexts`, not verified court territorial assignments.

The focused search includes these collections after the coordinated index rebuild. Original files remain in the workspace at each capture's recorded path. This supplement is not a standalone copy of those bytes.
''',encoding='utf-8')
    (OUT/'provenance.json').write_text(json.dumps({'reporter':src.relative_to(ROOT).as_posix(),'reporter_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'source_collections':COLLECTIONS,'captures':len(captures),'network_requests':0},indent=2)+'\n',encoding='utf-8')
def main():
    sys.path.insert(0,str(ROOT/'pipeline'))
    from corpus_crawler import run_lock
    with ExitStack() as locks:
        for collection in COLLECTIONS:locks.enter_context(run_lock(ROOT/collection))
        build()
if __name__=='__main__':main()
