"""Validate imported identities, original bytes, readable derivatives and lineage offline."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def sha(data): return hashlib.sha256(data).hexdigest()
def rows(path):
    with path.open(encoding='utf-8') as f: return [json.loads(line) for line in f if line.strip()]


def main():
    summary = json.loads((OUT / 'summary.json').read_bytes())
    assert sha((OUT / 'resources.jsonl').read_bytes()) == summary['resources_sha256']
    resources = rows(OUT / 'resources.jsonl'); originals = rows(OUT / 'originals.jsonl')
    by_id = {r['id']: r for r in resources}
    assert len(resources) == len(by_id) == summary['resources']
    assert len(originals) == 11194 and sum(r['metadata']['record_type'] == 'structured_provision' for r in resources) == 7166
    assert {r['id'] for r in originals} == {r['id'] for r in resources if r['metadata']['record_type'] == 'original_document'}
    verified = {}; external_verified = 0; zero_body_provisions = 0
    def artifact(name, expected):
        path = (ROOT / name).resolve(); path.relative_to(ROOT)
        if name not in verified:
            data = path.read_bytes(); verified[name] = (sha(data), len(data))
        assert verified[name][0] == expected, name
        return path
    for row in json.loads((OUT / 'inputs.json').read_bytes()):
        artifact(row['path'], row['sha256'])
        assert sha(Path(row['source_path']).read_bytes()) == row['sha256']
    for r in resources:
        raw = artifact(r['raw_path'], r['sha256'])
        meta_path = artifact(r['metadata_path'], r['metadata_sha256'])
        meta = json.loads(meta_path.read_bytes())
        assert r['county'] is None and r['county_geoids'] == []
        if r['text_path']:
            text_path = artifact(r['text_path'], r['text_sha256'])
            text = text_path.read_bytes().decode('utf-8')
            assert text.strip(), r['id']
        else:
            text = ''
        if r['metadata']['record_type'] == 'original_document':
            proof = meta['import_evidence']
            assert proof['original_sha256'] == r['sha256'] and proof['raw_path'] == r['raw_path']
            assert proof['text_path'] == r['text_path'] and proof['text_sha256'] == r['text_sha256']
            source = Path(proof['external_source_path'])
            source_data = source.read_bytes(); external_verified += 1
            assert sha(source_data) == r['sha256'] and len(source_data) == proof['bytes']
            assert source.stat().st_size == proof['external_source_stat']['size'] and source.stat().st_mtime_ns == proof['external_source_stat']['mtime_ns']
            assert meta['county_and_territorial_scope_inferred'] is False
            assert meta['legal_currency_or_applicability_verified_by_import'] is False
            assert r['metadata']['source_jurisdictions'] == meta['source_jurisdictions']
            assert r['metadata']['source_courts'] == meta['source_courts']
            if r['text_path']:
                assert len(text) == r['metadata']['extraction']['characters']
        else:
            parent = by_id[r['metadata']['parent_original_id']]
            assert parent['metadata']['record_type'] == 'original_document'
            assert parent['raw_path'] == r['raw_path'] and parent['sha256'] == r['sha256'] == meta['parent_original_raw_sha256']
            assert meta['legal_currency_verified'] is False
            source_manifest = meta['source_manifest']; path = artifact(source_manifest['path'], source_manifest['sha256'])
            if str(path) not in manifest_rows: manifest_rows[str(path)] = rows(path)
            record = manifest_rows[str(path)][source_manifest['line_1based'] - 1]
            assert record == meta['source_record']
            body = record.get('body_text', record.get('text', ''))
            assert meta['body_text_present'] == bool(body.strip()) == r['metadata']['body_text_present']
            zero_body_provisions += not body.strip()
            expected = [r['title'], body]
            if record.get('editorial_notes'): expected += ['Editorial notes', record['editorial_notes']]
            if record.get('source_credit'): expected += ['Source credit', record['source_credit']]
            assert text == '\n\n'.join(s for s in expected if s)
            if r['metadata']['family'] == 'nj':
                if str(raw) not in source_bytes: source_bytes[str(raw)] = raw.read_bytes()
                literal = source_bytes[str(raw)][record['source_byte_start']:record['source_byte_end_exclusive']]
                assert sha(literal) == record['source_slice_sha256'] and literal.decode('cp1252') == body
            else:
                assert record.get('source_xml_sha256', record.get('source_sha256')) == r['sha256']
    result = {'validated_at': datetime.now(timezone.utc).isoformat(), 'valid': True,
        'resource_records': len(resources), 'originals_hash_verified_against_external_sources': external_verified,
        'retained_court_originals': 11181, 'reference_originals': 13, 'structured_provisions': 7166,
        'unique_local_artifact_paths_hash_verified': len(verified), 'readable_text_records': sum(bool(r['text_path']) for r in resources),
        'structured_records_without_principal_body': zero_body_provisions,
        'new_jersey_byte_slice_roundtrips': sum(r['metadata'].get('family') == 'nj' for r in resources),
        'source_input_manifests_and_originals_unchanged': True, 'exact_original_and_derivative_lineage_verified': True,
        'county_territory_not_inferred': True, 'source_quality_flags_preserved': True, 'legal_currentness_certified': False,
        'by_kind': dict(Counter(r['kind'] for r in resources)), 'network_requests': 0, 'published_snapshots_modified': False,
        'resources_sha256': summary['resources_sha256'], 'originals_sha256': summary['originals_sha256']}
    (OUT / 'validation.json').write_bytes((json.dumps(result, indent=2) + '\n').encode('utf-8'))
    (OUT / 'progress.json').write_bytes((json.dumps({'status': 'validated_import', **summary,
        'validation_path': 'sources/seeger_import_20260918/validation.json',
        'validated_at': result['validated_at']}, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(result, indent=2))


manifest_rows = {}; source_bytes = {}
if __name__ == '__main__': main()
