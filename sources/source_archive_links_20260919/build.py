"""Build validated links from the 9,348-reference source map to records already saved in this archive.

Reads only: the validated source catalog, the local directory database and the canonical
capture index. Writes links.jsonl, excluded.jsonl and validation.json in this folder.
No network access, no acquisition, no change to any primary corpus database.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
from evidence_dates import date_value, latest_saved  # noqa: E402

CATALOG = ROOT / 'sources/public_law_directory_20260919/catalog.json'
CATALOG_GATE = ROOT / 'sources/public_law_directory_20260919/validation.json'
DIRECTORY = ROOT / 'delivery/archive-directory/directory.sqlite3'
BUILD_RECEIPT = ROOT / 'delivery/archive-directory/build_receipt.json'
CANONICAL = ROOT / 'catalog/documents.sqlite3'
CANONICAL_GATE = ROOT / 'catalog/validation.json'
PILOT = ROOT / 'sources/public_law_acquisition_20260919/resources.jsonl'
# Only these capture roots may be served through the archive asset route.
ASSET_ROOTS = ('sources/official_courts', 'corpus/official_courts')
DOCUMENT_DATASETS = {'focused': 'published_focused_release', 'seeger': 'imported_collection',
                     'federal': 'federal_supplement', 'pending_publication': 'awaiting_publication'}
SUSPECT_TITLE = re.compile(r'(page not found|\bnot found\b|\b404\b|access denied|forbidden|just a moment|attention required|'
                           r'error page|an error occurred|server error|\blog ?in\b|\bsign ?in\b|service unavailable|temporarily unavailable)', re.I)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''): digest.update(chunk)
    return digest.hexdigest()


def now(): return datetime.now(timezone.utc).isoformat()


def publication_for(dataset): return DOCUMENT_DATASETS.get(dataset)


def review_flags(title, text_characters):
    flags = []
    if isinstance(title, str) and SUSPECT_TITLE.search(title): flags.append('suspect_title')
    if not text_characters: flags.append('no_usable_text')
    return flags


def captured_at(payload):
    payload = payload if isinstance(payload, dict) else {}
    value = date_value(payload.get('retrieved_at'))
    if value and len(value) >= 10: return value, 'retrieved_at'
    value = latest_saved(payload.get('captured_at'))
    if value: return value, 'captured_at'
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    value = latest_saved(metadata.get('captured_at'))
    if value: return value, 'metadata.captured_at'
    return None, None


def workspace_file(value):
    """Directory file paths are workspace-relative; anything else is not a saved artifact."""
    if not isinstance(value, str) or not value: return None
    candidate = Path(value)
    file = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    if not file.is_relative_to(ROOT) or not file.is_file(): return None
    return file


def ro(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def load_catalog():
    gate = json.loads(CATALOG_GATE.read_text(encoding='utf-8'))
    payload = CATALOG.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if gate.get('status') != 'passed' or gate.get('ready') is not True or digest != gate.get('catalog_sha256'):
        raise SystemExit('Source directory catalog gate failed; refusing to build links')
    entries = json.loads(payload)['entries']
    return digest, {e['url']: e for e in entries}


def text_characters(con, canonical, row):
    if row['text_id']:
        file_row = con.execute('SELECT path FROM files WHERE id=?', (row['text_id'],)).fetchone()
        file = workspace_file(file_row['path']) if file_row else None
        if file is None: return None, 'text_file_missing'
        return len(file.read_text(encoding='utf-8', errors='replace').strip()), None
    if row['content_id']:
        found = canonical.execute('SELECT length(trim(text)) FROM contents WHERE id=?', (row['content_id'],)).fetchone()
        return (found[0] or 0) if found else 0, None
    return len((row['inline_text'] or '').strip()), None


def directory_targets(catalog_urls, canonical, excluded):
    """Exact source-URL joins to saved directory records; judge observations roll up to consolidated entities."""
    targets, judge_observations, stats = defaultdict(list), defaultdict(list), Counter()
    with ro(DIRECTORY) as con:
        rows = con.execute("SELECT r.*, b.eligible FROM records r JOIN browse b ON b.id=r.id WHERE "
                           "(COALESCE(r.original_id,'')!='' OR COALESCE(r.text_id,'')!='' OR r.content_id IS NOT NULL OR COALESCE(r.inline_text,'')!='')").fetchall()
        for row in rows:
            url, basis = row['source_url'], 'exact_source_url'
            if url not in catalog_urls:
                # A reference may cite the address a saved capture was redirected to; that is
                # still an exact recorded URL of the record, labelled as a final-URL match.
                if not row['payload'] or '"final_url"' not in row['payload']: continue
                final = (json.loads(row['payload']) or {}).get('final_url')
                if not isinstance(final, str) or final == url or final not in catalog_urls: continue
                url, basis = final, 'exact_final_url'
            stats['directory_candidates'] += 1
            stats['directory_final_url_candidates'] += basis == 'exact_final_url'
            payload = json.loads(row['payload']) if row['payload'] else {}
            if row['dataset'] == 'judge_enrichment':
                judge_observations[url].append((row['id'], row['title'], payload))
                continue
            if row['dataset'] == 'judge_entities':
                judge_observations.setdefault(url, [])
                targets[url].append({'kind': 'judge_entity', 'record_id': row['id'], 'entity_id': payload.get('entity_id') or '',
                                     'name': row['title'] or payload.get('name') or '', 'observation_count': 0,
                                     'source_observation_ids': list(payload.get('source_observation_ids') or []),
                                     'current_service_verified': payload.get('current_service_verified') is True, 'captured_at': None})
                continue
            publication = publication_for(row['dataset'])
            if publication is None:
                excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'dataset_not_linkable', 'dataset': row['dataset']}); continue
            if str(row['eligible']) != '1':
                excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'ineligible_record'}); continue
            original = None; original_bytes = None; verified = None
            if row['original_id']:
                file_row = con.execute('SELECT path FROM files WHERE id=?', (row['original_id'],)).fetchone()
                original = workspace_file(file_row['path']) if file_row else None
                if original is None:
                    excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'original_missing'}); continue
                original_bytes = original.stat().st_size
                expected = payload.get('raw_sha256') or payload.get('sha256')
                if isinstance(expected, str) and re.fullmatch(r'[a-f0-9]{64}', expected):
                    verified = sha256(original) == expected
                    if not verified:
                        excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'original_hash_mismatch'}); continue
                if not original_bytes:
                    excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'original_empty'}); continue
            characters, problem = text_characters(con, canonical, row)
            if problem:
                excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': problem}); continue
            if original is None and not characters:
                excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'no_saved_artifact'}); continue
            flags = review_flags(row['title'], characters)
            if 'suspect_title' in flags:
                excluded.append({'source_url': url, 'kind': 'directory_record', 'record_id': row['id'], 'reason': 'suspect_title', 'title': row['title']}); continue
            when, when_basis = captured_at(payload)
            targets[url].append({'kind': 'directory_record', 'record_id': row['id'], 'dataset': row['dataset'], 'publication': publication,
                                 'title': row['title'] or '', 'state': row['state'] or '', 'county': row['county'] or '', 'record_kind': row['kind'] or '',
                                 'quality': row['quality'] or '', 'original_file_id': row['original_id'] or None,
                                 'original_path': str(original.relative_to(ROOT)).replace('\\', '/') if original else None,
                                 'original_sha256': (payload.get('raw_sha256') or payload.get('sha256')) if original else None,
                                 'original_bytes': original_bytes, 'original_hash_verified': verified,
                                 'text_file_id': row['text_id'] or None, 'text_characters': characters, 'usable_text': bool(characters),
                                 'captured_at': when, 'captured_at_basis': when_basis, 'review_flags': flags,
                                 'match_basis': basis, 'recorded_source_url': row['source_url'], 'final_url': payload.get('final_url')})
        # Roll observation rows up to the consolidated entity that displays them.
        members = {}
        for record_id, display_id in con.execute('SELECT record_id, display_id FROM display_members'):
            members.setdefault(record_id, set()).add(display_id)
    for url, observations in judge_observations.items():
        entities = {t['record_id']: t for t in targets.get(url, []) if t['kind'] == 'judge_entity'}
        for record_id, _title, payload in observations:
            stats['judge_observations'] += 1
            owners = members.get(record_id, set()) & set(entities)
            if not owners:
                excluded.append({'source_url': url, 'kind': 'judge_observation', 'record_id': record_id, 'reason': 'observation_without_linked_entity'}); continue
            for owner in owners:
                entity = entities[owner]
                entity['observation_count'] += 1
                when = latest_saved(payload.get('captured_at'))
                if when and (entity['captured_at'] is None or when > entity['captured_at']): entity['captured_at'] = when
    return targets, stats


def retained_targets(catalog_urls, linked_urls, canonical, excluded):
    """Successful canonical captures that never reached the directory; verified afresh and confined to registered roots."""
    targets, stats = defaultdict(list), Counter()
    rows = canonical.execute('SELECT * FROM latest_documents').fetchall()
    candidates = []
    for row in rows:
        # One capture can satisfy two references: the address requested and the address it redirected to.
        options = [(row['source_url'], 'exact_source_url')]
        if row['final_url'] and row['final_url'] != row['source_url']: options.append((row['final_url'], 'exact_final_url'))
        candidates.extend((row, url, basis) for url, basis in options if url in catalog_urls)
    for row, url, basis in candidates:
        stats['canonical_candidates'] += 1
        if url in linked_urls:
            stats['already_linked_in_directory'] += 1; continue
        record = {'source_url': url, 'kind': 'retained_capture', 'version_id': row['version_id'], 'collection': row['collection'], 'match_basis': basis}
        raw_path = (row['raw_path'] or '').replace('\\', '/')
        if not any(raw_path == base or raw_path.startswith(base + '/') for base in ASSET_ROOTS):
            excluded.append({**record, 'reason': 'unregistered_root', 'raw_path': raw_path}); continue
        if row['index_text_status'] != 'searchable':
            excluded.append({**record, 'reason': 'canonical_' + str(row['index_text_status'])}); continue
        raw = workspace_file(raw_path)
        if raw is None:
            excluded.append({**record, 'reason': 'original_missing'}); continue
        if not raw.stat().st_size:
            excluded.append({**record, 'reason': 'original_empty', 'raw_path': raw_path}); continue
        if sha256(raw) != row['raw_sha256']:
            excluded.append({**record, 'reason': 'original_hash_mismatch'}); continue
        text = workspace_file(row['text_path']) if row['text_path'] else None
        text_sha = None; characters = 0
        if text is not None:
            text_sha = sha256(text)
            if row['text_file_sha256'] and text_sha != row['text_file_sha256']:
                excluded.append({**record, 'reason': 'text_hash_mismatch'}); continue
            characters = len(text.read_text(encoding='utf-8', errors='replace').strip())
        if review_flags(row['title'], characters):
            excluded.append({**record, 'reason': 'review_flags', 'flags': review_flags(row['title'], characters), 'title': row['title']}); continue
        targets[url].append({'kind': 'retained_capture', 'version_id': row['version_id'], 'collection': row['collection'], 'title': row['title'] or '',
                             'raw_path': str(raw.relative_to(ROOT)).replace('\\', '/'), 'raw_sha256': row['raw_sha256'], 'raw_bytes': raw.stat().st_size,
                             'content_type': row['content_type'] or '', 'text_path': str(text.relative_to(ROOT)).replace('\\', '/') if text else None,
                             'text_sha256': text_sha, 'text_characters': characters, 'retrieved_at': row['retrieved_at'],
                             'retrieval_time_basis': row['retrieval_time_basis'], 'index_text_status': row['index_text_status'],
                             'publication': 'retained_capture_not_in_published_release', 'match_basis': basis, 'final_url': row['final_url'],
                             'jurisdiction_claim': row['jurisdiction'], 'category_claim': row['category']})
    return targets, stats


def main():
    catalog_sha, catalog = load_catalog()
    canonical_gate = json.loads(CANONICAL_GATE.read_text(encoding='utf-8')) if CANONICAL_GATE.exists() else {}
    receipt = json.loads(BUILD_RECEIPT.read_text(encoding='utf-8'))
    pilot_urls = {json.loads(line)['source_url'] for line in PILOT.read_text(encoding='utf-8-sig').splitlines() if line.strip()} if PILOT.exists() else set()
    excluded = []
    with ro(CANONICAL) as canonical:
        targets, stats = directory_targets(set(catalog), canonical, excluded)
        document_urls = {url for url, items in targets.items() if any(t['kind'] == 'directory_record' for t in items)}
        retained, retained_stats = retained_targets(set(catalog), document_urls, canonical, excluded)
    for url, items in retained.items(): targets[url].extend(items)
    rows = []
    for url in sorted(targets):
        items = [t for t in targets[url] if t['kind'] != 'judge_entity' or t['observation_count'] > 0 or t['source_observation_ids']]
        if not items: continue
        entry = catalog[url]
        basis = sorted({t.get('match_basis', 'exact_source_url') for t in items})
        rows.append({'source_url': url, 'source_id': entry['id'], 'registry_id': entry['source_record']['id'],
                     'match_basis': basis[0] if len(basis) == 1 else 'mixed', 'targets': items})
    payload = ('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n').encode('utf-8')
    (HERE / 'links.jsonl').write_bytes(payload)
    excluded_payload = ('\n'.join(json.dumps(r, ensure_ascii=False) for r in excluded) + ('\n' if excluded else '')).encode('utf-8')
    (HERE / 'excluded.jsonl').write_bytes(excluded_payload)
    kinds = Counter(t['kind'] for r in rows for t in r['targets'])
    publications = Counter(t['publication'] for r in rows for t in r['targets'] if t['kind'] == 'directory_record')
    datasets = Counter(t['dataset'] for r in rows for t in r['targets'] if t['kind'] == 'directory_record')
    verified = Counter(str(t.get('original_hash_verified')) for r in rows for t in r['targets'] if t['kind'] == 'directory_record')
    retained_basis = Counter(t['match_basis'] for r in rows for t in r['targets'] if t['kind'] == 'retained_capture')
    gate = {
        'status': 'passed', 'ready': True, 'generated_at': now(),
        'links_sha256': hashlib.sha256(payload).hexdigest(), 'excluded_sha256': hashlib.sha256(excluded_payload).hexdigest(),
        'asset_roots': list(ASSET_ROOTS), 'source_catalog_sha256': catalog_sha,
        'directory_build_receipt': receipt, 'canonical_index_validation': canonical_gate,
        'counts': {'source_references': len(catalog), 'linked_references': len(rows),
                   'references_with_pilot_capture': sum(u in pilot_urls for u in targets), 'pilot_capture_urls': len(pilot_urls),
                   'targets': dict(kinds), 'directory_record_publications': dict(publications), 'directory_record_datasets': dict(datasets),
                   'directory_original_hash_verified': dict(verified), 'retained_capture_match_basis': dict(retained_basis),
                   'candidates': dict(stats) | dict(retained_stats), 'excluded_by_reason': dict(Counter(e['reason'] for e in excluded)),
                   'usable_text_records': sum(1 for r in rows for t in r['targets'] if t['kind'] == 'directory_record' and t['usable_text']),
                   'judge_observations_linked': sum(t['observation_count'] for r in rows for t in r['targets'] if t['kind'] == 'judge_entity')},
        'checks': ['source catalog gate and hash', 'exact URL equality only (no normalization)', 'directory record eligibility and file presence',
                   'fresh SHA-256 of every original with a recorded hash', 'soft-404/challenge title rejection', 'empty artifact rejection',
                   'judge observations rolled up through display membership', 'retained captures confined to registered roots with fresh raw/text hashes',
                   'canonical empty-text captures excluded', 'no network access'],
        'qualification': ('Links attach existing saved records to source references by exact URL. They are not new downloads, '
                          'do not establish the publisher page is currently available, and unlinked references are not claimed absent. '
                          'Saved dates record collection time; legal currency is never inferred.'),
    }
    (HERE / 'validation.json').write_text(json.dumps(gate, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'linked_references': len(rows), 'targets': dict(kinds), 'excluded': dict(Counter(e['reason'] for e in excluded)),
                      'links_sha256': gate['links_sha256']}, ensure_ascii=False))


if __name__ == '__main__': main()
