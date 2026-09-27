"""Independent offline identity/ref validation for the pending directory supplement."""
from collections import Counter
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def main():
    summary = json.loads((OUT / 'summary.json').read_bytes())
    frozen = json.loads((OUT / 'published_keys.snapshot.json').read_bytes())
    raw = (OUT / 'resources.jsonl').read_bytes()
    assert sha(raw) == summary['resources_sha256']
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    assert len(rows) == summary['resources'] == len({r['id'] for r in rows})
    keys = {(r['metadata']['collection'], r['source_url'], r['sha256']) for r in rows}
    assert len(keys) == len(rows) and keys.isdisjoint({tuple(k) for k in frozen['keys']})
    published_path = ROOT / summary['published_database_path']
    assert sha(published_path.read_bytes()) == summary['published_database_sha256'] == frozen['published_database_sha256']
    verified_files = {}
    with ExitStack() as stack:
        databases = {}
        for snapshot in summary['collection_snapshots']:
            collection = snapshot['collection']
            db = stack.enter_context(closing(sqlite3.connect((ROOT / collection / 'corpus.sqlite3').as_uri() + '?mode=ro', uri=True)))
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            databases[collection] = db
        pdb = stack.enter_context(closing(sqlite3.connect(published_path.as_uri() + '?mode=ro', uri=True)))
        published = set(pdb.execute('SELECT collection,source_url,raw_sha256 FROM documents'))
        assert keys.isdisjoint(published)
        for row in rows:
            assert row['quality'] == 'Awaiting publication validation'
            evidence = row['metadata']; collection = evidence['collection']
            current = dict(databases[collection].execute('SELECT * FROM resources WHERE id=?', (evidence['resource_id'],)).fetchone())
            assert current == evidence['resource_row']
            assert current['status'] == 'downloaded' and current['last_http_status'] == 200 and current['raw_complete'] == 1
            assert current['url'] == row['source_url'] and current['sha256'] == row['sha256']
            assert sha(canonical(current)) == evidence['resource_row_sha256']
            for field in ('raw_evidence', 'metadata_evidence', 'text_evidence'):
                proof = evidence[field]
                if proof is None:
                    assert field == 'text_evidence' and row['text_path'] is None
                    continue
                path = (ROOT / proof['path']).resolve()
                path.relative_to((ROOT / collection).resolve())
                data = path.read_bytes()
                assert len(data) == proof['bytes'] and sha(data) == proof['sha256']
                verified_files[proof['path']] = proof['sha256']
            assert row['raw_path'] == evidence['raw_evidence']['path']
            assert row['sha256'] == evidence['raw_evidence']['sha256']
            meta = json.loads((ROOT / evidence['metadata_path']).read_bytes())
            assert meta == evidence['source_metadata'] and evidence['metadata_sha256'] == evidence['metadata_evidence']['sha256']
            assert meta['requested_url'] == row['source_url'] and meta['sha256'] == row['sha256']
            assert meta['fetch_id'] == evidence['fetch_id'] == current['last_fetch_id']
            assert row['captured_at'] == meta['fetched_at']
            assert meta['http_status'] == 200 and meta['status'] == 'downloaded' and meta['raw_complete'] is True
            for field in ('raw_path', 'text_path', 'byte_count', 'extraction_status'):
                assert meta[field] == current[field] == evidence['fetch_row'][field]
            if evidence['text_evidence']:
                proof = evidence['text_evidence']
                content = (ROOT / proof['path']).read_bytes().decode('utf-8')
                assert row['text_path'] == proof['path'] and proof['sha256'] == meta['text_sha256']
                assert proof['characters'] == len(content) and proof['nonempty'] == bool(content.strip())
            for field in ('state', 'county'):
                values = {c.get('jurisdiction', {}).get(field) for c in meta['contexts']} - {None, ''}
                assert row[field] == (next(iter(values)) if len(values) == 1 else None)
            assert row['county_geoids'] == sorted({c['jurisdiction']['geoid'] for c in meta['contexts'] if c.get('jurisdiction', {}).get('geoid')})
            for context in meta['contexts']:
                seed = next(c for c in evidence['source_context_records'] if c['id'] == context['context_id'])
                assert seed['jurisdiction'] == context['jurisdiction']
                original = dict(databases[collection].execute('SELECT * FROM contexts WHERE id=?', (seed['id'],)).fetchone())
                assert json.loads(original['seed_json']) == seed['seed']
    assert sha(published_path.read_bytes()) == summary['published_database_sha256']
    result = {'validated_at': datetime.now(timezone.utc).isoformat(), 'valid': True,
        'resource_records': len(rows), 'by_group': dict(Counter(r['group'] for r in rows)),
        'unique_existing_artifacts_verified': len(verified_files),
        'exact_collection_url_raw_hash_absence_verified': True, 'downloaded_status_verified_against_source_dbs': True,
        'raw_text_metadata_binding_and_hashes_verified': True, 'explicit_metadata_geography_only': True,
        'published_database_unchanged': True, 'resources_sha256': sha(raw),
        'network_requests': 0, 'source_mutations': 0, 'publication_validation_complete': False}
    (OUT / 'validation.json').write_bytes((json.dumps(result, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
