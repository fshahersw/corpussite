"""Validate and preserve a stopped finite registry pass without changing its queue."""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    if not args.label.isalnum():
        raise ValueError('Snapshot label must be alphanumeric.')
    out = ROOT / args.label
    out.mkdir(exist_ok=True)
    if (out / 'validation.json').exists():
        raise RuntimeError('Existing checkpoint is immutable; use a new label.')
    c = sqlite3.connect((ROOT / 'corpus.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('BEGIN')
    try:
        tables = {name: [dict(r) for r in c.execute('SELECT * FROM ' + name)] for name in ('resources', 'fetches', 'contexts', 'resource_contexts', 'hosts', 'runs')}
    finally:
        c.rollback()
        c.close()
    assert tables['runs'][-1]['ended_at'], 'Latest run is still open.'
    assert not any(r['status'] == 'fetching' for r in tables['resources']), 'Claims still fetching.'
    checked = {}
    def checked_artifact(relative):
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT.resolve()), 'Artifact escapes collection.'
        if relative not in checked:
            raw = path.read_bytes()
            checked[relative] = {'path': relative, 'sha256': sha(raw), 'bytes': len(raw)}
        return checked[relative]
    captures = []
    for row in tables['fetches']:
        if row['raw_path']:
            raw = checked_artifact(row['raw_path'])
            assert raw['sha256'] == row['sha256']
            assert raw['bytes'] == row['byte_count']
        if row['metadata_path']:
            checked_artifact(row['metadata_path'])
            meta = json.loads((ROOT / row['metadata_path']).read_text(encoding='utf-8'))
            assert meta['fetch_id'] == row['id']
            if row['text_path']:
                text = checked_artifact(row['text_path'])
                assert text['sha256'] == meta['text_sha256']
            if row['status'] == 'downloaded':
                nonempty = bool(row['text_path'] and (ROOT / row['text_path']).read_text(encoding='utf-8').strip())
                captures.append({'url': meta['requested_url'], 'fetch_id': row['id'], 'http_status': row['http_status'], 'title': meta.get('title'), 'raw_sha256': row['sha256'], 'raw_path': row['raw_path'], 'metadata_path': row['metadata_path'], 'text_path': row['text_path'], 'text_sha256': meta.get('text_sha256'), 'text_char_count': meta.get('text_char_count'), 'content_status': 'nonempty_extracted_entry_text' if nonempty else 'no_usable_extracted_body', 'raw_complete': bool(row['raw_complete']), 'authority_classification': 'official_government_domain_registration', 'county_name_association': 'UNREVIEWED', 'court_fips_association_verified': False})
    old = sqlite3.connect((ROOT.parents[1] / 'corpus/county_sites/corpus.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    old.row_factory = sqlite3.Row
    old_verified = set()
    for row in tables['contexts']:
        seed = json.loads(row['seed_json'])
        assert seed['source_family'] == 'official_cisa_county_gov_domain_registry'
        assert seed['court_fips_association_verified'] is False
        for lineage in seed['transfer_lineage']:
            current = old.execute('SELECT status,attempts,raw_path,text_path,metadata_path,sha256 FROM resources WHERE id=?', (lineage['resource_id'],)).fetchone()
            assert current['status'] == 'pending' and current['attempts'] == 0
            assert not any(current[k] for k in ('raw_path', 'text_path', 'metadata_path', 'sha256'))
            old_verified.add(lineage['resource_id'])
    old.close()
    summary = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'label': args.label, 'status': 'stopped_checkpoint_validated', 'contexts': len(tables['contexts']), 'resources': len(tables['resources']), 'fetch_records': len(tables['fetches']), 'resource_status_counts': dict(Counter(r['status'] for r in tables['resources'])), 'captured_content_status_counts': dict(Counter(r['content_status'] for r in captures)), 'hashed_artifact_files': len(checked), 'old_pending_transfer_rows_unchanged': len(old_verified), 'latest_run': {k:v for k,v in tables['runs'][-1].items() if k != 'config_json'}, 'full_corpus_complete': False, 'empty_http_200_policy': 'HTTP success alone is not usable content; zero or whitespace-only extracted bodies are explicitly separated.', 'authority_limit': 'Government-domain registration does not verify county/FIPS or court authority.'}
    for name, rows in {**tables, 'capture_validation': captures, 'artifact_hashes': list(checked.values())}.items():
        (out / (name + '.jsonl')).write_bytes(b''.join(packed(r) + b'\n' for r in rows))
    for name in ('seeds.jsonl', 'config.json', 'preparation.json'):
        (out / (name + '.snapshot')).write_bytes((ROOT / name).read_bytes())
    (out / 'validation.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    files = [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())} for p in sorted(out.iterdir()) if p.is_file()]
    (out / 'checkpoint_hashes.json').write_text(json.dumps(files, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
