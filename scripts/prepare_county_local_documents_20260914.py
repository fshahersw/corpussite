"""Prepare an isolated, evidence-bound county legal-link batch; never fetch or mutate queues."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import corpus_crawler as engine

OUT = ROOT / 'sources/counties/local_documents_20260914'
COLLECTIONS = ['county_sites', 'county_entries_resume_20260913', 'county_entries_continuation_20260913', 'county_registry_continuation_20260913']
LIMIT, PER_COUNTY = 200, 3


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')


def jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows), encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rel(path):
    return path.resolve().relative_to(ROOT).as_posix()


def db_read(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def group(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def normalize(value):
    return re.sub(r'[^a-z0-9]+', ' ', unquote(value).lower()).strip()


def classify(url, anchor):
    value = normalize(anchor + ' ' + urlsplit(url).path)
    if re.search(r'\b(employment|careers?|jobs?|procurement|bid opportunities|courtyard|courthouse tour|courthouse history|news|calendar events|payment|pay online|login|log in|sign in|subscribe|donate)\b', value):
        return None
    patterns = [
        ('local_rules', r'\b(local rules|court rules|rules of court|rules of practice|standing orders|administrative orders)\b'),
        ('local_laws_codes', r'\b(ordinances?|county code|municipal code|county laws|code of ordinances)\b'),
        ('court_forms_filing_documents', r'\b(filings?|e ?filing|efile|e file|court forms|legal forms|judicial forms|court documents|legal documents|court records|case records|dockets?|court calendars?)\b'),
        ('court_clerk_office', r'\b(clerk of (the )?courts?|court clerks?|circuit clerks?|district clerks?|register of wills|clerk recorder)\b'),
        ('court_local_resources', r'\b(courts?|judicial|judiciary|probate)\b'),
    ]
    for label, pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return label, match.group(0)
    return None


def main():
    global OUT
    cli = argparse.ArgumentParser(description=__doc__)
    target = cli.add_mutually_exclusive_group()
    target.add_argument('--output-dir', help='New workspace directory; an existing preparation is never overwritten.')
    target.add_argument('--timestamped', action='store_true', help='Write a new batch beneath the initial preparation directory.')
    args = cli.parse_args()
    if args.output_dir:
        OUT = (ROOT / args.output_dir).resolve()
    elif args.timestamped:
        OUT = OUT / 'batches' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if not OUT.resolve().is_relative_to(ROOT):
        raise ValueError('Output must remain within the workspace.')
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'summary.json').exists():
        raise RuntimeError('Prepared snapshot exists; preserve its evidence rather than overwriting it.')
    baseline_path = ROOT / 'sources/official_courts/datasets/counties_50_plus_dc.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    counties = {row['geoid']: row for row in baseline}
    assert len(counties) == 3144
    previously_targeted_counties = set()
    for local_name in ('county_local_documents_20260914', 'county_local_rules_washington_20260914'):
        local_db = ROOT / 'corpus' / local_name / 'corpus.sqlite3'
        if local_db.is_file():
            with closing(db_read(local_db)) as db:
                for native in db.execute('SELECT jurisdiction_json FROM contexts'):
                    geoid = json.loads(native['jurisdiction_json']).get('geoid')
                    if geoid in counties:
                        previously_targeted_counties.add(geoid)
    parents, source_inputs, candidates = {}, [], {}
    ledger_parents, ledger_unverified = defaultdict(set), defaultdict(set)
    rejected_parent = Counter()
    link_observations = 0

    input_collections = COLLECTIONS + (['county_local_documents_20260914'] if (ROOT / 'corpus/county_local_documents_20260914/corpus.sqlite3').is_file() else [])
    for name in input_collections:
        collection = ROOT / 'corpus' / name
        dbpath = collection / 'corpus.sqlite3'
        with closing(db_read(dbpath)) as db:
            contexts = {r['id']: json.loads(r['seed_json']) for r in db.execute('SELECT id,seed_json FROM contexts')}
            associations = defaultdict(list)
            for row in db.execute('SELECT resource_id,context_id FROM resource_contexts'):
                associations[row['resource_id']].append((row['context_id'], contexts[row['context_id']]))
            links = [dict(r) for r in db.execute('SELECT l.*,f.raw_path parent_raw_path,f.sha256 parent_sha256,f.raw_complete parent_raw_complete,f.http_status parent_http_status,f.metadata_path parent_metadata_path,r.url parent_url,r.status parent_current_status FROM links l JOIN fetches f ON f.id=l.fetch_id JOIN resources r ON r.id=l.source_id WHERE l.relation=\'link\' AND l.target_url IS NOT NULL')]
            source_inputs.append({'database': rel(dbpath), 'read_only': True, 'observed_link_rows': len(links), 'context_rows': len(contexts), 'resource_rows': db.execute('SELECT count(*) FROM resources').fetchone()[0]})
        link_observations += len(links)
        for link in links:
            category = classify(link['target_url'], link['anchor_text'] or '')
            if not category:
                continue
            if not link['parent_raw_complete'] or link['parent_http_status'] != 200 or not link['parent_raw_path']:
                rejected_parent['parent_not_complete_http200'] += 1
                continue
            pkey = (name, link['fetch_id'])
            if pkey not in parents:
                rawpath = (collection / link['parent_raw_path']).resolve()
                if not rawpath.is_relative_to(ROOT) or not rawpath.is_file():
                    parents[pkey] = {'error': 'missing_parent_raw'}
                else:
                    raw = rawpath.read_bytes()
                    if sha(raw) != link['parent_sha256']:
                        parents[pkey] = {'error': 'parent_raw_sha_mismatch'}
                    else:
                        metapath = (collection / link['parent_metadata_path']).resolve()
                        meta = json.loads(metapath.read_text(encoding='utf-8'))
                        encoding = meta.get('headers', {}).get('content-encoding', '').lower()
                        decoded = gzip.decompress(raw) if encoding == 'gzip' else raw
                        if encoding not in ('', 'identity', 'gzip'):
                            parents[pkey] = {'error': 'unsupported_parent_content_encoding'}
                        else:
                            html, charset = engine.decode_text(decoded, meta.get('headers', {}).get('content-type', ''))
                            parser = engine.TextHTML(); parser.feed(html); parser.close()
                            base = engine.canonical_url(parser.base_href, link['parent_url']) if parser.base_href else link['parent_url']
                            observed = {(e['href'], ' '.join(e['text'].split())[:2000], engine.canonical_url(e['href'], base or link['parent_url'])) for e in parser.links if e['relation'] == 'link'}
                            parents[pkey] = {'raw_path': rel(rawpath), 'raw_sha256': sha(raw), 'metadata_path': rel(metapath), 'metadata_sha256': sha(metapath.read_bytes()), 'charset': charset, 'observed': observed, 'base_url': base or link['parent_url'], 'page_title': parser.title, 'page_text': parser.text}
            parent = parents[pkey]
            if parent.get('error'):
                rejected_parent[parent['error']] += 1
                continue
            evidence_tuple = (link['raw_href'], link['anchor_text'] or '', link['target_url'])
            if evidence_tuple not in parent['observed']:
                rejected_parent['link_not_reproduced_from_raw_html'] += 1
                continue
            for context_id, seed in associations[link['source_id']]:
                jurisdiction = seed.get('jurisdiction', {})
                fips = jurisdiction.get('geoid')
                strength = seed.get('source_association_strength') or 'trellis_county_name_matched_to_census_site_authority_unverified'
                hint = seed.get('candidate_county_association')
                if fips not in counties:
                    hints = {h.get('candidate_geoid') for h in seed.get('unreviewed_registry_county_hints', []) if h.get('candidate_geoid') in counties}
                    rr = seed.get('registry_record') or {}
                    if rr.get('candidate_geoid') in counties:
                        hints.add(rr['candidate_geoid'])
                    if len(hints) != 1:
                        rejected_parent['missing_or_ambiguous_county_association'] += 1
                        continue
                    fips = next(iter(hints))
                    strength = 'cisa_registry_candidate_county_unverified'
                    hint = {'candidate_geoid': fips, 'assessment': 'Unverified county association from saved registry hint; no verified court jurisdiction or site ownership claim.'}
                county = counties[fips]
                ledger_parents[fips].add(parent['raw_path'])
                ledger_unverified[fips].add(strength)
                key = (fips, link['target_url'])
                record = candidates.setdefault(key, {
                    'url': link['target_url'], 'county_geoid': fips, 'county': county['name'], 'state': county['state'], 'usps': county['usps'],
                    'category': category[0], 'matched_legal_terms': category[1], 'source_association_strength': strength,
                    'site_authority_verified': False, 'court_fips_association_verified': False, 'candidate_county_association': hint,
                    'provenance': [], 'selection_status': None,
                })
                proof = {'collection': 'corpus/' + name, 'database': rel(dbpath), 'source_resource_id': link['source_id'], 'source_fetch_id': link['fetch_id'], 'link_id': link['id'], 'source_context_id': context_id, 'parent_url': link['parent_url'], 'parent_raw_path': parent['raw_path'], 'parent_raw_sha256': parent['raw_sha256'], 'parent_metadata_path': parent['metadata_path'], 'parent_metadata_sha256': parent['metadata_sha256'], 'raw_href': link['raw_href'], 'anchor_text': link['anchor_text'], 'target_url': link['target_url'], 'observed_at': link['observed_at'], 'parent_page_title': parent['page_title'], 'link_reproduced_from_raw_html': True, 'source_family': seed.get('source_family'), 'source_association_strength': strength, 'source_seed_sha256': sha(json.dumps(seed, sort_keys=True, separators=(',', ':')).encode())}
                proof['observed_link_base_url'] = parent['base_url']
                proof['target_host_relation'] = 'same_exact_host' if urlsplit(link['target_url']).hostname == urlsplit(link['parent_url']).hostname else 'observed_www_variant_of_source_hostname' if group(link['target_url']) == group(link['parent_url']) else 'external_host'
                if proof not in record['provenance']:
                    record['provenance'].append(proof)

    # Existing queues and host controls are read only, including queues outside county collections.
    candidate_urls = {r['url'] for r in candidates.values()}
    existing, barriers = defaultdict(list), defaultdict(list)
    now = time.time()
    for dbpath in sorted((ROOT / 'corpus').glob('*/corpus.sqlite3')):
        with closing(db_read(dbpath)) as db:
            for row in db.execute('SELECT id,url,status,attempts,last_http_status,raw_path,sha256,raw_complete FROM resources'):
                if row['url'] in candidate_urls:
                    existing[row['url']].append({'database': rel(dbpath), **dict(row)})
            for row in db.execute("SELECT * FROM hosts WHERE coalesce(pause_reason,'') != '' OR cooldown_until > ?", (now,)):
                barriers[row['host'].removeprefix('www.')].append({'database': rel(dbpath), **dict(row)})
    shared = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    if shared.exists():
        with closing(db_read(shared)) as db:
            for row in db.execute("SELECT * FROM host_state WHERE coalesce(pause_reason,'') != '' OR cooldown_until > ?", (now,)):
                barriers[row['host'].removeprefix('www.')].append({'database': rel(shared), **dict(row)})
    catalog = ROOT / 'catalog/documents.sqlite3'
    with closing(db_read(catalog)) as db:
        for row in db.execute('SELECT source_url,raw_path,raw_sha256,version_id FROM latest_documents'):
            if row['source_url'] in candidate_urls:
                existing[row['source_url']].append({'database': rel(catalog), 'status': 'indexed_saved_capture', **dict(row)})
    candidate_hosts = {group(u) for u in candidate_urls}
    for control in sorted((ROOT / 'corpus').glob('*/controls/robots/*.json')):
        try:
            data = json.loads(control.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            continue
        host = group(data.get('origin', ''))
        if host in candidate_hosts and (data.get('problem') or data.get('in_progress')):
            barriers[host].append({'robots_control_path': rel(control), 'robots_control_sha256': sha(control.read_bytes()), 'origin': data.get('origin'), 'problem': data.get('problem'), 'in_progress': data.get('in_progress', False)})

    ranks = {'local_rules': 0, 'court_forms_filing_documents': 1, 'local_laws_codes': 2, 'court_clerk_office': 3, 'court_local_resources': 4}
    ordered = sorted(candidates.values(), key=lambda r: (r['county_geoid'], ranks[r['category']], r['url']))
    eligible = defaultdict(list)
    for record in ordered:
        url = record['url']; parts = urlsplit(url)
        record['prior_records'] = existing.get(url, [])
        record['preserved_barriers'] = barriers.get(group(url), [])
        exact_scope = {'host': parts.netloc.lower(), 'path_prefixes': [parts.path or '/']}
        allowed, reason = engine.Config.from_dict({'allow': [exact_scope]}).allowed(url, exact_scope)
        same_source_host = any(group(p['parent_url']) == group(url) for p in record['provenance'])
        if engine.canonical_url(url) != url or not allowed:
            record['selection_status'] = 'deferred_invalid_or_action_url_' + reason
        elif not same_source_host:
            record['selection_status'] = 'deferred_external_host_authority_review'
        elif record['prior_records']:
            record['selection_status'] = 'deferred_existing_saved_or_enqueued_url'
        elif record['preserved_barriers']:
            record['selection_status'] = 'deferred_existing_host_or_robots_barrier'
        else:
            eligible[record['county_geoid']].append(record)
    selected, selected_urls = [], set()
    # Each eligible county gets its first URL before any county gets its second.
    for round_number in range(PER_COUNTY):
        for fips in sorted(eligible, key=lambda value: (value in previously_targeted_counties, value)):
            records = eligible[fips]
            if len(records) <= round_number:
                continue
            record = records[round_number]
            if len(selected_urls) >= LIMIT:
                break
            if record['url'] in selected_urls:
                record['selection_status'] = 'deferred_shared_url_county_association_review'
                continue
            record['selection_status'] = 'selected'
            record['selection_round'] = round_number + 1
            selected.append(record); selected_urls.add(record['url'])
    for record in ordered:
        if record['selection_status'] is None:
            record['selection_status'] = 'deferred_batch_limit_or_per_county_cap'
    seeds = []
    for record in selected:
        county = counties[record['county_geoid']]
        parts = urlsplit(record['url'])
        seeds.append({
            'url': record['url'], 'source_family': 'observed_county_legal_link_from_saved_html', 'category': record['category'],
            'jurisdiction': {'country': 'US', 'state': county['state'], 'state_fips': county['state_fips'], 'county': county['name'], 'county_fips': county['county_fips'], 'geoid': county['geoid'], 'geography_vintage': county['geography_vintage'], 'association_status': record['source_association_strength'], 'court_fips_association_verified': False},
            'scope': {'host': parts.netloc.lower(), 'path_prefixes': [parts.path or '/']}, 'discovered_from': record['provenance'][0]['parent_url'],
            'source_association_strength': record['source_association_strength'], 'site_authority_verified': False, 'court_fips_association_verified': False,
            'candidate_county_association': record['candidate_county_association'], 'source_url_basis': 'exact observed HTML href resolved against saved page base URL',
            'provenance': record['provenance'], 'selection_round': record['selection_round'], 'selection_rank': len(seeds) + 1,
            'collection_scope': 'One exact observed public legal-resource or publicly linked filing/document URL plus permitted server redirects; automatic link following disabled. No form submission, account login, payment, or query/pagination enumeration. Further document links require another finite observed-link preparation.',
        })
    allow = defaultdict(set)
    for seed in seeds:
        allow[seed['scope']['host']].update(seed['scope']['path_prefixes'])
    config = {'allow': [{'host': h, 'path_prefixes': sorted(paths)} for h, paths in sorted(allow.items())], 'workers': 8, 'per_host_delay': 2.0, 'timeout_seconds': 30, 'max_transfer_seconds': 180, 'max_response_bytes': 67108864, 'max_extract_chars': 20971520, 'min_free_bytes': 10737418240, 'max_depth': 0, 'max_retries': 0, 'respect_robots': True, 'follow_links': False, 'follow_external_allowed_links': False, 'pause_host_on_access_block': True, 'user_agent': 'LegalCorpusResearch/1.0', 'shared_host_dir': 'corpus/_shared_hosts'}
    validated_config = engine.Config.from_dict(config)
    assert all(validated_config.allowed(s['url'], s['scope'])[0] for s in seeds)
    assert len(seeds) == len(selected_urls) <= LIMIT
    counts = Counter(s['jurisdiction']['geoid'] for s in seeds)
    assert max(counts.values(), default=0) <= PER_COUNTY
    by_county = defaultdict(list)
    for record in ordered:
        by_county[record['county_geoid']].append(record)
    ledger = []
    for fips, county in sorted(counties.items()):
        records = by_county[fips]
        ledger.append({'geoid': fips, 'state': county['state'], 'usps': county['usps'], 'county': county['name'], 'geography_vintage': county['geography_vintage'], 'census_geography_note': county['geography_note'], 'observed_legal_candidate_urls': len(records), 'selected_this_batch': counts[fips], 'selection_statuses': dict(Counter(r['selection_status'] for r in records)), 'qualifying_parent_captures': sorted(ledger_parents[fips]), 'source_association_strengths': sorted(ledger_unverified[fips]), 'local_corpus_complete': False, 'status': 'selected_batch_pending_acquisition' if counts[fips] else 'observed_candidates_deferred' if records else 'no_eligible_saved_legal_links_in_this_input_snapshot'})
    jsonl(OUT / 'seeds.jsonl', seeds); dump(OUT / 'config.json', config)
    existing_config_path = ROOT / 'corpus/county_local_documents_20260914/config.json'
    config_merge = None
    if existing_config_path.is_file():
        old_bytes = existing_config_path.read_bytes()
        old_config = json.loads(old_bytes)
        merged = json.loads(old_bytes)
        merged_allow = defaultdict(set)
        for rule in old_config['allow'] + config['allow']:
            merged_allow[rule['host']].update(rule['path_prefixes'])
        merged['allow'] = [{'host': h, 'path_prefixes': sorted(paths)} for h, paths in sorted(merged_allow.items())]
        merged_cfg = engine.Config.from_dict(merged)
        assert all(merged_cfg.allowed(seed['url'], seed['scope'])[0] for seed in seeds)
        (OUT / 'prior_collection_config_snapshot.json').write_bytes(old_bytes)
        dump(OUT / 'merged_collection_config.json', merged)
        config_merge = {'prior_live_config_path': rel(existing_config_path), 'prior_live_config_sha256': sha(old_bytes), 'snapshot_path': rel(OUT / 'prior_collection_config_snapshot.json'), 'merged_config_path': rel(OUT / 'merged_collection_config.json'), 'merged_config_sha256': sha((OUT / 'merged_collection_config.json').read_bytes()), 'old_allow_rules_preserved': True, 'only_allow_rules_extended': all(merged[k] == old_config[k] for k in old_config if k != 'allow'), 'live_config_modified': False}
    jsonl(OUT / 'all_candidates.jsonl', ordered); jsonl(OUT / 'deferred.jsonl', [r for r in ordered if r['selection_status'] != 'selected'])
    jsonl(OUT / 'county_ledger.jsonl', ledger)
    dump(OUT / 'validation.json', {'valid': True, 'network_requests': 0, 'legacy_queue_mutations': 0, 'baseline_counties': len(counties), 'selected_unique_urls': len(selected_urls), 'selected_counties': len(counts), 'maximum_selected_per_county': max(counts.values(), default=0), 'selected_all_reproduced_from_hash_verified_raw_html': True, 'all_selected_config_allowed': True, 'selected_no_existing_urls_or_host_barriers_in_snapshot': True, 'site_and_court_authority_claims_verified': 0, 'rejected_parent_observations': dict(rejected_parent), 'scope_limits': ['Candidate association is retained without a verified court-jurisdiction claim.', 'CISA domain registration and a county-name hint do not verify that linked material belongs to that county court.', 'Metadata records the candidate URL at preparation; live robots, redirects, barriers and downloaded content still require collection-time checks.']})
    summary = {'prepared_at_utc': datetime.now(timezone.utc).isoformat(), 'purpose': 'Isolated county-by-county local legal-document batch from exact saved links', 'source_link_observations_scanned': link_observations, 'candidate_url_county_pairs': len(ordered), 'candidate_unique_urls': len(candidate_urls), 'candidate_counties': sum(bool(rows) for rows in by_county.values()), 'selected_unique_urls': len(selected_urls), 'selected_counties': len(counts), 'selected_states': sorted({s['jurisdiction']['state'] for s in seeds}), 'selected_categories': dict(Counter(s['category'] for s in seeds)), 'selected_association_strengths': dict(Counter(s['source_association_strength'] for s in seeds)), 'selection_status_counts': dict(Counter(r['selection_status'] for r in ordered)), 'county_baseline_rows': len(ledger), 'county_cap': PER_COUNTY, 'url_limit': LIMIT, 'selection_order': 'Legal-category priority within each county; one URL per county in Census FIPS order per round, then repeat up to county cap.', 'network_requests': 0, 'full_corpus_complete': False, 'source_inputs': source_inputs, 'baseline_source': {'path': rel(baseline_path), 'sha256': sha(baseline_path.read_bytes())}, 'outputs': {p.name: {'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size} for p in sorted(OUT.iterdir()) if p.is_file()}, 'next_step': 'Root review followed by new isolated county-local-documents collection; no legacy queue overwrite.'}
    summary.update(previously_targeted_counties=len(previously_targeted_counties), selected_previously_untargeted_counties=len(set(counts) - previously_targeted_counties), selected_previously_targeted_counties=len(set(counts) & previously_targeted_counties), config_merge=config_merge, selection_order='Previously untargeted county associations first; one eligible legal-category-prioritized URL per county in Census FIPS order per round, up to per-county cap.')
    dump(OUT / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ('outputs', 'source_inputs')}, indent=2))


if __name__ == '__main__':
    main()
