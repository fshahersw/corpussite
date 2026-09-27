"""Prepare at most 100 literal Washington judiciary local-rule PDF links offline."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_county_local_documents_20260914 as common


def main():
    out = ROOT / 'sources/counties/local_documents_20260914/batches' / ('wa_local_rules_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    source_path = ROOT / 'sources/official_laws/state_rules_followup_20260914/scan_candidates.jsonl'
    rows = [json.loads(x) for x in source_path.read_text(encoding='utf-8').splitlines() if x.strip()]
    rows = [r for r in rows if urlsplit(r['target_url']).hostname == 'www.courts.wa.gov' and urlsplit(r['target_url']).path.startswith('/court_rules/pdf/LCR/') and urlsplit(r['target_url']).path.endswith('.pdf')]
    baseline_path = ROOT / 'sources/official_courts/datasets/counties_50_plus_dc.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    wa = [c for c in baseline if c['usps'] == 'WA']
    assert len(wa) == 39
    parents, dbs, candidates = {}, {}, []
    try:
        for row in rows:
            dbpath = ROOT / row['database']
            if row['database'] not in dbs:
                dbs[row['database']] = common.db_read(dbpath)
            db = dbs[row['database']]
            native = dict(db.execute('SELECT * FROM links WHERE id=?', (row['id'],)).fetchone())
            assert all(native[k] == row[k] for k in ('source_id', 'fetch_id', 'target_url', 'raw_href', 'anchor_text'))
            fetched = dict(db.execute('SELECT * FROM fetches WHERE id=?', (row['fetch_id'],)).fetchone())
            assert fetched['http_status'] == 200 and fetched['raw_complete'] == 1 and fetched['sha256'] == row['parent_sha256']
            assert db.execute('SELECT url FROM resources WHERE id=?', (row['source_id'],)).fetchone()[0] == row['parent_url']
            if row['raw_path'] not in parents:
                rawpath = ROOT / row['raw_path']; raw = rawpath.read_bytes()
                assert common.sha(raw) == row['parent_sha256']
                metapath = ROOT / row['metadata_path']; meta = json.loads(metapath.read_text(encoding='utf-8'))
                assert meta['sha256'] == row['parent_sha256'] and meta['fetch_id'] == row['fetch_id'] and meta['requested_url'] == row['parent_url']
                assert meta['headers'].get('content-encoding', '') in ('', 'identity')
                text, _ = common.engine.decode_text(raw, meta['headers'].get('content-type', ''))
                parser = common.engine.TextHTML(); parser.feed(text); parser.close()
                base = common.engine.canonical_url(parser.base_href, row['parent_url']) if parser.base_href else row['parent_url']
                links = {(x['href'], ' '.join(x['text'].split())[:2000], common.engine.canonical_url(x['href'], base)) for x in parser.links if x['relation'] == 'link'}
                parents[row['raw_path']] = {'links': links, 'base': base, 'title': parser.title, 'metadata_sha256': common.sha(metapath.read_bytes())}
            parent = parents[row['raw_path']]
            assert (row['raw_href'], row['anchor_text'], row['target_url']) in parent['links']
            court_label = row['anchor_text']; normalized = common.normalize(court_label)
            matches = [c for c in wa if re.search(r'\b' + re.escape(common.normalize(c['name'].removesuffix(' County'))) + r'\b', normalized)]
            municipal = 'municipal' in normalized
            county = matches[0] if len(matches) == 1 and not municipal else None
            status = 'official_WA_judiciary_label_unique_county_name' if county else 'municipal_court_county_unassigned' if municipal else 'multiple_or_unknown_counties_unassigned'
            proof = {'database': row['database'], 'source_resource_id': row['source_id'], 'source_fetch_id': row['fetch_id'], 'link_id': row['id'], 'parent_url': row['parent_url'], 'parent_raw_path': row['raw_path'], 'parent_raw_sha256': row['parent_sha256'], 'parent_metadata_path': row['metadata_path'], 'parent_metadata_sha256': parent['metadata_sha256'], 'parent_page_title': parent['title'], 'raw_href': row['raw_href'], 'anchor_text': court_label, 'target_url': row['target_url'], 'observed_link_base_url': parent['base'], 'observed_at': row['observed_at'], 'target_host_relation': 'same_exact_host', 'link_reproduced_from_raw_html': True, 'county_mapping_basis': 'Unique explicit county name in official Washington court label; no municipal-to-county inference and no exclusive territorial-scope claim.', 'county_name_candidates': [{'geoid': c['geoid'], 'name': c['name']} for c in matches], 'source_association_strength': status}
            candidates.append({'url': row['target_url'], 'county_geoid': county['geoid'] if county else None, 'county': county['name'] if county else None, 'state': 'Washington', 'usps': 'WA', 'category': 'local_rules', 'court_label': court_label, 'source_association_strength': status, 'site_authority_verified': True, 'site_authority_basis': 'Published link from the official Washington State Courts site at www.courts.wa.gov.', 'county_geography_association_verified': county is not None, 'court_fips_association_verified': False, 'candidate_county_association': None if county else proof['county_name_candidates'], 'provenance': [proof], 'selection_status': None})
    finally:
        for db in dbs.values():
            db.close()
    assert len({r['url'] for r in candidates}) == len(candidates)
    targets = {r['url'] for r in candidates}; existing = defaultdict(list); barriers = []; now = time.time()
    for path in sorted((ROOT / 'corpus').glob('*/corpus.sqlite3')):
        with closing(common.db_read(path)) as db:
            for native in db.execute('SELECT id,url,status,attempts,last_http_status,raw_path,sha256 FROM resources'):
                if native['url'] in targets:
                    existing[native['url']].append({'database': common.rel(path), **dict(native)})
            for native in db.execute("SELECT * FROM hosts WHERE host IN ('www.courts.wa.gov','courts.wa.gov') AND (coalesce(pause_reason,'') != '' OR cooldown_until > ?)", (now,)):
                barriers.append({'database': common.rel(path), **dict(native)})
    shared = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    with closing(common.db_read(shared)) as db:
        for native in db.execute("SELECT * FROM host_state WHERE host IN ('www.courts.wa.gov','courts.wa.gov') AND (coalesce(pause_reason,'') != '' OR cooldown_until > ?)", (now,)):
            barriers.append({'database': common.rel(shared), **dict(native)})
    catalog = ROOT / 'catalog/documents.sqlite3'
    with closing(common.db_read(catalog)) as db:
        for native in db.execute('SELECT source_url,raw_path,raw_sha256,version_id FROM latest_documents'):
            if native['source_url'] in targets:
                existing[native['source_url']].append({'database': common.rel(catalog), 'status': 'indexed_saved_capture', **dict(native)})
    for path in sorted((ROOT / 'corpus').glob('*/controls/robots/*.json')):
        control = json.loads(path.read_text(encoding='utf-8'))
        if common.group(control.get('origin', '')) == 'courts.wa.gov' and (control.get('problem') or control.get('in_progress')):
            barriers.append({'robots_control_path': common.rel(path), 'robots_control_sha256': common.sha(path.read_bytes()), 'problem': control.get('problem'), 'in_progress': control.get('in_progress', False)})
    candidates.sort(key=lambda r: (r['county_geoid'] or '99999', r['court_label'], r['url']))
    seeds = []
    for row in candidates:
        row['prior_records'] = existing.get(row['url'], []); row['preserved_barriers'] = barriers
        row['selection_status'] = 'deferred_existing_saved_or_enqueued_url' if row['prior_records'] else 'deferred_existing_host_or_robots_barrier' if barriers else 'deferred_batch_limit' if len(seeds) >= 100 else 'selected'
        if row['selection_status'] != 'selected':
            continue
        county = next((c for c in wa if c['geoid'] == row['county_geoid']), None)
        seeds.append({'url': row['url'], 'source_family': 'official_washington_judiciary_observed_local_rule_pdf', 'category': 'local_rules', 'court_label': row['court_label'], 'jurisdiction': {'country': 'US', 'state': 'Washington', 'state_fips': '53', 'county': row['county'], 'geoid': row['county_geoid'], 'county_fips': county['county_fips'] if county else None, 'geography_vintage': 2026, 'association_status': row['source_association_strength'], 'court_fips_association_verified': False}, 'scope': {'host': 'www.courts.wa.gov', 'path_prefixes': [urlsplit(row['url']).path]}, 'discovered_from': row['provenance'][0]['parent_url'], 'source_association_strength': row['source_association_strength'], 'site_authority_verified': True, 'county_geography_association_verified': row['county_geography_association_verified'], 'court_fips_association_verified': False, 'candidate_county_association': row['candidate_county_association'], 'source_url_basis': 'Exact observed official Washington Courts PDF href, reproduced from saved HTML.', 'provenance': row['provenance'], 'selection_rank': len(seeds) + 1, 'collection_scope': 'One exact observed public local-rule PDF plus permitted server redirects; no automatic links or pagination.'})
    config = {'allow': [{'host': 'www.courts.wa.gov', 'path_prefixes': [urlsplit(s['url']).path for s in seeds]}], 'workers': 3, 'per_host_delay': 2.0, 'timeout_seconds': 30, 'max_transfer_seconds': 180, 'max_response_bytes': 67108864, 'max_extract_chars': 20971520, 'min_free_bytes': 10737418240, 'max_depth': 0, 'max_retries': 0, 'respect_robots': True, 'follow_links': False, 'follow_external_allowed_links': False, 'pause_host_on_access_block': True, 'shared_host_dir': 'corpus/_shared_hosts', 'user_agent': 'LegalCorpusResearch/1.0'}
    if seeds:
        cfg = common.engine.Config.from_dict(config)
        assert all(cfg.allowed(s['url'], s['scope'])[0] for s in seeds)
    else:
        config['allow'] = []
    common.jsonl(out / 'seeds.jsonl', seeds); common.dump(out / 'config.json', config)
    common.jsonl(out / 'all_candidates.jsonl', candidates); common.jsonl(out / 'deferred.jsonl', [r for r in candidates if r['selection_status'] != 'selected'])
    validation = {'valid': True, 'network_requests': 0, 'legacy_queue_mutations': 0, 'all_seed_urls_literal_official_pdf_links': True, 'all_parent_raw_and_metadata_hashes_verified': True, 'all_link_database_bindings_verified': True, 'candidate_pdfs': len(candidates), 'selected_pdfs': len(seeds), 'unassigned_municipal_or_multi_county_records_preserved': True, 'court_exclusive_territorial_scope_verified': False}
    common.dump(out / 'validation.json', validation)
    summary = {'prepared_at_utc': datetime.now(timezone.utc).isoformat(), 'purpose': 'Washington official local-rule PDFs observed on three saved official court indexes', 'candidate_unique_urls': len(candidates), 'selected_unique_urls': len(seeds), 'selected_county_associations': len({r['jurisdiction']['geoid'] for r in seeds} - {None}), 'selected_county_assigned_pdfs': sum(r['jurisdiction']['geoid'] is not None for r in seeds), 'selected_county_unassigned_pdfs': sum(r['jurisdiction']['geoid'] is None for r in seeds), 'selection_status_counts': dict(Counter(r['selection_status'] for r in candidates)), 'county_authority_note': 'An explicit county in the official court label verifies only that geographic association; municipal and multi-county labels remain unassigned and jurisdiction boundaries are not inferred.', 'network_requests': 0, 'full_corpus_complete': False, 'source_scan': {'path': common.rel(source_path), 'sha256': common.sha(source_path.read_bytes())}, 'census_baseline': {'path': common.rel(baseline_path), 'sha256': common.sha(baseline_path.read_bytes())}, 'outputs': {p.name: {'sha256': common.sha(p.read_bytes()), 'bytes': p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()}}
    common.dump(out / 'summary.json', summary)
    print(json.dumps({'batch_directory': common.rel(out), **{k: v for k, v in summary.items() if k not in ('outputs', 'source_scan', 'census_baseline')}}, indent=2))


if __name__ == '__main__':
    main()
