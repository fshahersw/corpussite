"""Offline, additive coverage crosswalk. Does not copy originals or rewrite the archive DB."""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, json, re, shutil, sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
QUALIFICATION = ('Historical court-library snapshot, not current or complete court coverage. '
                 'Document links reuse existing originals by exact SHA-256; roster counts are source-reported. '
                 'Statewide and shared-site entries are not individual county coverage. No blanket reuse license is asserted.')

def digest(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def rows(p):
    return [json.loads(x) for x in p.read_text('utf-8-sig').splitlines() if x.strip()]

def urls(text):
    from urllib.parse import urlsplit
    values = text if isinstance(text, list) else str(text or '').split(' | ')
    return [u for u in values if isinstance(u, str) and urlsplit(u).scheme in ('http', 'https')
            and urlsplit(u).netloc and not urlsplit(u).username and not urlsplit(u).password]

def write(name, value):
    p = OUT / name
    content = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in value) if name.endswith('.jsonl') else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    p.write_text(content, encoding='utf-8')
    return {'path': name, 'sha256': digest(p), 'rows': len(value) if isinstance(value, list) else None}

def build(source):
    source = source.resolve()
    manifest = json.loads((source / 'PUBLICATION-MANIFEST.json').read_text('utf-8-sig'))
    advertised = {r['path']: r for r in manifest['files']}
    evidence = []
    for rel in ['00-Catalog/COURTS.json', '00-Catalog/DOCUMENTS.csv', '00-Catalog/COVERAGE-SUMMARY.json', 'README.md', 'THIRD-PARTY-NOTICES.md']:
        p = source / rel
        h = digest(p)
        if rel not in advertised or h != advertised[rel]['sha256'] or p.stat().st_size != advertised[rel]['bytes']:
            raise ValueError('Source publication hash mismatch: ' + rel)
        target = OUT / 'evidence' / Path(rel).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        evidence.append({'path': target.relative_to(OUT).as_posix(), 'sha256': h, 'bytes': target.stat().st_size})
    summary = json.loads((source / '00-Catalog/COVERAGE-SUMMARY.json').read_text('utf-8-sig'))
    captured = summary['generated_at']
    courts = json.loads((source / '00-Catalog/COURTS.json').read_text('utf-8-sig'))
    with (source / '00-Catalog/DOCUMENTS.csv').open(encoding='utf-8-sig', newline='') as f: docs = list(csv.DictReader(f))
    prior = {r.get('sha256'): r for r in rows(ROOT / 'sources/seeger_import_20260918/resources.jsonl') if r.get('metadata', {}).get('record_type') == 'original_document'}
    spine = {e['key']: r['id'] for r in rows(ROOT / 'sources/court_spine_20260919/courts.jsonl') for e in r.get('registry', [])}
    with sqlite3.connect((ROOT / 'delivery/archive-directory/directory.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        index = {}
        for rid, payload, original_id, text_id in db.execute("SELECT id,payload,original_id,text_id FROM records WHERE dataset='seeger'"):
            p = json.loads(payload)
            if p.get('sha256') in prior:
                index[p['sha256']] = {'record_id': rid, 'original_id': original_id, 'text_id': text_id, 'saved_at': p.get('captured_at')}
    court_keys = {c['collection_id'] for c in courts}
    documents, held = [], []
    for d in docs:
        sha = d['sha256']; existing = prior.get(sha); mapped = index.get(sha)
        if not existing or not mapped:
            held.append({'sha256': sha, 'title': d['title'], 'reason': 'No exact existing original and live directory match'})
            continue
        original = ROOT / existing['raw_path']
        if not original.is_file() or original.stat().st_size != int(d['bytes']):
            held.append({'sha256': sha, 'title': d['title'], 'reason': 'Existing original missing or size changed'})
            continue
        collection_ids = d['source_collection_ids'].split(' | ')
        documents.append({'id': 'document_' + sha, 'sha256': sha, 'title': d['title'], 'format': d['format'],
            'bytes': int(d['bytes']), 'collection_ids': collection_ids, 'known_collection_ids': [x for x in collection_ids if x in court_keys],
            'source_courts': d['source_courts'].split(' | '), 'jurisdictions': d['source_jurisdictions'].split(' | '),
            'source_urls': urls(d['source_urls']), 'source_date': d['source_date'] or None, 'source_date_kind': d['source_date_kind'] or None,
            'source_as_of': captured, 'review_status': d['review_status'], 'technical_flags': d['technical_flags'],
            'language': d['language'], 'language_note': d['language_note'], 'title_evidence': d['title_evidence'],
            'scope_note': d['publisher_scope_note'], 'association_basis': 'Exact source_collection_ids from saved document catalog; not territorial inference',
            'source_row_sha256': hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest(), **mapped})
    counts = Counter(k for d in documents for k in d['known_collection_ids'])
    court_rows = []
    for c in courts:
        key = c['collection_id']
        court_rows.append({'id': 'coverage_' + hashlib.sha256(key.encode()).hexdigest()[:20], 'collection_id': key,
            'title': c['name'], 'level': c['level'], 'jurisdiction': c['jurisdiction'], 'homepage': (urls([c.get('homepage')]) or [None])[0],
            'forms_pages': urls(c.get('forms_pages', [])), 'directory_source': (urls([c.get('directory_source')]) or [None])[0],
            'verification': c.get('verification'), 'notes': c.get('notes'), 'scope_note': c.get('coverage_limit'),
            'source_as_of': captured, 'source_reported_documents': c.get('local_documents', 0), 'linked_documents': counts[key],
            'observed_urls': c.get('discovered_document_urls', 0), 'source_reported_acquired_urls': c.get('acquired_discovered_urls', 0),
            'source_reported_unacquired_urls': c.get('not_yet_acquired_urls', 0), 'existing_court_id': spine.get(key),
            'source_row_sha256': hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()})
    files = [write('courts.jsonl', court_rows), write('documents.jsonl', documents), write('held.jsonl', held)] + evidence
    stats = {'court_entries': len(court_rows), 'matched_existing_court_entries': sum(bool(c['existing_court_id']) for c in court_rows),
        'new_roster_entries': sum(not c['existing_court_id'] for c in court_rows), 'existing_originals_reused': len(documents),
        'new_original_downloads': 0, 'new_original_copies': 0, 'held_documents': len(held), 'exact_court_document_associations': sum(counts.values()),
        'courts_with_exact_document_links': sum(bool(counts[c['collection_id']]) for c in courts),
        'court_count_discrepancies': sum(c['source_reported_documents'] != c['linked_documents'] for c in court_rows)}
    write('summary.json', stats)
    gate = {'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_as_of': captured, 'source_publication_prepared_at': manifest.get('prepared_at'), 'source_publication_manifest_sha256': digest(source / 'PUBLICATION-MANIFEST.json'),
        'qualification': QUALIFICATION, 'license_ref': 'evidence/THIRD-PARTY-NOTICES.md; artifact-specific rights retained; no blanket license',
        'data_files': files, 'counts': stats, 'validation_scope': 'Catalog publication hashes; exact SHA-256 identity reuse; live record IDs; existing original size/existence. Original binary hashes are retained, not recomputed by this crosswalk.'}
    write('validation.json', gate)
    return stats

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--source', type=Path, default=Path('C:/Users/firas/Downloads/docsupload'))
    print(json.dumps(build(p.parse_args().source), indent=2))
