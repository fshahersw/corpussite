"""Report this isolated county legal-resource batch offline, without altering its queue."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'sources/counties/local_documents_20260914'
COLLECTION = ROOT / 'corpus/county_local_documents_20260914'
EXTRA_COLLECTIONS = [ROOT / 'corpus/county_local_rules_washington_20260914']
OUT = ROOT / 'reports/counties/local_documents_20260914'


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def artifact(path_value, expected_hash=None, collection=None, require_expected_hash=False):
    collection = collection or COLLECTION
    if not path_value:
        return {'path': None, 'exists': False, 'sha256': None, 'bytes': 0, 'hash_matches': False}
    path = (collection / path_value).resolve()
    if not path.is_relative_to(collection):
        raise ValueError('Collection artifact escapes its collection: ' + str(path_value))
    exists = path.is_file()
    actual = digest(path) if exists else None
    expected_valid = isinstance(expected_hash, str) and re.fullmatch(r'[0-9a-fA-F]{64}', expected_hash) is not None
    hash_matches = bool(exists and ((expected_valid and expected_hash.lower() == actual) or (expected_hash is None and not require_expected_hash)))
    return {'path': relative(path), 'exists': exists, 'sha256': actual, 'bytes': path.stat().st_size if exists else 0, 'expected_sha256_valid': expected_valid, 'hash_matches': hash_matches}


def context_record(context, link):
    """Preserve observed seed labels and their evidence without inferring geography."""
    seed = context['seed']
    record = {'context_id': context['id'], 'collection': link['collection'], 'seed_url': context['seed_url'], 'category': context['category'], 'jurisdiction': context['jurisdiction'], 'source_association_strength': seed.get('source_association_strength'), 'court_fips_association_verified': seed.get('court_fips_association_verified', False), 'site_authority_verified': seed.get('site_authority_verified', False), 'county_geography_association_verified': seed.get('county_geography_association_verified', False), 'depth': link['depth'], 'discovered_from': link['discovered_from']}
    if 'court_label' in seed:
        record.update(court_label=seed['court_label'], provenance=seed.get('provenance', []),
                      source_seed_evidence={'record_locator': link['collection'] + '/corpus.sqlite3#contexts/' + context['id'],
                                            'sha256': hashlib.sha256(json.dumps(seed, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest(),
                                            'source_family': seed.get('source_family'), 'source_url_basis': seed.get('source_url_basis')})
    return record


def main():
    baseline = read_rows(INPUT / 'county_ledger.jsonl')
    assert len(baseline) == len({r['geoid'] for r in baseline}) == 3144
    preparation_dirs = [INPUT] + sorted(p for p in (INPUT / 'batches').glob('*') if (p / 'summary.json').is_file())
    # General batches contain the full county graph; focused state batches may
    # contain only a subset. Union all observed URL/county pairs without dropping
    # earlier candidates when a specialized batch is added.
    initial_candidates = read_rows(INPUT / 'all_candidates.jsonl')
    unique_candidates = {}
    for directory in preparation_dirs:
        for candidate in read_rows(directory / 'all_candidates.jsonl'):
            unique_candidates[(candidate['url'], candidate.get('county_geoid'))] = candidate
    candidates = list(unique_candidates.values())
    unique_seeds = {}
    for directory in preparation_dirs:
        for seed in read_rows(directory / 'seeds.jsonl'):
            unique_seeds[(seed['url'], seed['jurisdiction']['geoid'])] = seed
    seeds = list(unique_seeds.values())
    reviewed_payloads, batch_reviews = defaultdict(set), []
    for directory in preparation_dirs:
        receipt_path = directory / 'root_review.json'
        review = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.is_file() else {}
        hashes = review.get('input_sha256', {})
        review_valid = bool(review.get('validated') is True and review.get('unresolved_material_findings') == 0 and hashes and all((directory / name).is_file() and digest(directory / name) == expected for name, expected in hashes.items()))
        batch_seeds = read_rows(directory / 'seeds.jsonl')
        if review_valid:
            for seed in batch_seeds:
                reviewed_payloads[seed['url']].add(json.dumps(seed, sort_keys=True, separators=(',', ':')))
        batch_reviews.append({'batch_directory': relative(directory), 'prepared_seed_urls': len({s['url'] for s in batch_seeds}), 'reviewed_inputs_match': review_valid, 'review_receipt': relative(receipt_path) if receipt_path.is_file() else None, 'review_receipt_sha256': digest(receipt_path) if receipt_path.is_file() else None})
    contexts, raw_resources, associations, runs = {}, [], [], []
    collection_roots = [COLLECTION, *EXTRA_COLLECTIONS]
    for collection in collection_roots:
        dbpath = collection / 'corpus.sqlite3'
        if not dbpath.is_file():
            continue
        namespace = relative(collection)
        with closing(sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            for row in db.execute('SELECT id,seed_url,category,jurisdiction_json,seed_json FROM contexts'):
                record = dict(row)
                record['jurisdiction'] = json.loads(record.pop('jurisdiction_json'))
                record['seed'] = json.loads(record.pop('seed_json'))
                record['collection'] = namespace
                contexts[(namespace, row['id'])] = record
            raw_resources.extend({'collection': namespace, **dict(r)} for r in db.execute('SELECT r.*,f.fetched_at FROM resources r LEFT JOIN fetches f ON f.id=r.last_fetch_id ORDER BY r.id'))
            associations.extend({'collection': namespace, **dict(r)} for r in db.execute('SELECT resource_id,context_id,depth,discovered_from FROM resource_contexts'))
            runs.extend({'collection': namespace, **dict(r)} for r in db.execute('SELECT id,started_at,ended_at,stop_reason,processed FROM runs ORDER BY started_at'))
    by_resource = defaultdict(list)
    for link in associations:
        context = contexts[(link['collection'], link['context_id'])]
        by_resource[(link['collection'], link['resource_id'])].append(context_record(context, link))
    resources, county_resources = [], defaultdict(list)
    issues = []
    for raw in raw_resources:
        collection = ROOT / raw['collection']
        proof = artifact(raw['raw_path'], raw['sha256'], collection)
        metaproof = artifact(raw['metadata_path'], collection=collection)
        meta, metadata_error = {}, None
        if metaproof['exists']:
            try:
                meta = json.loads((ROOT / metaproof['path']).read_text(encoding='utf-8'))
            except (UnicodeError, ValueError) as exc:
                metadata_error = type(exc).__name__
        bindings = {
            'raw_sha256': bool(meta.get('sha256') and meta['sha256'] == raw['sha256']),
            'requested_url': meta.get('requested_url') == raw['url'],
            'fetch_id': bool(raw['last_fetch_id'] and meta.get('fetch_id') == raw['last_fetch_id']),
            'raw_path': bool(raw['raw_path'] and meta.get('raw_path') == raw['raw_path']),
            'http_status': meta.get('http_status') == raw['last_http_status'],
            'raw_complete': meta.get('raw_complete') == bool(raw['raw_complete']),
        }
        metadata_bound = bool(metaproof['exists'] and metadata_error is None and all(bindings.values()))
        metaproof.update(parsed_without_error=metadata_error is None, parse_error=metadata_error, resource_binding_checks=bindings, resource_binding_valid=metadata_bound)
        textproof = artifact(raw['text_path'], meta.get('text_sha256'), collection, require_expected_hash=True)
        textproof['expected_sha256'] = meta.get('text_sha256')
        textproof['metadata_path_matches'] = bool(raw['text_path'] and meta.get('text_path') == raw['text_path'])
        captured = raw['status'] == 'downloaded' and raw['last_http_status'] == 200 and raw['raw_complete'] == 1 and proof['hash_matches'] and metadata_bound
        text_present = False
        textproof['strict_utf8_valid'] = None
        if textproof['exists']:
            try:
                text_present = bool((ROOT / textproof['path']).read_text(encoding='utf-8').strip())
                textproof['strict_utf8_valid'] = True
            except UnicodeError:
                textproof['strict_utf8_valid'] = False
        content_saved = captured and text_present and textproof['hash_matches'] and textproof['metadata_path_matches'] and textproof['strict_utf8_valid'] is True and raw['extraction_status'] in ('extracted', 'text_truncated') and meta.get('extraction_status') == raw['extraction_status']
        record = {'collection': raw['collection'], 'resource_key': raw['collection'] + ':' + str(raw['id']), 'resource_id': raw['id'], 'url': raw['url'], 'status': raw['status'], 'http_status': raw['last_http_status'], 'attempts': raw['attempts'], 'fetched_at': raw['fetched_at'], 'title': raw['title'], 'raw_complete': bool(raw['raw_complete']), 'raw_evidence': proof, 'text_evidence': textproof, 'metadata_evidence': metaproof, 'extraction_status': raw['extraction_status'], 'detected_type': meta.get('detected_type'), 'public_page_or_document_captured': bool(captured), 'local_legal_resource_text_captured': bool(content_saved), 'direct_document_captured': bool(captured and meta.get('detected_type') in ('pdf', 'docx', 'doc', 'rtf', 'csv', 'xml', 'json', 'xlsx')), 'contexts': by_resource[(raw['collection'], raw['id'])], 'error': raw['error']}
        if raw['status'] == 'downloaded' and not proof['hash_matches']:
            issues.append({'collection': raw['collection'], 'resource_id': raw['id'], 'issue': 'downloaded_raw_missing_or_hash_mismatch'})
        if raw['status'] == 'downloaded' and not metadata_bound:
            issues.append({'collection': raw['collection'], 'resource_id': raw['id'], 'issue': 'downloaded_metadata_missing_invalid_or_binding_mismatch', 'checks': bindings})
        if raw['status'] == 'downloaded' and raw['extraction_status'] in ('extracted', 'text_truncated') and not (textproof['hash_matches'] and textproof['metadata_path_matches'] and textproof['strict_utf8_valid'] is True and meta.get('extraction_status') == raw['extraction_status']):
            issues.append({'collection': raw['collection'], 'resource_id': raw['id'], 'issue': 'extracted_text_missing_hash_path_encoding_or_status_mismatch'})
        resources.append(record)
        for fips in {c['jurisdiction'].get('geoid') for c in record['contexts']} - {None}:
            county_resources[fips].append(record)
    semantic_path = OUT / 'content_kind_sample.jsonl'
    semantic_rows = {r['resource_key']: r for r in read_rows(semantic_path)} if semantic_path.is_file() else {}
    for record in resources:
        review = semantic_rows.get(record['resource_key'])
        bound = bool(review and review['raw_evidence']['sha256'] == record['raw_evidence']['sha256'] and review['text_evidence']['sha256'] == record['text_evidence']['sha256'])
        record['semantic_review'] = {'reviewed': bound, 'actual_resource_kind': review['actual_resource_kind'] if bound else None,
                                     'document_shape': review['document_shape'] if bound else None,
                                     'contains_actual_case_filing': review['contains_actual_case_filing'] if bound else None,
                                     'judicial_administration_resource': review['judicial_administration_resource'] if bound else None,
                                     'legal_effective_status': review['legal_effective_status'] if bound else None,
                                     'source': relative(semantic_path) if bound else None}
    by_county_candidates = defaultdict(list)
    for candidate in candidates:
        by_county_candidates[candidate['county_geoid']].append(candidate)
    initial_by_county = defaultdict(list)
    for candidate in initial_candidates:
        initial_by_county[candidate['county_geoid']].append(candidate)
    prepared_per_county = Counter(s['jurisdiction']['geoid'] for s in seeds if s['jurisdiction']['geoid'] is not None)
    ledger = []
    for row in baseline:
        fips = row['geoid']; records = county_resources[fips]
        ledger.append({**row, 'initial_candidate_categories': dict(Counter(r['category'] for r in initial_by_county[fips])), 'observed_candidate_categories_all_batches': dict(Counter(r['category'] for r in by_county_candidates[fips])), 'observed_legal_candidate_urls_all_batches': len(by_county_candidates[fips]), 'prepared_seed_associations_all_batches': prepared_per_county[fips], 'collection_resource_count': len(records), 'collection_resource_statuses': dict(Counter(r['status'] for r in records)), 'captured_local_resource_pages_or_documents': sum(r['public_page_or_document_captured'] for r in records), 'captured_local_resource_with_text': sum(r['local_legal_resource_text_captured'] for r in records), 'captured_direct_documents': sum(r['direct_document_captured'] for r in records), 'local_collection_resource_keys': [r['resource_key'] for r in records], 'local_corpus_complete': False, 'progress_status': 'local_resource_text_saved_court_territorial_scope_not_verified' if any(r['local_legal_resource_text_captured'] for r in records) else 'local_resource_raw_saved' if any(r['public_page_or_document_captured'] for r in records) else 'collection_attempted_or_queued' if records else row['status']})
    nonterminal = {'pending', 'fetching', 'retry_wait'}
    seed_contexts = defaultdict(list)
    for record in resources:
        for seed_url in {c['seed_url'] for c in record['contexts']}:
            seed_contexts[seed_url].append(record)
    seed_urls = {s['url'] for s in seeds}
    ingested_seed_urls = {c['seed_url'] for c in contexts.values()}
    ingested_reviewed_seed_urls = {c['seed_url'] for c in contexts.values() if json.dumps(c['seed'], sort_keys=True, separators=(',', ':')) in reviewed_payloads.get(c['seed_url'], set())}
    finished_seeds = sum(bool(seed_contexts[url]) and all(r['status'] not in nonterminal for r in seed_contexts[url]) for url in ingested_reviewed_seed_urls)
    summary = {'updated_at_utc': datetime.now(timezone.utc).isoformat(), 'collection': relative(COLLECTION), 'preparation_directories': [relative(p) for p in preparation_dirs], 'network_requests_by_reporter': 0, 'queue_mutations': 0, 'baseline_counties_and_equivalents': 3144, 'prepared_seed_urls': len(seed_urls), 'prepared_counties': len({s['jurisdiction']['geoid'] for s in seeds} - {None}), 'prepared_county_unassigned_urls': len({s['url'] for s in seeds if s['jurisdiction']['geoid'] is None}), 'resource_status_counts': dict(Counter(r['status'] for r in resources)), 'resources': len(resources), 'captured_pages_or_documents': sum(r['public_page_or_document_captured'] for r in resources), 'captured_local_resources_with_text': sum(r['local_legal_resource_text_captured'] for r in resources), 'captured_direct_documents': sum(r['direct_document_captured'] for r in resources), 'county_associations_with_saved_local_resource_text': sum(bool(r['captured_local_resource_with_text']) for r in ledger), 'county_associations_with_saved_direct_documents': sum(bool(r['captured_direct_documents']) for r in ledger), 'verified_county_court_authority_count': 0, 'seed_jobs_terminal_in_this_snapshot': finished_seeds, 'finite_seed_batch_terminal_percent': round(100 * finished_seeds / len(seed_urls), 2) if seed_urls else None, 'full_county_or_national_corpus_percent': None, 'full_corpus_complete': False, 'runs': runs, 'validation': {'valid': not issues, 'issues': issues}, 'notes': ['These counts cover new local legal-resource URLs, excluding previously saved county entry pages.', 'Court information HTML is a local resource; it is not automatically a filing or a local rule document.', 'Counts grouped by county include unverified source associations. They do not independently establish court jurisdiction or legal authority.', 'Terminal batch percentage measures exhausted work items, including access failures; it does not measure corpus completeness.', 'The national denominator of all online local laws or filings is not known.']}
    summary.update(collections=[relative(p) for p in collection_roots], batch_reviews=batch_reviews,
                   reviewed_prepared_seed_urls=len(reviewed_payloads), ingested_seed_urls=len(ingested_seed_urls),
                   ingested_reviewed_seed_urls=len(ingested_reviewed_seed_urls), ingested_unreviewed_seed_urls=len(ingested_seed_urls - ingested_reviewed_seed_urls),
                   prepared_not_ingested_seed_urls=len(seed_urls - ingested_seed_urls),
                   finite_seed_batch_terminal_percent=round(100 * finished_seeds / len(ingested_reviewed_seed_urls), 2) if ingested_reviewed_seed_urls else None,
                   terminal_percent_denominator='Ingested seed URLs whose complete seed payload matches a hash-bound reviewed preparation. Prepared but uningested packets are excluded.',
                   collection_resource_status_counts={relative(p): dict(Counter(r['status'] for r in resources if r['collection'] == relative(p))) for p in collection_roots})
    summary.update(semantic_reviewed_resources=sum(r['semantic_review']['reviewed'] for r in resources),
                   semantic_reviewed_kind_counts=dict(Counter(r['semantic_review']['actual_resource_kind'] for r in resources if r['semantic_review']['reviewed'])),
                   semantic_sample_path=relative(semantic_path) if semantic_path.is_file() else None,
                   semantic_sample_sha256=digest(semantic_path) if semantic_path.is_file() else None,
                   actual_case_filing_corpus_count=None)
    summary['notes'].append('Discovery categories are URL-selection hints. Capture totals include county administration pages; only the separately evidence-bound semantic sample has reviewed actual resource kinds. Unreviewed resource kinds and the full actual case-filing count remain unknown.')
    for batch in summary['batch_reviews']:
        packet_urls = {s['url'] for s in read_rows(ROOT / batch['batch_directory'] / 'seeds.jsonl')}
        packet_ingested = packet_urls & ingested_reviewed_seed_urls
        packet_terminal = sum(bool(seed_contexts[url]) and all(r['status'] not in nonterminal for r in seed_contexts[url]) for url in packet_ingested)
        batch.update(ingested_reviewed_seed_urls=len(packet_ingested), terminal_ingested_seed_urls=packet_terminal,
                     terminal_percent=round(100 * packet_terminal / len(packet_ingested), 2) if packet_ingested else None)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in [('county_ledger.jsonl', ledger), ('resources.jsonl', resources)]:
        (OUT / name).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows), encoding='utf-8')
    (OUT / 'progress.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('runs', 'notes')}, indent=2))


if __name__ == '__main__':
    main()
