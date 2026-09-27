"""Offline, exact-artifact Washington court-label supplement; no collection edits."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'corpus/county_local_rules_washington_20260914'
sys.path.insert(0, str(ROOT / 'pipeline'))
import corpus_crawler as engine


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rel(path):
    return path.resolve().relative_to(ROOT).as_posix()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def read_db(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def exact_file(name, expected):
    path = (ROOT / name).resolve()
    path.relative_to(ROOT)
    data = path.read_bytes()
    assert expected and sha(data) == expected, 'Artifact hash mismatch: ' + name
    return data


def label_values(value):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'court_label' and isinstance(item, str):
                found.append(item)
            else:
                found.extend(label_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(label_values(item))
    return sorted(set(found))


def main():
    out = ROOT / 'reports/counties/local_rules_washington_20260914/court_labels' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    frozen = defaultdict(list)
    batch_inputs = []
    for directory in sorted((ROOT / 'sources/counties/local_documents_20260914/batches').glob('wa_local_rules_*')):
        seed_path, review_path = directory / 'seeds.jsonl', directory / 'root_review.json'
        seed_data, review_data = seed_path.read_bytes(), review_path.read_bytes()
        review = json.loads(review_data)
        assert review['validated'] and review['unresolved_material_findings'] == 0
        assert review['input_sha256']['seeds.jsonl'] == sha(seed_data)
        packet = {'path': rel(seed_path), 'sha256': sha(seed_data), 'root_review_path': rel(review_path), 'root_review_sha256': sha(review_data)}
        batch_inputs.append(packet)
        for number, line in enumerate(seed_data.decode('utf-8').splitlines(), 1):
            seed = json.loads(line)
            frozen[sha(canonical(seed).encode())].append({**packet, 'line_number': number})

    dbpath = ROOT / COLLECTION / 'corpus.sqlite3'
    with closing(read_db(dbpath)) as db:
        resources = [dict(row) for row in db.execute('SELECT * FROM resources ORDER BY id')]
        contexts = {row['id']: dict(row) for row in db.execute('SELECT * FROM contexts')}
        associations = defaultdict(list)
        for row in db.execute('SELECT * FROM resource_contexts'):
            associations[row['resource_id']].append(dict(row))
    parents, db_links = {}, {}
    mappings = []
    for resource in resources:
        assert resource['status'] == 'downloaded' and resource['raw_complete'] and resource['last_http_status'] == 200
        raw_name = COLLECTION + '/' + resource['raw_path']
        exact_file(raw_name, resource['sha256'])
        metadata_name = COLLECTION + '/' + resource['metadata_path']
        metadata_bytes = (ROOT / metadata_name).read_bytes()
        metadata = json.loads(metadata_bytes)
        assert metadata['sha256'] == resource['sha256'] and metadata['requested_url'] == resource['url']
        assert metadata['fetch_id'] == resource['last_fetch_id'] and metadata['raw_path'] == resource['raw_path']
        labels = []
        for association in associations[resource['id']]:
            context = contexts[association['context_id']]
            seed = json.loads(context['seed_json'])
            seed_hash = sha(canonical(seed).encode())
            assert frozen[seed_hash], 'Seed must exactly match a reviewed frozen packet'
            assert context['seed_url'] == seed['url'] == resource['url'] and association['depth'] == 0
            assert seed['court_label'] and seed['jurisdiction']['state'] == 'Washington'
            assert json.loads(context['jurisdiction_json']) == seed['jurisdiction']
            for proof in seed['provenance']:
                assert proof['anchor_text'] == seed['court_label'] and proof['target_url'] == resource['url']
                key = (proof['parent_raw_path'], proof['parent_raw_sha256'], proof['parent_metadata_sha256'])
                if key not in parents:
                    original = exact_file(proof['parent_raw_path'], proof['parent_raw_sha256'])
                    parent_meta = json.loads(exact_file(proof['parent_metadata_path'], proof['parent_metadata_sha256']))
                    assert parent_meta['requested_url'] == proof['parent_url'] and parent_meta['fetch_id'] == proof['source_fetch_id']
                    encoding = parent_meta.get('headers', {}).get('content-encoding', '').lower()
                    assert encoding in ('', 'identity', 'gzip')
                    decoded = gzip.decompress(original) if encoding == 'gzip' else original
                    text, charset = engine.decode_text(decoded, parent_meta.get('headers', {}).get('content-type', ''))
                    parser = engine.TextHTML(); parser.feed(text); parser.close()
                    base = engine.canonical_url(parser.base_href, proof['parent_url']) if parser.base_href else proof['parent_url']
                    parents[key] = {(entry['href'], ' '.join(entry['text'].split())[:2000], engine.canonical_url(entry['href'], base)) for entry in parser.links if entry['relation'] == 'link'}
                assert (proof['raw_href'], proof['anchor_text'], proof['target_url']) in parents[key]
                link_key = (proof['database'], proof['link_id'])
                if link_key not in db_links:
                    with closing(read_db(ROOT / proof['database'])) as db:
                        db_links[link_key] = dict(db.execute('SELECT * FROM links WHERE id=?', (proof['link_id'],)).fetchone())
                link = db_links[link_key]
                for observed, evidence in [('source_id', 'source_resource_id'), ('fetch_id', 'source_fetch_id'), ('target_url', 'target_url'), ('raw_href', 'raw_href'), ('anchor_text', 'anchor_text')]:
                    assert link[observed] == proof[evidence]
            labels.append({'court_label': seed['court_label'], 'label_basis': 'Exact saved official Washington judiciary index anchor; not a reviewed PDF title or territorial jurisdiction finding.', 'context_id': context['id'], 'seed_url': seed['url'], 'seed_json_sha256': seed_hash, 'frozen_seed_records': frozen[seed_hash], 'source_family': seed['source_family'], 'source_association_strength': seed['source_association_strength'], 'jurisdiction': seed['jurisdiction'], 'candidate_county_association': seed.get('candidate_county_association'), 'site_authority_verified': seed.get('site_authority_verified', False), 'county_geography_association_verified': seed.get('county_geography_association_verified', False), 'court_fips_association_verified': seed.get('court_fips_association_verified', False), 'provenance': seed['provenance']})
        assert labels
        mappings.append({'collection': COLLECTION, 'source_url': resource['url'], 'raw_sha256': resource['sha256'], 'resource_id': resource['id'], 'source_fetch_id': resource['last_fetch_id'], 'raw_path': raw_name, 'metadata_path': metadata_name, 'metadata_sha256': sha(metadata_bytes), 'court_labels': sorted({row['court_label'] for row in labels}), 'geoid_associations': sorted({row['jurisdiction']['geoid'] for row in labels if row['jurisdiction'].get('geoid')}), 'county_assignment_inferred_from_url': False, 'labels': labels})

    assert len(mappings) == 129
    keys = {(row['collection'], row['source_url'], row['raw_sha256']) for row in mappings}
    assert len(keys) == len(mappings)
    export_audits = []
    for name in ['reports/counties/local_documents_20260914/resources.jsonl', 'delivery/focused_legal_corpus/county_local_resources/captures.jsonl']:
        data = (ROOT / name).read_bytes()
        selected = [row for row in (json.loads(line) for line in data.decode('utf-8').splitlines() if line) if row.get('collection') == COLLECTION]
        export_audits.append({'path': name, 'snapshot_sha256': sha(data), 'wa_records': len(selected), 'records_with_structured_court_label': sum(bool(label_values(row)) for row in selected), 'label_values': sorted({label for row in selected for label in label_values(row)})})
    with closing(read_db(ROOT / 'catalog/documents.sqlite3')) as db:
        indexed = [dict(row) for row in db.execute('SELECT collection,source_url,raw_sha256,version_id,title,source_evidence_json,jurisdictions_json FROM latest_documents WHERE collection IN (?,?)', (COLLECTION, COLLECTION + '/ocr'))]
    index_snapshot = []
    for row in indexed:
        parent_collection = COLLECTION if row['collection'] in (COLLECTION, COLLECTION + '/ocr') else None
        matched = (parent_collection, row['source_url'], row['raw_sha256']) in keys
        index_snapshot.append({**row, 'parent_key_matches_supplement': matched, 'structured_court_labels': label_values(json.loads(row['source_evidence_json'])) + label_values(json.loads(row['jurisdictions_json']))})
    target = out / 'court_labels.jsonl'
    target.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in mappings), encoding='utf-8')
    (out / 'index_evidence_snapshot.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in index_snapshot), encoding='utf-8')
    report = {'validated_at_utc': datetime.now(timezone.utc).isoformat(), 'valid': True, 'mapping_records': len(mappings), 'county_assigned_records': sum(bool(row['geoid_associations']) for row in mappings), 'county_unassigned_records': sum(not row['geoid_associations'] for row in mappings), 'unique_observed_court_labels': len({label for row in mappings for label in row['court_labels']}), 'mapping_path': rel(target), 'mapping_sha256': sha(target.read_bytes()), 'all_original_pdf_and_metadata_bindings_verified': True, 'all_seed_payloads_match_frozen_reviewed_packets': True, 'all_labels_match_hash_verified_saved_html_anchors_and_database_links': True, 'parent_html_artifacts_verified': len(parents), 'source_link_proofs_verified': len(db_links), 'export_snapshots': export_audits, 'generic_index_direct_records': sum(row['collection'] == COLLECTION for row in index_snapshot), 'generic_index_ocr_records': sum(row['collection'] == COLLECTION + '/ocr' for row in index_snapshot), 'index_records_with_structured_court_label': sum(bool(row['structured_court_labels']) for row in index_snapshot), 'index_records_bound_to_mapping': sum(row['parent_key_matches_supplement'] for row in index_snapshot), 'index_snapshot_path': rel(out / 'index_evidence_snapshot.jsonl'), 'index_snapshot_sha256': sha((out / 'index_evidence_snapshot.jsonl').read_bytes()), 'source_batches': batch_inputs, 'network_requests': 0, 'collection_or_delivery_mutations': 0, 'integration_rule': 'Exact collection + source_url + raw_sha256 join. For a known OCR derivative namespace, bind to its explicit parent collection and unchanged original PDF hash. Never fill null county associations from court-name or URL guesses.', 'legal_currency_or_territorial_scope_verified': False}
    (out / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
