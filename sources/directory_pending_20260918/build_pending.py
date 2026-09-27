"""Read-only scoped collection directory delta; never publishes or mutates collectors."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote, urlsplit

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
COLLECTIONS = (
    'corpus/official_law_resume_pass3_20260913',
    'corpus/official_law_state_rules_followup_20260914',
    'corpus/county_local_documents_20260914',
    'corpus/county_local_rules_washington_20260914',
    'corpus/county_local_backfill_20260918',
    'corpus/county_local_backfill_20260918T2213',
)
PUBLISHED = ROOT / 'delivery/focused_legal_corpus/focused.sqlite3'
TITLE_CLEANUP_COLLECTIONS = {
    'corpus/county_local_backfill_20260918',
    'corpus/county_local_backfill_20260918T2213',
}


def stamp(): return datetime.now(timezone.utc).isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
def rel(path): return path.resolve().relative_to(ROOT).as_posix()
def write_json(path, value): path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def connect(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def artifact(collection, value, expected=None):
    if not value:
        return None
    path = (ROOT / collection / value).resolve()
    path.relative_to((ROOT / collection).resolve())
    assert path.is_file(), str(path)
    data = path.read_bytes()
    digest = sha(data)
    if expected is not None:
        assert re.fullmatch('[0-9a-f]{64}', expected) and digest == expected, str(path)
    return {'path': rel(path), 'sha256': digest, 'bytes': len(data)}


def unique_context_value(contexts, field):
    values = sorted({c.get('jurisdiction', {}).get(field) for c in contexts if c.get('jurisdiction', {}).get(field)})
    return values[0] if len(values) == 1 else None


def source_link_display_title(title, url, contexts):
    """Replace technical PDF titles only with one exact, evidenced publisher label."""
    original = title or ''
    if re.match(r'^Microsoft (?:Word|Excel|PowerPoint)\s*[-–]\s*\S', original, re.I):
        return re.sub(r'^Microsoft (?:Word|Excel|PowerPoint)\s*[-–]\s*', '', original, flags=re.I), 'producer_prefix_removed'
    technical = not original or original == url or bool(re.match(r'^(?:https?://|[a-z]:[\\/]|\\\\)', original, re.I))
    if not technical:
        return original, 'capture_metadata'
    labels = set()
    generic_labels = {'pdf','download','view','view/download','click here','here','document','doc',
        'jan','feb','mar','apr','may','jun','jul','aug','sep','sept','oct','nov','dec',
        'january','february','march','april','june','july','august','september','october','november','december'}
    for context in contexts:
        for proof in context.get('seed', {}).get('provenance', []):
            if proof.get('target_url') != url or proof.get('link_reproduced_from_raw_html') is not True:
                continue
            label = ' '.join((proof.get('anchor_text') or '').split())
            if (label and len(label) <= 500 and not re.match(r'^(?:https?://|[a-z]:[\\/])', label, re.I)
                    and label.casefold() not in generic_labels):
                labels.add(label)
    filename = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
    if len(labels) == 1:
        label = labels.pop()
        revision = re.search(r'Revised[-_ ]+(\d{4}[-_]\d{2}[-_]\d{2})', filename, re.I)
        if revision and revision.group(1).replace('_','-') not in label:
            return label+' [filename: Revised '+revision.group(1).replace('_','-')+']', 'source_link_label_with_filename_revision'
        return label, 'exact_observed_source_link_label'
    stem = re.sub(r'\.pdf$', '', filename, flags=re.I)
    if filename.lower().endswith('.pdf') and stem.casefold() not in generic_labels and len(stem) > 5:
        # Only spacing changes; retain numeric identifiers and date separators.
        return re.sub(r'(?<!\d)-|-(?!\d)|_+', ' ', stem), 'normalized_source_filename'
    return original or url, 'source_title_fallback'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    started = stamp()
    published_sha = sha(PUBLISHED.read_bytes())
    with closing(connect(PUBLISHED)) as db:
        all_published = [tuple(row) for row in db.execute('SELECT collection,source_url,raw_sha256 FROM documents')]
    published = set(all_published)
    frozen_keys = sorted(key for key in published if key[0] in COLLECTIONS)
    write_json(OUT / 'published_keys.snapshot.json', {
        'captured_at': started, 'published_database_path': rel(PUBLISHED),
        'published_database_sha256': published_sha, 'all_published_record_count': len(all_published),
        'key_fields': ['collection', 'source_url', 'raw_sha256'], 'keys': frozen_keys})
    resources, collection_snapshots = [], []
    for collection in COLLECTIONS:
        dbpath = ROOT / collection / 'corpus.sqlite3'
        with closing(connect(dbpath)) as db:
            snapshot_at = stamp()
            downloaded = [dict(r) for r in db.execute("SELECT * FROM resources WHERE status='downloaded' ORDER BY id")]
            rows = [r for r in downloaded if (collection, r['url'], r['sha256']) not in published]
            statuses = dict(db.execute('SELECT status,count(*) FROM resources GROUP BY status'))
            contexts = {r['id']: dict(r) for r in db.execute('SELECT * FROM contexts')}
            for source in rows:
                fetch = dict(db.execute('SELECT * FROM fetches WHERE id=?', (source['last_fetch_id'],)).fetchone())
                links = [dict(r) for r in db.execute('SELECT * FROM resource_contexts WHERE resource_id=? ORDER BY context_id', (source['id'],))]
                seed_evidence = []
                for link in links:
                    item = dict(contexts[link['context_id']])
                    for field in ('jurisdiction_json', 'scope_json', 'seed_json'):
                        item[field.removesuffix('_json')] = json.loads(item.pop(field))
                    item['association'] = link
                    seed_evidence.append(item)
                raw = artifact(collection, source['raw_path'], source['sha256'])
                meta_ref = artifact(collection, source['metadata_path'])
                meta = json.loads((ROOT / meta_ref['path']).read_bytes())
                assert source['last_http_status'] == meta['http_status'] == fetch['http_status'] == 200
                assert source['raw_complete'] == meta['raw_complete'] == fetch['raw_complete'] == 1
                assert source['status'] == meta['status'] == fetch['status'] == 'downloaded'
                assert meta['requested_url'] == source['url']
                assert meta['fetch_id'] == source['last_fetch_id'] == fetch['id']
                for field in ('sha256', 'raw_path', 'text_path', 'extraction_status', 'byte_count'):
                    assert source[field] == meta[field] == fetch[field], (collection, source['id'], field)
                assert raw['bytes'] == source['byte_count']
                assert source['metadata_path'] == fetch['metadata_path']
                text = artifact(collection, source['text_path'], meta.get('text_sha256'))
                if text:
                    assert re.fullmatch('[0-9a-f]{64}', meta.get('text_sha256', ''))
                    content = (ROOT / text['path']).read_bytes().decode('utf-8')
                    text['characters'] = len(content)
                    text['nonempty'] = bool(content.strip())
                    text['strict_utf8_valid'] = True
                elif source['extraction_status'] in ('extracted', 'text_truncated'):
                    raise AssertionError('Expected text missing: ' + source['url'])
                metadata_contexts = meta.get('contexts', [])
                # Metadata is the capture-time geographic authority for this supplement.
                for context in metadata_contexts:
                    matches = [c for c in seed_evidence if c['id'] == context['context_id']]
                    assert len(matches) == 1 and matches[0]['jurisdiction'] == context['jurisdiction']
                categories = sorted({c['category'] for c in metadata_contexts if c.get('category')})
                key = (collection, source['url'], source['sha256'])
                title = source.get('title') or meta.get('title')
                title_basis = 'capture_metadata'
                captured_title = title
                if collection in TITLE_CLEANUP_COLLECTIONS:
                    title, title_basis = source_link_display_title(title, source['url'], seed_evidence)
                if not title:
                    labels = sorted({c['seed'].get('title') or c['seed'].get('court_label') for c in seed_evidence if c['seed'].get('title') or c['seed'].get('court_label')})
                    title = labels[0] if len(labels) == 1 else source['url']
                    title_basis = 'observed_seed_label' if len(labels) == 1 else 'source_url_fallback'
                evidence = {'collection': collection, 'resource_id': source['id'], 'fetch_id': source['last_fetch_id'],
                    'source_database': rel(dbpath), 'source_database_snapshot_at': snapshot_at,
                    'resource_row': source, 'resource_row_sha256': sha(canonical(source)),
                    'fetch_row': fetch, 'source_context_records': seed_evidence,
                    'source_metadata': meta, 'metadata_path': meta_ref['path'], 'metadata_sha256': meta_ref['sha256'],
                    'raw_evidence': raw, 'text_evidence': text, 'metadata_evidence': meta_ref,
                    'discovery_categories': categories, 'resource_kind_basis': 'capture-time discovery category; semantic content unreviewed',
                    'title_basis': title_basis, 'captured_title': captured_title,
                    'jurisdiction_basis': 'explicit capture-time metadata contexts',
                    'county_associations_are_source_contexts_not_verified_court_territory': True,
                    'publication_status': 'absent_from_frozen_published_exact_triple',
                    'source_legal_currency_verified': False, 'downloaded_status_verified': True,
                    'raw_and_metadata_binding_verified': True, 'file_hashes_verified': True}
                resources.append({'id': 'pending_capture_' + sha(canonical(key))[:28],
                    'title': title, 'source_url': source['url'], 'state': unique_context_value(metadata_contexts, 'state'),
                    'county': unique_context_value(metadata_contexts, 'county'),
                    'county_geoids': sorted({c['jurisdiction']['geoid'] for c in metadata_contexts if c.get('jurisdiction', {}).get('geoid')}),
                    'resource_kind': categories[0] if len(categories) == 1 else 'unreviewed_resource',
                    'group': 'counties' if 'county_local_' in collection else 'laws',
                    'raw_path': raw['path'], 'text_path': text['path'] if text else None, 'sha256': raw['sha256'],
                    'captured_at': meta['fetched_at'], 'quality': 'Awaiting publication validation', 'metadata': evidence})
            collection_snapshots.append({'collection': collection, 'snapshot_at': snapshot_at, 'status_counts': statuses,
                'downloaded': len(downloaded), 'absent_published_downloaded': len(rows),
                'downloaded_snapshot_rows_sha256': sha(canonical(downloaded))})
    assert sha(PUBLISHED.read_bytes()) == published_sha, 'Published package changed during snapshot; rerun supplement'
    resources.sort(key=lambda r: (r['metadata']['collection'], r['source_url'], r['sha256']))
    data = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in resources).encode('utf-8')
    (OUT / 'resources.jsonl').write_bytes(data)
    summary = {'prepared_at': stamp(), 'snapshot_started_at': started, 'resources': len(resources),
        'by_collection': dict(Counter(r['metadata']['collection'] for r in resources)),
        'by_group': dict(Counter(r['group'] for r in resources)),
        'by_state': dict(Counter(r['state'] for r in resources)),
        'with_nonempty_verified_text': sum(bool(r['metadata']['text_evidence'] and r['metadata']['text_evidence']['nonempty']) for r in resources),
        'raw_only_or_empty_text': sum(not r['metadata']['text_evidence'] or not r['metadata']['text_evidence']['nonempty'] for r in resources),
        'quality': 'Awaiting publication validation', 'published_counts_unchanged': True,
        'published_database_path': rel(PUBLISHED), 'published_database_sha256': published_sha,
        'published_record_count': len(all_published), 'collection_snapshots': collection_snapshots,
        'resources_sha256': sha(data), 'network_requests': 0, 'source_mutations': 0,
        'full_national_or_collection_content_complete': False}
    write_json(OUT / 'summary.json', summary)
    print(json.dumps({key: summary[key] for key in ('resources', 'by_collection', 'by_group', 'with_nonempty_verified_text', 'raw_only_or_empty_text', 'resources_sha256')}, indent=2))


if __name__ == '__main__': main()
