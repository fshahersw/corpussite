"""Build the focused laws handoff from saved files only; never fetch or edit source data."""
from __future__ import annotations

import contextlib
from collections import Counter, defaultdict
import csv
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'delivery/focused_legal_corpus/laws'
VERSION = '1.0.5'
INPUTS = {}
REFERENCES = {}
ISSUES = []


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def relative(path):
    p = Path(str(path).replace('\\', '/'))
    p = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
    return p.relative_to(ROOT).as_posix()


def read(path):
    path = ROOT / relative(path)
    raw = path.read_bytes()
    INPUTS[relative(path)] = {'sha256': digest(raw), 'bytes': len(raw)}
    return raw


def read_json(path):
    return json.loads(read(path))


def read_jsonl(path):
    return [json.loads(x) for x in read(path).splitlines() if x.strip()]


def write_json(path, value):
    (OUT / path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_jsonl(path, rows):
    (OUT / path).write_bytes(b''.join(packed(x) + b'\n' for x in rows))


def write_pair(name, rows, fields=None):
    write_jsonl(name + '.jsonl', rows)
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / (name + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: json.dumps(row.get(k), ensure_ascii=False, separators=(',', ':'))
                        if isinstance(row.get(k), (dict, list)) else row.get(k) for k in fields})


def canonical(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def markdown_serialization(markdown, standalone_bytes=None):
    """Keep both byte identities; permit only CRLF/LF serialization differences."""
    if not isinstance(markdown, str):
        raise ValueError('Unexpected non-string provider markdown')
    embedded = markdown.encode('utf-8')
    result = {'embedded_markdown_sha256': digest(embedded), 'embedded_markdown_utf8_bytes': len(embedded),
              'standalone_available': standalone_bytes is not None}
    if standalone_bytes is not None:
        decoded = standalone_bytes.decode('utf-8')
        if decoded.replace('\r\n', '\n') != markdown.replace('\r\n', '\n'):
            raise ValueError('Catalog Markdown differs beyond CRLF/LF serialization')
        result.update(standalone_text_sha256=digest(standalone_bytes), standalone_text_bytes=len(standalone_bytes),
                      exact_bytes_equal=standalone_bytes == embedded,
                      equal_after_crlf_to_lf_normalization=True)
    return result


def ro(path):
    db = sqlite3.connect((ROOT / path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    return db


def reference(path, expected=None, byte_count=None, freshly_read=None, file_cache=None):
    """Validate paths; reuse unchanged index SHA fingerprints for already validated large files."""
    if not path:
        return None
    rel = relative(path)
    key = (rel, expected)
    if key in REFERENCES:
        return rel
    p = ROOT / rel
    result = {'path': rel, 'expected_sha256': expected, 'exists': p.is_file()}
    if not p.is_file():
        result['issue'] = 'missing_file'
    else:
        st = p.stat()
        result['bytes'] = st.st_size
        if byte_count is not None and byte_count != st.st_size:
            result['issue'] = 'byte_count_mismatch'
        cached = (file_cache or {}).get(rel)
        if expected:
            if freshly_read is not None:
                actual = digest(freshly_read)
                result.update(actual_sha256=actual, verification='fresh_sha256')
            elif cached and cached['sha256'] == expected and cached['size'] == st.st_size and cached['mtime_ns'] == st.st_mtime_ns:
                actual = expected
                result['verification'] = 'prior_index_sha256_plus_unchanged_size_mtime'
            elif INPUTS.get(rel, {}).get('sha256') == expected:
                actual = expected
                result['verification'] = 'fresh_input_sha256'
            else:
                actual = digest(p.read_bytes())
                result.update(actual_sha256=actual, verification='fresh_sha256')
            if actual != expected:
                result['issue'] = 'sha256_mismatch'
        else:
            result['verification'] = 'exists_within_workspace'
    REFERENCES[key] = result
    if result.get('issue'):
        ISSUES.append(result)
    return rel


def classify_provider(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.select_one('div.rule-header')
    if container:
        heading = container.find('h1')
        if heading:
            heading.decompose()
        for tag in container.select('script,style'):
            tag.decompose()
        body = container.get_text(' ', strip=True)
        if re.search(r'FILE\s*:\s*text/plain\s*;', body, re.I):
            return {'content_kind': 'captured_law_representation', 'body_status': 'missing_body/storage_placeholder',
                    'body_characters': 0, 'placeholder_present': True,
                    'classification_basis': 'The observed div.rule-header body contains a FILE:text/plain storage reference, not an intact legal body.'}
        if body:
            return {'content_kind': 'legal_text_fragment', 'body_status': 'observed_nonempty_legal_container',
                    'body_characters': len(body), 'body_sha256': digest(body.encode('utf-8')),
                    'body_locator': '/html :: div.rule-header excluding h1/script/style; BeautifulSoup get_text(space, strip=True)',
                    'classification_basis': 'Nonempty text in the observed legal-body container; not a correctness or complete-law certification.'}
    if soup.select_one('div.profileBillingContainer') or url.rstrip('/') == 'https://trellis.law/state-rules':
        return {'content_kind': 'legal_inventory_navigation', 'body_status': 'directory_navigation',
                'body_characters': 0, 'classification_basis': 'Observed law directory representation without a nonempty rule-header legal body.'}
    return {'content_kind': 'needs_content_review', 'body_status': 'not_established', 'body_characters': 0,
            'classification_basis': 'No recognized nonempty legal body or directory container.'}


def legal_category(url):
    parts = urlsplit(url).path.strip('/').split('/')
    family = parts[2] if len(parts) > 2 else 'root_law_directory'
    if 'constitution' in family:
        category = 'constitution'
    elif any(t in family for t in ('administrative', 'regulation')):
        category = 'administrative_rules'
    elif any(t in family for t in ('rules', 'court-acts')):
        category = 'court_rules'
    elif any(t in family for t in ('code', 'statut', 'statue', 'law', 'consolidated')):
        category = 'statutes'
    else:
        category = 'other_law_or_directory'
    return category, family


def metrics(rows):
    payloads = {c['raw_sha256']: c.get('bytes') for r in rows for c in r.get('captures', [])}
    texts = {t['text_sha256']: t.get('characters') for r in rows for t in r.get('verified_texts', [])}
    return {'downloaded_resource_urls': len({r['source_url'] for r in rows}),
            'unique_payloads': len(payloads), 'unique_payload_bytes': sum(x or 0 for x in payloads.values()),
            'urls_with_verified_nonempty_text_artifact': sum(bool(r.get('verified_texts')) for r in rows),
            'unique_verified_text_artifacts': len(texts), 'unique_verified_text_characters': sum(x or 0 for x in texts.values()),
            'content_kind_counts': dict(Counter(r['content_kind'] for r in rows)),
            'body_status_counts': dict(Counter(r['body_status'] for r in rows)),
            'whole_category_complete': False, 'edition_currency_verified': False}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'provenance').mkdir(exist_ok=True)
    started = now()
    read(__file__)
    # Reuse the existing read-only reconciliation algorithm against current saved sources,
    # redirecting every output into this owned delivery directory or memory.
    audit_path = ROOT / 'reports/laws/reconcile.py'
    read(audit_path)
    spec = importlib.util.spec_from_file_location('focused_existing_law_audit', audit_path)
    audit = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(audit)
    audit.OUT = OUT / 'provenance'
    bundle = {}
    audit.write_json = lambda name, data: bundle.__setitem__(name, data)
    audit.write_jsonl = lambda name, rows: bundle.__setitem__(name, rows)
    with contextlib.redirect_stdout(io.StringIO()):
        audit.main()
    INPUTS.update(audit.INPUTS)
    if audit.STAT_ISSUES:
        ISSUES.extend(audit.STAT_ISSUES)
    db = ro('catalog/documents.sqlite3')
    file_cache = {r['path']: dict(r) for r in db.execute('SELECT * FROM file_cache')}
    db.close()

    wy = read_json('reports/laws/gap_review_20260913/wyoming_constitution_reconciliation.json')
    dispositions = read_json('reports/laws/gap_review_20260913/category_dispositions.json')
    old_audit = read_json('reports/laws/jurisdiction_coverage.json')
    read('reports/laws/gap_review_20260913/summary.json')
    for field in ('raw_path', 'text_path', 'metadata_path'):
        reference(wy[field], wy[field.replace('_path', '_sha256')], file_cache=file_cache)
    by_edition = defaultdict(list)
    for e in bundle['edition_observations.jsonl']:
        by_edition[e['url']].append(e)
    official = []
    for source in bundle['resource_inventory.jsonl']:
        if not source['downloaded']:
            continue
        r = dict(source)
        r['source_url'] = r.pop('url')
        r['source_contexts'] = list(r['contexts'])
        r['content_kind'] = r.pop('content_role')
        r['whole_document_status'] = 'not_established'
        if r['source_url'] == wy['source_url']:
            ctx = {'jurisdiction': 'Wyoming', 'category': 'constitution'}
            if ctx not in r['contexts']:
                r['contexts'].append(ctx)
            r['content_kind'] = 'observed_comprehensive_constitution'
            r['body_status'] = 'observed_constitution_body'
            r['whole_document_status'] = 'constitution_document_observed_articles_1_through_21_currency_unverified'
            r['content_category_reconciliation'] = {'evidence_path': 'reports/laws/gap_review_20260913/wyoming_constitution_reconciliation.json',
                'original_source_category': 'statutes', 'observed_content_category': 'constitution', 'pdf_pages': wy['pdf_pages'],
                'source_metadata_preserved': True}
        elif r['content_kind'] in ('legal_inventory_navigation', 'ancillary', 'reconciled_duplicate_archive'):
            r['body_status'] = {'legal_inventory_navigation': 'directory_navigation', 'ancillary': 'ancillary_material',
                                'reconciled_duplicate_archive': 'duplicate_archive_members_already_saved'}[r['content_kind']]
        elif r['content_kind'] == 'verified_law_document_derivative':
            covered = any(d['body_or_book_coverage_verified'] for d in r['document_derivatives'])
            r['body_status'] = 'verified_parser_body_scope' if covered else 'verified_document_text_with_parser_limits'
            r['whole_document_status'] = 'parser_body_or_spine_scope_verified' if covered else 'document_extracted_with_formatting_limits'
        elif r['content_kind'] in ('law_chapter_body', 'law_text_fragment'):
            r['body_status'] = 'observed_chapter_body' if r['content_kind'] == 'law_chapter_body' else 'observed_text_fragment'
            if r['content_kind'] == 'law_chapter_body':
                followup = any(o['collection'] == 'corpus/official_law_state_rules_followup_20260914' for o in r['observations'])
                r['whole_document_status'] = 'observed_chapter_text_extent_and_currency_unverified' if followup else 'observed_full_chapter_not_whole_code'
        elif r['content_kind'] in ('historical_recodification_notice', 'historical_repeal_notice', 'historical_expiration_notice'):
            r['body_status'] = 'observed_' + r['content_kind']
            r['whole_document_status'] = 'historical_notice_observed_current_law_not_established'
        else:
            r['body_status'] = 'text_artifact_available_content_review_required' if r['verified_texts'] else 'no_verified_nonempty_text'
        ts = r['verified_texts']
        chosen = max(ts, key=lambda t: (t.get('capture_kind') == 'document_text_derivative',
                     'ocr' in t.get('capture_kind', ''), t.get('characters', 0)), default=None)
        captures = r['captures']
        chosen_raw = next((c for c in captures if chosen and c['raw_sha256'] == chosen['raw_sha256']), captures[0])
        r.update(raw_path=chosen_raw['raw_path'], raw_sha256=chosen_raw['raw_sha256'],
                 text_path=chosen['text_path'] if chosen else None, text_sha256=chosen['text_sha256'] if chosen else None,
                 text_location='whole_file' if chosen else None,
                 title=(r['labels'] or [''])[0], jurisdiction='; '.join(sorted({c['jurisdiction'] for c in r['contexts']})),
                 category='; '.join(sorted({c['category'] for c in r['contexts']})),
                 edition_observation_count=len(by_edition[r['source_url']]),
                 provenance={'source_type': 'official_source_map_and_observed_official_links',
                             'authority_limit': 'Original source attribution retained; inferred content categories are not official certification.',
                             'observations': r.pop('observations')})
        for cap in captures:
            reference(cap['raw_path'], cap['raw_sha256'], cap.get('bytes'), file_cache=file_cache)
        for t in ts:
            reference(t['text_path'], t['text_sha256'], file_cache=file_cache)
        for obs in r['provenance']['observations']:
            reference(obs.get('metadata_path'), obs.get('metadata_sha256'), file_cache=file_cache)
        official.append(r)
    official.sort(key=lambda r: r['source_url'])
    official_fields = ['source_url','jurisdiction','category','title','content_kind','body_status','whole_document_status',
        'raw_path','raw_sha256','text_path','text_sha256','text_location','edition_observation_count','contexts',
        'source_contexts','captures','verified_texts','document_derivatives','ocr_screenings','classification_basis','content_review',
        'source_entries','provenance','content_category_reconciliation','whole_jurisdiction_completeness']
    write_pair('official_resources', official, official_fields)
    write_pair('official_resource_gaps', bundle['resource_gaps.jsonl'])
    write_jsonl('edition_observations.jsonl', bundle['edition_observations.jsonl'])
    write_json('provenance/source_map.json', bundle['source_map_snapshot.json'])
    write_json('provenance/official_reconciliation.json', bundle['jurisdiction_coverage.json'])
    write_json('provenance/official_input_evidence.json', bundle['input_evidence.json'])
    write_json('provenance/wyoming_constitution_reconciliation.json', wy)

    state_codes = dict(zip('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(),
        ['Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','District of Columbia','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming']))
    db = ro('sources/trellis/worker/frontier.sqlite3')
    frontier = [dict(r) for r in db.execute("SELECT * FROM frontier WHERE category='rules' AND in_scope=1 ORDER BY url")]
    db.close()
    provider = [r for r in frontier if r['status'] == 'downloaded']
    trellis = []
    serialization_checks = []
    for f in provider:
        path = relative(f['response_path'])
        raw = read(path)
        data = json.loads(raw)
        meta = data.get('metadata', {})
        url = f['url']
        if meta.get('statusCode') != 200 or canonical(meta.get('sourceURL') or meta.get('url') or url) != canonical(url):
            ISSUES.append({'source_url': url, 'issue': 'provider_success_or_url_mismatch'})
        md = data.get('markdown', '')
        raw_sha = digest(raw)
        reference(path, raw_sha, len(raw), freshly_read=raw)
        category, family = legal_category(url)
        parts = urlsplit(url).path.strip('/').split('/')
        code = parts[1].upper() if len(parts) > 1 else None
        standalone = f'sources/trellis/catalog/extracted/{raw_sha[:2]}/{raw_sha}.md'
        if (ROOT / standalone).is_file():
            standalone_bytes = read(standalone)
            serialization = markdown_serialization(md, standalone_bytes)
            text_sha = serialization['standalone_text_sha256']
            reference(standalone, text_sha, freshly_read=standalone_bytes)
            serialization_checks.append({'source_url': url, 'provider_path': path, 'provider_sha256': raw_sha,
                                         'standalone_text_path': standalone, **serialization})
        else:
            standalone = None
            serialization = markdown_serialization(md)
            text_sha = serialization['embedded_markdown_sha256']
        row = {'source_url': url, 'jurisdiction': state_codes.get(code, 'National directory'), 'state_code': code,
            'category': category, 'observed_trellis_family': family, 'title': meta.get('title'),
            'capture_kind': 'provider_rendered_public_page', 'raw_path': path, 'raw_sha256': raw_sha, 'raw_bytes': len(raw),
            'text_path': standalone or path, 'text_sha256': text_sha,
            'text_location': 'whole_file' if standalone else '/markdown', 'standalone_text_path': standalone,
            'embedded_text_path': path, 'embedded_text_location': '/markdown',
            'embedded_text_sha256': serialization['embedded_markdown_sha256'],
            'standalone_text_serialization': 'Verified byte identity or CRLF/LF-only serialization difference; both hashes retained.' if standalone else None,
            'markdown_serialization': serialization,
            'text_characters': len(md),
            'text_hash_verified': True, 'source_http_status': meta.get('statusCode'),
            'whole_document_status': 'not_established', 'whole_jurisdiction_complete': False, 'legal_currency_verified': False,
            'provider_cache_state': meta.get('cacheState'), 'provider_metadata': meta,
            'provenance': {'source_type': 'Trellis secondary legal publisher', 'source_path': path,
                'discovered_from': f['discovered_from'], 'frontier_status': f['status'], 'attempts': f['attempts'],
                'verification': 'Fresh provider/embedded Markdown SHA256; standalone file SHA256 and CRLF/LF-only content equivalence when present.',
                'original_http_payload': False}, **classify_provider(data.get('html', ''), url)}
        trellis.append(row)
    write_json('provenance/markdown_serialization_validation.json', {'validated_at_utc': now(),
        'provider_records_checked': len(serialization_checks), 'provider_records_total': len(provider),
        'provider_records_without_standalone_text': len(provider) - len(serialization_checks),
        'exact_byte_matches': sum(c['exact_bytes_equal'] for c in serialization_checks),
        'newline_serialization_differences': sum(not c['exact_bytes_equal'] for c in serialization_checks),
        'source_files_modified': False,
        'method': 'Fresh provider/embedded/file hashes; decoded Markdown equality allowing only CRLF-to-LF normalization.',
        'checks': serialization_checks})
    browser_manifest = 'corpus/trellis_browser_laws/manifest.jsonl'
    for m in read_jsonl(browser_manifest):
        if m['status'] != 'captured':
            continue
        raw_path = 'corpus/trellis_browser_laws/' + m['raw_path']
        text_path = 'corpus/trellis_browser_laws/' + m['text_path']
        raw, text = read(raw_path), read(text_path)
        data = json.loads(raw)
        reference(raw_path, m['raw_sha256'], m['raw_bytes'], freshly_read=raw)
        reference(text_path, m['text_sha256'], m['text_bytes'], freshly_read=text)
        if data['url'] != m['source_url'] or data['legal_text'].encode('utf-8') != text:
            ISSUES.append({'source_url': m['source_url'], 'issue': 'browser_manifest_raw_text_mismatch'})
        category, family = legal_category(m['source_url'])
        body = m['content_kind'] == 'law_text'
        trellis.append({'source_url': m['source_url'], 'jurisdiction': state_codes[m['state_code']], 'state_code': m['state_code'],
            'category': category, 'observed_trellis_family': family, 'title': m['title'],
            'capture_kind': 'browser_rendered_dom', 'content_kind': 'legal_text_fragment' if body else 'legal_inventory_navigation',
            'body_status': 'observed_nonempty_legal_container' if body else 'directory_navigation',
            'body_characters': len(text.decode('utf-8')) if body else 0,
            'classification_basis': 'Previously validated selected browser DOM capture; leaf/directory role preserved.',
            'raw_path': raw_path, 'raw_sha256': m['raw_sha256'], 'raw_bytes': m['raw_bytes'],
            'text_path': text_path, 'text_sha256': m['text_sha256'], 'text_location': 'whole_file',
            'standalone_text_path': text_path, 'text_characters': len(text.decode('utf-8')), 'text_hash_verified': True,
            'source_http_status': None, 'captured_at': m['captured_at'], 'whole_document_status': 'not_established',
            'whole_jurisdiction_complete': False, 'legal_currency_verified': False,
            'provenance': {'source_type': 'Trellis secondary legal publisher via signed-in browser',
                'manifest': browser_manifest, 'dom_selector': m['dom_selector'], 'limitations': m['limitations'],
                'original_http_payload': False, 'verification': 'Fresh parent/text SHA256 plus saved DOM JSON/text equality.'}})
    trellis.sort(key=lambda r: (r['source_url'], r['capture_kind'], r['raw_path']))
    write_pair('trellis_law_captures', trellis)
    write_json('provenance/trellis_frontier_snapshot.json', {'snapshot_at': now(), 'source': 'sources/trellis/worker/frontier.sqlite3',
        'logical_rows_sha256': digest(packed(frontier)), 'status_counts': dict(Counter(r['status'] for r in frontier)),
        'rows': frontier, 'interpretation': 'Observed finite frontier only; pending rows remain uncollected in the focused package.'})
    trellis_gaps = [{'source_url': r['url'], 'category': 'state_rule', 'state_code': r['state'], 'gap': 'uncollected_observed_url',
        'saved_status': r['status'], 'attempts': r['attempts'], 'error': r['error'], 'discovered_from': r['discovered_from']}
        for r in frontier if r['status'] not in ('downloaded', 'captured_elsewhere')]
    trellis_gaps.extend({'source_url': r['source_url'], 'category': r['category'], 'state_code': r['state_code'],
        'gap': r['body_status'], 'raw_path': r['raw_path'], 'raw_sha256': r['raw_sha256']}
        for r in trellis if r['body_status'] == 'missing_body/storage_placeholder')
    write_pair('trellis_gaps', trellis_gaps)

    coverage = []
    detailed_gaps = {(r['jurisdiction'], r['category']): r for r in dispositions}
    for old in bundle['jurisdiction_coverage.json']['coverage']:
        j, c = old['jurisdiction'], old['category']
        rs = [r for r in official if {'jurisdiction': j, 'category': c} in r['contexts']]
        gaps = list(old['specific_gaps'])
        if (j, c) == ('Wyoming', 'constitution'):
            gaps = [g for g in gaps if not g.startswith(('No accepted', 'No nonempty'))]
            gaps.append('Saved title97.pdf contains the constitution, Articles 1–21. Its original statutes context is preserved; this delivery adds an observed-content association, not a new download or currency claim.')
        if (j, c) == ('Arizona', 'constitution'):
            gaps.append('The separate Trellis manifest includes partial article/section bodies, not an official whole-constitution capture.')
        coverage.append({'source_family': 'official', 'jurisdiction': j, 'category': c, **metrics(rs),
            'source_map_slots': old['source_map_slots'], 'observed_resource_urls': old['observed_resource_urls'],
            'pending_known_access_block_urls': old['pending_known_access_block_urls'],
            'unresolved_redirect_urls': old['unresolved_redirect_urls'],
            'unresolved_failure_or_exclusion_urls': old['unresolved_failure_or_exclusion_urls'],
            'expected_total_resources': None, 'specific_gaps': gaps,
            'bounded_gap_review': detailed_gaps.get((j, c)), 'source_entries': old['source_entries']})
    trellis_groups = defaultdict(list)
    for r in trellis:
        trellis_groups[r['jurisdiction'], r['category']].append(r)
    for (j, c), rs in sorted(trellis_groups.items()):
        coverage.append({'source_family': 'trellis', 'jurisdiction': j, 'category': c,
            'downloaded_resource_urls': len({r['source_url'] for r in rs}), 'saved_representations': len(rs),
            'urls_with_verified_nonempty_text_artifact': len({r['source_url'] for r in rs if r['text_characters']}),
            'observed_legal_body_representations': sum(r['body_status'] == 'observed_nonempty_legal_container' for r in rs),
            'content_kind_counts': dict(Counter(r['content_kind'] for r in rs)),
            'body_status_counts': dict(Counter(r['body_status'] for r in rs)), 'expected_total_resources': None,
            'whole_category_complete': False, 'edition_currency_verified': False,
            'specific_gaps': ['Trellis is a secondary source. Directory captures and storage placeholders do not supply legal bodies.',
                             'Saved observed URLs are a partial inventory. No complete state, code or rule collection is certified.']})
    write_pair('coverage', coverage)
    jurisdiction_rows = []
    for j in sorted(state_codes.values()):
        rs = [r for r in official if any(c['jurisdiction'] == j for c in r['contexts'])]
        ts = [r for r in trellis if r['jurisdiction'] == j]
        jurisdiction_rows.append({'jurisdiction': j, **metrics(rs),
            'trellis_saved_urls': len({r['source_url'] for r in ts}),
            'trellis_observed_body_representations': sum(r['body_status'] == 'observed_nonempty_legal_container' for r in ts),
            'trellis_storage_placeholders': sum(r['body_status'] == 'missing_body/storage_placeholder' for r in ts),
            'whole_jurisdiction_complete': False})
    write_pair('jurisdiction_coverage', jurisdiction_rows)

    official_totals = metrics(official)
    official_categories = [r for r in coverage if r['source_family'] == 'official']
    summary = {'builder': relative(__file__), 'version': VERSION, 'started_at_utc': started, 'completed_at_utc': now(),
        'scope': 'Focused package of saved official law resources and saved Trellis law representations; no additional acquisition.',
        'input_method': 'Fresh read-only original-law reconciliation using saved metadata and the explicitly reviewed law SQLite archives; retained index verification for unchanged artifacts; direct provider JSON/frontier and browser manifests for saved Trellis laws.',
        'official': {**official_totals, 'jurisdictions': 51, 'jurisdiction_category_rows': len(official_categories),
            'categories_with_no_downloads': sum(r['downloaded_resource_urls'] == 0 for r in official_categories),
            'categories_with_no_verified_nonempty_text': sum(r['urls_with_verified_nonempty_text_artifact'] == 0 for r in official_categories),
            'pending_known_access_barrier_urls': sum(r['pending_known_access_block'] for r in bundle['resource_gaps.jsonl']),
            'ocr_selected_pages_completed': bundle['jurisdiction_coverage.json']['totals_deduplicated_across_jurisdictions_and_categories']['selected_ocr_pages_completed'],
            'verified_document_derivatives': bundle['jurisdiction_coverage.json']['verified_document_derivatives_by_family']},
        'trellis': {'provider_captures': len(provider), 'browser_captures': sum(r['capture_kind'] == 'browser_rendered_dom' for r in trellis),
            'standalone_markdown_serialization_records_verified': len(serialization_checks),
            'unique_saved_urls': len({r['source_url'] for r in trellis}), 'saved_representations': len(trellis),
            'provider_body_status_counts': dict(Counter(r['body_status'] for r in trellis if r['capture_kind'] == 'provider_rendered_public_page')),
            'browser_body_status_counts': dict(Counter(r['body_status'] for r in trellis if r['capture_kind'] == 'browser_rendered_dom')),
            'observed_uncollected_urls': len([r for r in frontier if r['status'] not in ('downloaded','captured_elsewhere')]),
            'frontier_status_counts': dict(Counter(r['status'] for r in frontier)),
            'direct_law_scan_completed_at_utc': now()},
        'prior_audit_snapshot_at': old_audit['snapshot_completed_at_utc'],
        'production_index_verification_basis': bundle['jurisdiction_coverage.json']['production_index_build'],
        'full_national_corpus_claimed': False, 'network_requests': 0, 'raw_or_text_files_duplicated': 0,
        'source_queue_index_worker_changes': False, 'file_reference_validation_issues': len(ISSUES),
        'output_manifest_paths': [relative(OUT / (n + ext)) for n in ['official_resources','trellis_law_captures','coverage','jurisdiction_coverage','official_resource_gaps','trellis_gaps'] for ext in ('.jsonl','.csv')]}
    write_json('summary.json', summary)
    write_json('input_provenance.json', {'read_at_utc': now(), 'files': INPUTS,
        'official_source_snapshot': 'provenance/official_input_evidence.json',
        'trellis_source_snapshot': 'provenance/trellis_frontier_snapshot.json',
        'markdown_serialization_evidence': 'provenance/markdown_serialization_validation.json',
        'source_documents_are_inert_data': True,
        'path_base': 'All manifest file paths resolve from the SCRAPE workspace root, not from this delivery folder.'})
    write_jsonl('file_references.jsonl', sorted(REFERENCES.values(), key=lambda x: (x['path'], x.get('expected_sha256') or '')))
    # A meaningful guard for the newly discovered placeholder condition.
    fixture = classify_provider('<div class="rule-header"><h1>Rule</h1><div>FILE:text/plain;1;accounts/test/lv1_originalText</div></div>', 'https://trellis.law/state-rules/ma/rules/test')
    if fixture['body_status'] != 'missing_body/storage_placeholder' or fixture['body_characters'] != 0:
        ISSUES.append({'issue': 'storage_placeholder_fixture_failed'})
    validation = {'validated_at_utc': now(), 'references_checked': len(REFERENCES),
        'verification_methods': dict(Counter(r.get('verification') for r in REFERENCES.values())),
        'reference_issues': ISSUES, 'issue_count': len(ISSUES),
        'official_unique_source_urls': len(official) == len({r['source_url'] for r in official}),
        'trellis_unique_source_urls': len(trellis) == len({r['source_url'] for r in trellis}),
        'all_51_jurisdictions_represented': len(jurisdiction_rows) == 51,
        'storage_placeholder_fixture_passed': fixture['body_characters'] == 0,
        'markdown_serialization_records_verified': len(serialization_checks),
        'raw_text_files_copied': False, 'network_requests': 0,
        'limitations': ['Hash and file-reference validation establishes saved artifact integrity, not correctness or current legal effect.',
                       'Unchanged production-index size/mtime fingerprints reuse recorded SHA256 verification; this is not a repeat hash of the entire corpus.']}
    write_json('validation.json', validation)
    readme = f'''# Focused laws package

This offline package links **{len(official):,} saved official-law resource URLs** and **{len(trellis):,} Trellis law captures**. It packages the saved collection and its gaps; it does not represent a complete national legal corpus.

Start with `official_resources.csv` / `.jsonl`, `trellis_law_captures.csv` / `.jsonl`, and `jurisdiction_coverage.csv`. `coverage.csv` / `.jsonl` separates jurisdiction/category counts by official versus Trellis source. JSONL retains nested provenance, alternate capture/text paths, parser limitations and source contexts. Every file path is relative to **C:/Users/firas/Downloads/SCRAPE**, not this folder. Raw documents and extracted text remain in their original locations; none were copied here.

The common manifest fields are `source_url`, `jurisdiction`, `category`, `raw_path`, `raw_sha256`, `text_path`, `text_sha256`, `text_location`, `content_kind`, `body_status`, and `whole_document_status`. For provider records with `text_location=/markdown`, `text_path` points to an existing JSON response and the text hash covers its decoded Markdown string encoded as UTF-8. `whole_file` identifies ordinary existing text/Markdown files. Do not feed the whole provider JSON to a text reader without honoring `text_location`. Raw hashes always cover the original saved file. A nonempty verified text artifact may be navigation, commentary or a partial document.

Provider `embedded_text_sha256` always identifies the decoded Markdown value encoded as UTF-8. For the {len(serialization_checks):,} available standalone Markdown files, `text_sha256` identifies the actual file bytes. All were freshly hashed and compared with the provider value, allowing only CRLF-to-LF newline normalization; other content differences fail the build. Both hashes and equality evidence are in `provenance/markdown_serialization_validation.json`. The ordinary rebuild performs this verification directly and does not require the historical repair command.

Official totals cover all 50 states and DC across {len(official_categories)} source-category rows. {summary['official']['categories_with_no_downloads']} category rows have no saved download and {summary['official']['categories_with_no_verified_nonempty_text']} have no verified nonempty text. Counts include explicitly labeled navigation and ancillary material; categories preserve source attribution and can overlap. Wyoming `title97.pdf` is additionally associated with its observed constitution body (81 pages, Articles 1–21), while its original statutes provenance is retained. PDF/OCR and DOCX, Word XML and EPUB derivatives stay attached to their original resource and do not count as new downloads.

Trellis has {len(provider):,} provider captures plus {summary['trellis']['browser_captures']} selected browser DOM captures. There are {sum(r['body_status']=='observed_nonempty_legal_container' for r in trellis)} observed legal-body representations, {sum(r['body_status']=='directory_navigation' for r in trellis)} directory representations and {sum(r['body_status']=='missing_body/storage_placeholder' for r in trellis)} storage-placeholder bodies. The latter preserve a page whose legal container contains a `FILE:text/plain` reference instead of the rule body. These counts do not certify intact full codes, statutes or rule collections. The observed frontier retains {summary['trellis']['observed_uncollected_urls']:,} uncollected URLs, explicitly outside the finished focused acquisition package.

`official_resource_gaps` and `trellis_gaps` retain exact unresolved URLs, barriers and source evidence. The official queue retains 613 saved access barriers; no barriers were cleared or retried. `edition_observations.jsonl` retains source titles, generation statements, OPF edition metadata and retrieval/HTTP dates. Iowa's extracted EPUB is labeled **2022 Code of Iowa**; its MOBI remains unparsed. Arizona title pages are navigation and its saved constitution directory has an outdated compilation notice. Retrieval dates are not effective dates or current-law certification. Entire states/categories remain unenumerated and incomplete.

`summary.json`, `input_provenance.json`, `provenance/` and `validation.json` preserve source snapshots, input hashes and reference checks. The official portion refreshes the existing read-only audit algorithm against saved sources and reuses unchanged index verification. The Trellis portion reads saved provider results directly after the law batch drained, not a stale catalog count. Validation reports {len(ISSUES)} issues. No network requests, source edits, queue changes, indexing or worker controls occurred.

Rebuild only this package with `python scripts/build_focused_laws.py` from the workspace root. The script reads local files and writes this package plus no other directory. Run after any relevant acquisition is checkpointed so the snapshot is stable.
'''
    (OUT / 'README.md').write_text(readme, encoding='utf-8')
    write_json('files.sha256.json', {p.relative_to(OUT).as_posix(): {'sha256': digest(p.read_bytes()), 'bytes': p.stat().st_size}
        for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'files.sha256.json'})
    print(json.dumps({'output': relative(OUT), 'official': summary['official'], 'trellis': summary['trellis'],
                      'validation_issues': len(ISSUES)}, indent=2))
    if ISSUES:
        raise SystemExit(2)
    write_count_lineage()


def finalize_existing_text_references():
    """Finish this owned package without rerunning the official-law reconciliation."""
    rows = [json.loads(x) for x in (OUT / 'trellis_law_captures.jsonl').read_bytes().splitlines()]
    refs = [json.loads(x) for x in (OUT / 'file_references.jsonl').read_bytes().splitlines()]
    validation = json.loads((OUT / 'validation.json').read_bytes())
    summary = json.loads((OUT / 'summary.json').read_bytes())
    provenance = json.loads((OUT / 'input_provenance.json').read_bytes())
    corrected = {}
    checks = []
    for row in rows:
        if row['capture_kind'] != 'provider_rendered_public_page':
            continue
        raw = read(row['raw_path'])
        if digest(raw) != row['raw_sha256']:
            raise ValueError('Source provider bytes changed: ' + row['source_url'])
        md = json.loads(raw)['markdown']
        md_sha = markdown_serialization(md)['embedded_markdown_sha256']
        path = row.get('standalone_text_path')
        row['embedded_text_sha256'] = md_sha
        if not path:
            continue
        text = read(path)
        serialization = markdown_serialization(md, text)
        text_sha = serialization['standalone_text_sha256']
        previous = row['text_sha256']
        corrected[path] = {'old_expected': previous, 'text_sha256': text_sha, 'bytes': len(text)}
        row['text_sha256'] = text_sha
        row['standalone_text_serialization'] = 'Catalog Markdown bytes equal provider Markdown after CRLF-to-LF normalization; both byte hashes retained.'
        row['markdown_serialization'] = serialization
        checks.append({'source_url': row['source_url'], 'provider_path': row['raw_path'], 'provider_sha256': row['raw_sha256'],
            'standalone_text_path': path, **serialization})
    fixed_refs = []
    for r in refs:
        c = corrected.get(r['path'])
        if c and r.get('expected_sha256') == c['old_expected']:
            fixed_refs.append({'path': r['path'], 'expected_sha256': c['text_sha256'], 'actual_sha256': c['text_sha256'],
                'bytes': c['bytes'], 'exists': True, 'verification': 'fresh_sha256_and_provider_newline_equivalence'})
        else:
            fixed_refs.append(r)
    remaining = [r for r in validation['reference_issues'] if not (r.get('issue') == 'sha256_mismatch'
        and r.get('path') in corrected and r.get('expected_sha256') == corrected[r['path']]['old_expected']
        and r.get('actual_sha256') == corrected[r['path']]['text_sha256'])]
    if remaining:
        raise ValueError('Unresolved validation issues: ' + json.dumps(remaining[:3]))
    write_pair('trellis_law_captures', rows)
    write_jsonl('file_references.jsonl', fixed_refs)
    write_json('provenance/markdown_serialization_validation.json', {'validated_at_utc': now(),
        'provider_records_checked': len(checks), 'source_files_modified': False,
        'method': 'Fresh original/provider/text hashes; decoded Markdown comparison allowing only CRLF-to-LF normalization.',
        'checks': checks})
    validation.update(validated_at_utc=now(), reference_issues=[], issue_count=0,
        verification_methods=dict(Counter(r.get('verification') for r in fixed_refs)),
        markdown_serialization_records_verified=len(checks))
    summary.update(completed_at_utc=now(), file_reference_validation_issues=0)
    write_json('summary.json', summary)
    write_json('validation.json', validation)
    read(__file__)
    provenance['files'].update(INPUTS)
    provenance['finalized_at_utc'] = now()
    provenance['markdown_serialization_evidence'] = 'provenance/markdown_serialization_validation.json'
    write_json('input_provenance.json', provenance)
    text = (OUT / 'README.md').read_text(encoding='utf-8')
    text = re.sub(r'Validation reports \d+ issues\.', 'Validation reports 0 issues.', text)
    text += f'\nProvider `embedded_text_sha256` covers the decoded Markdown value. A standalone Markdown file can have a different byte hash solely because the existing catalog writer used Windows line endings. All {len(checks):,} standalone files were freshly hashed and compared with their provider values after only CRLF-to-LF normalization; this evidence is in `provenance/markdown_serialization_validation.json`. Original files were not changed.\n'
    (OUT / 'README.md').write_text(text, encoding='utf-8')
    write_json('files.sha256.json', {p.relative_to(OUT).as_posix(): {'sha256': digest(p.read_bytes()), 'bytes': p.stat().st_size}
        for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'files.sha256.json'})
    print(json.dumps({'finalized_at_utc': now(), 'official_urls': summary['official']['downloaded_resource_urls'],
        'trellis_captures': len(rows), 'newline_serialization_checked': len(checks), 'validation_issues': 0}))


def write_count_lineage():
    rows = [json.loads(x) for x in (OUT / 'official_resources.jsonl').read_bytes().splitlines()]
    index_path = 'catalog/documents.jsonl'
    index_raw = read(index_path)
    collections = {'official_laws', 'corpus/official_law_pages', 'corpus/official_law_recovery_pass_1', 'corpus/official_law_nebraska_chapters', 'corpus/official_law_resume_pass2_20260913', 'corpus/official_law_resume_pass3_20260913', 'corpus/official_law_resume_quick_20260914', 'corpus/official_law_state_rules_followup_20260914'}
    selected = []
    for line in index_raw.splitlines():
        r = json.loads(line)
        if r['collection'] in collections:
            selected.append({k: r[k] for k in ('source_url','collection','raw_path','raw_sha256','capture_kind')})
    grouped = defaultdict(list)
    for r in selected:
        grouped[r['source_url']].append(r)
    package_urls = {r['source_url'] for r in rows}
    omitted = sorted(set(grouped) - package_urls)
    if omitted:
        raise ValueError('Base law index URLs omitted from package: ' + json.dumps(omitted))
    extras = []
    for r in rows:
        if r['source_url'] in grouped:
            continue
        observations = r['provenance']['observations']
        repair = any(c['collection'] == 'official_laws/parser_repairs' for c in r['captures'])
        extras.append({'source_url': r['source_url'], 'jurisdiction': r['jurisdiction'], 'category': r['category'],
            'raw_path': r['raw_path'], 'raw_sha256': r['raw_sha256'], 'text_path': r['text_path'], 'text_sha256': r['text_sha256'],
            'body_status': r['body_status'], 'source_metadata_observations': observations,
            'reason': 'Separate verified offline parser-repair adapter; original error metadata retained.' if repair else
                'Preserved HTTP-200 source-entry bytes; base official_laws adapter requires exact status retrieved and excludes rendered/review-JavaScript statuses. No verified nonempty text counted.',
            'addition_kind': 'verified_parser_repair' if repair else 'source_entry_representation_without_verified_text'})
    read('scripts/build_document_index.py')
    proof = {'reconciled_at_utc': now(), 'index_manifest': index_path,
        'index_manifest_sha256': digest(index_raw), 'base_collections': sorted(collections),
        'base_capture_records': len(selected), 'base_distinct_source_urls': len(grouped),
        'base_collection_record_counts': dict(Counter(r['collection'] for r in selected)),
        'duplicate_url_excess_records': len(selected) - len(grouped),
        'overlapping_source_urls': [{'source_url': u, 'records': rs} for u, rs in sorted(grouped.items()) if len(rs) > 1],
        'package_official_source_urls': len(package_urls), 'package_additional_url_count': len(extras),
        'package_addition_kinds': dict(Counter(r['addition_kind'] for r in extras)),
        'package_additional_source_status_counts': dict(Counter(o['status'] for r in extras for o in r['source_metadata_observations'])),
        'package_only_resources': extras, 'base_urls_missing_from_package': omitted,
        'identity_rule': 'Exact recorded source URL; these source strings already match the package normalization.',
        'index_adapter_evidence': {'path': 'scripts/build_document_index.py', 'sha256': INPUTS['scripts/build_document_index.py']['sha256'],
            'method': 'Builder.laws accepts only exact verification_status == retrieved; parser_repairs is a separate adapter.'},
        'interpretation': 'Downloaded representations are not synonymous with indexed captures or verified legal bodies. No source record or index was changed.'}
    write_json('provenance/index_count_reconciliation.json', proof)
    summary = json.loads((OUT / 'summary.json').read_bytes())
    summary['count_lineage'] = {k: proof[k] for k in ('base_capture_records','base_distinct_source_urls','duplicate_url_excess_records',
        'package_official_source_urls','package_additional_url_count','package_addition_kinds')}
    summary['count_lineage']['evidence_path'] = 'provenance/index_count_reconciliation.json'
    write_json('summary.json', summary)
    prov = json.loads((OUT / 'input_provenance.json').read_bytes())
    read(__file__)
    prov['files'].update(INPUTS)
    prov['count_lineage_reconciled_at_utc'] = now()
    write_json('input_provenance.json', prov)
    text = (OUT / 'README.md').read_text(encoding='utf-8')
    marker = '\nCount lineage: '
    if marker in text:
        text = text.split(marker)[0]
    text += f'\nCount lineage: the {len(collections)} base law index collections contain {len(selected):,} capture records / {len(grouped):,} distinct URLs. {len(proof["overlapping_source_urls"]):,} overlapping URLs explain the record/URL difference. This package adds {len(extras)} distinct URLs, classified in the count-lineage evidence as verified parser repairs or preserved source-entry representations without verified nonempty text. Retained bytes with review/JavaScript/rendered status are not extra legal bodies. No base-index URL is omitted. Exact URLs, source metadata and overlap groups are in `provenance/index_count_reconciliation.json`.\n'
    (OUT / 'README.md').write_text(text, encoding='utf-8')
    write_json('files.sha256.json', {p.relative_to(OUT).as_posix(): {'sha256': digest(p.read_bytes()), 'bytes': p.stat().st_size}
        for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'files.sha256.json'})
    print(json.dumps({'count_lineage_written': True, 'base_records': len(selected), 'base_urls': len(grouped),
        'package_urls': len(rows), 'additions': len(extras), 'base_urls_omitted': len(omitted)}))


if __name__ == '__main__':
    if sys.argv[1:] == ['--finalize-existing-text-references']:
        finalize_existing_text_references()
        write_count_lineage()
    elif sys.argv[1:] == ['--write-count-lineage']:
        write_count_lineage()
    elif sys.argv[1:]:
        raise SystemExit('Usage: python scripts/build_focused_laws.py [--finalize-existing-text-references | --write-count-lineage]')
    else:
        main()
