"""Build the settlements supplement (offline, re-runnable).

Inputs (read-only):
  * the normalized third-party settlement catalog (848 publisher references)
  * packet1/corpus (collector database, receipts and raw bytes fetched 2026-09-19)
Outputs (this folder): settlements.jsonl, documents.jsonl, rejected_captures.jsonl, validation.json.

Nothing is inferred: caption parties and dollar figures are copied only when printed in the publisher title,
states are the publisher's list, the deadline state is arithmetic on the publisher's claim_deadline against
AS_OF, and the mass-tort flag is a keyword mention, never a legal characterisation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import classifier  # noqa: E402
import court_docs  # noqa: E402

CATALOG = Path('C:/Users/firas/Downloads/Court-Document-Library/07-Settlement-References/catalog/catalog.json')
PACKET = HERE / 'packet1' / 'corpus'
AS_OF = date(2026, 9, 19)
MIN_TEXT_CHARS = 200

PUBLISHER_LABEL = 'Third-party consumer settlement aggregator (SettleSignal); not a court, agency or administrator'
VERIFICATION_LABEL = 'Publisher self-assertion; not independently verified'
GOV_TYPES = {'government_refund', 'state_ag_refund', 'regulatory_compensation_program'}
CONSUMER_TYPES = {'class_action_settlement', 'other_consumer_compensation', 'privacy_settlement',
                  'financial_fee_settlement', 'consumer_product_settlement'}
FAMILY_LABELS = {'class_consumer': 'Class / consumer', 'ag_government': 'AG / government', 'data_breach': 'Data breach',
                 'mdl_mass_tort': 'MDL / mass tort (keyword)', 'other': 'Other'}

HTML_PAGE_TYPES = {'settlement_website_home', 'court_documents_index', 'faq_page', 'deadlines_page', 'press_release',
                   'executive_summary', classifier.UNCLASSIFIED}

CAPTION_PAREN =re.compile(r'\(([^()]*?\S\s+vs?\.?\s+\S[^()]*)\)')
CAPTION_V = re.compile(r'^(.*?\S)\s+vs?\.?\s+(\S.*)$')
IN_RE = re.compile(r'^\s*in re:?\s+(.+)$', re.I)
DESCRIPTOR = re.compile(r'settlement|class action|data breach|litigation|lawsuit', re.I)
AMOUNT = re.compile(r'\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|thousand|[MBK])\b)?', re.I)
SUPPRESS = re.compile(r'personal injury protection|non-?toxic', re.I)
CHALLENGE = re.compile(r'just a moment|access denied|request rate threshold|checking your browser|attention required|'
                       r'verify you are human|captcha|enable javascript and cookies', re.I)
SOFT_404 = re.compile(r'\b404\b|page (?:not|cannot be) found|no longer available|page you (?:requested|are looking for)', re.I)
PARKED = re.compile(r'domain (?:is |name is )?for sale|buy this domain|this domain (?:is|may be) (?:parked|for sale)|'
                    r'parked free|sedoparking|hugedomains', re.I)
REDIRECT_SHELL = re.compile(r'^\s*redirecting|you are being redirected', re.I)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def temporal(**values):
    block = {}
    for key in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to'):
        value, basis = values.get(key, (None, None))
        block[key] = value
        block[key + '_basis'] = basis
    return block


def caption_from_title(title: str):
    match = CAPTION_PAREN.search(title)
    where = 'parenthesised caption in title'
    segment = match.group(1).strip() if match else None
    if not segment:
        if CAPTION_V.match(title):
            segment, where = title.strip(), 'title text'
    if segment:
        parts = CAPTION_V.match(segment)
        if parts:
            plaintiff, defendant = parts.group(1).strip(), parts.group(2).strip()
            return {'as_printed': segment, 'kind': 'adversarial', 'plaintiff_side_as_printed': plaintiff,
                    'defendant_side_as_printed': defendant,
                    'side_text_may_include_descriptive_words': bool(DESCRIPTOR.search(plaintiff) or DESCRIPTOR.search(defendant)),
                    'reported_by': 'title text', 'where': where}
    inre = IN_RE.match(title)
    if inre:
        return {'as_printed': title.strip(), 'kind': 'in_re', 'matter_as_printed': inre.group(1).strip(),
                'plaintiff_side_as_printed': None, 'defendant_side_as_printed': None,
                'side_text_may_include_descriptive_words': False, 'reported_by': 'title text', 'where': 'title text'}
    return None


def amounts_from_title(title: str):
    found = []
    for match in AMOUNT.finditer(title):
        text = ' '.join(match.group(0).split())
        if text not in found:
            found.append(text)
    if not found:
        return None
    return {'as_printed': found, 'reported_by': 'title text',
            'note': 'Dollar figure(s) printed in the publisher title; not classified as fund size, per-person amount or fee; never summed.'}


def deadline_state(value):
    if not value:
        return 'unknown'
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return 'unknown'
    if day < AS_OF:
        return 'passed'
    if day <= AS_OF + timedelta(days=30):
        return 'within_30_days'
    return 'future'


def signals(title: str, where: str):
    cleaned = SUPPRESS.sub(' ', title or '')
    return [dict(item, where=where) for item in classifier.mass_tort_signals(cleaned)]


def is_gov_host(host: str) -> bool:
    host = (host or '').lower()
    return host.endswith('.gov') or host.endswith('.mil')


def family_for(publisher: dict, mass_tort: bool):
    stype = publisher.get('settlement_type') or ''
    host = urlsplit(publisher.get('official_settlement_url') or '').hostname or ''
    title = publisher.get('title') or ''
    if stype in GOV_TYPES:
        return 'ag_government', 'publisher settlement_type = %s' % stype
    if is_gov_host(host):
        return 'ag_government', 'official settlement URL host %s is a .gov host' % host
    if stype == 'data_breach_settlement' or (publisher.get('category') or '') == 'Data Breach Settlements':
        return 'data_breach', 'publisher settlement_type/category labels a data breach'
    if re.search(r'data breach', title, re.I):
        return 'data_breach', 'title text contains "data breach"'
    if mass_tort:
        return 'mdl_mass_tort', 'explicit mass-tort keyword in title or captured official page title (keyword mention only)'
    if stype in CONSUMER_TYPES:
        return 'class_consumer', 'publisher settlement_type = %s' % stype
    return 'other', 'publisher settlement_type = %s' % (stype or '(empty)')


# ----------------------------------------------------------------------------- captures
def load_captures():
    con = sqlite3.connect('file:%s?mode=ro' % (PACKET / 'corpus.sqlite3').as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    seeds = {}
    for row in con.execute('select seed_url, source_family, seed_json from contexts'):
        seeds[row['seed_url']] = {'source_family': row['source_family'], 'seed': json.loads(row['seed_json'])}
    rows = [dict(r) for r in con.execute('select * from resources order by id')]
    con.close()
    return rows, seeds


def evaluate_capture(row, seeds):
    """Return (document | None, rejection | None)."""
    base = {'url': row['url'], 'host': row['host'], 'collector_status': row['status'], 'http_status': row['last_http_status'],
            'fetch_id': row['last_fetch_id']}
    if not row['raw_path']:
        return None, dict(base, reason='not_fetched: %s' % (row['error'] or row['status']))
    raw_file = PACKET / row['raw_path']
    if not raw_file.is_file():
        return None, dict(base, reason='raw_file_missing')
    raw = raw_file.read_bytes()
    digest = sha256_bytes(raw)
    base['sha256'] = digest
    base['byte_count'] = len(raw)
    if digest != row['sha256'] or len(raw) != (row['byte_count'] or 0):
        return None, dict(base, reason='hash_or_size_mismatch_against_receipt')
    receipt = {}
    if row['metadata_path'] and (PACKET / row['metadata_path']).is_file():
        receipt = json.loads((PACKET / row['metadata_path']).read_text(encoding='utf-8'))
    if receipt.get('sha256') != digest:
        return None, dict(base, reason='receipt_missing_or_hash_differs')
    if row['status'] != 'downloaded' or row['last_http_status'] != 200 or not row['raw_complete']:
        return None, dict(base, reason='http_%s_%s' % (row['last_http_status'], row['status']))
    text = ''
    if row['text_path'] and (PACKET / row['text_path']).is_file():
        text = (PACKET / row['text_path']).read_text(encoding='utf-8', errors='replace')
    title = (row['title'] or '').strip()
    flat = ' '.join(text.split())
    head = (title + ' ' + flat[:600])
    if CHALLENGE.search(head):
        return None, dict(base, reason='challenge_or_access_denied_shell', title=title)
    if REDIRECT_SHELL.search(title) or REDIRECT_SHELL.search(flat[:200]):
        return None, dict(base, reason='redirect_shell', title=title)
    if len(flat) < MIN_TEXT_CHARS:
        return None, dict(base, reason='empty_extraction (%d text characters; script-rendered shell)' % len(flat), title=title)
    if SOFT_404.search(title) or SOFT_404.search(flat[:300]):
        return None, dict(base, reason='soft_404', title=title)
    if PARKED.search(flat[:3000]):
        return None, dict(base, reason='parked_domain', title=title)
    detected = receipt.get('detected_type') or ''
    is_html = detected == 'html'
    content_type = ((receipt.get('headers') or {}).get('content-type') or '').split(';')[0].strip().lower()
    mime = content_type or ('text/html' if is_html else 'application/octet-stream')
    if is_html:
        typed = classifier.classify(url=row['url'], first_page_text=title, is_html=True)
        if typed['type'] not in HTML_PAGE_TYPES:
            # A saved HTML page is never itself a judgment/agreement/order; a URL slug word is not evidence of one.
            overruled = {'type': typed['type'], 'type_basis': typed['type_basis']}
            if re.search(r'/press-releases?/', urlsplit(row['url']).path):
                typed = {'type': 'press_release', 'type_basis': 'url_path',
                         'type_evidence': {'matched': '/press-releases/', 'where': 'url_path', 'source_value': row['url'][:300], 'overruled': overruled}}
            else:
                typed = {'type': classifier.UNCLASSIFIED, 'type_basis': 'none',
                         'type_evidence': {'matched': None, 'where': None, 'source_value': None, 'overruled': overruled}}
    else:
        typed = classifier.classify(url=row['url'], first_page_text=text[:4000], is_html=False)
    seed = seeds.get(row['url']) or {}
    suffix = Path(row['raw_path']).suffix or ('.html' if is_html else '.bin')
    file_id = 'stlfile-' + digest[:20]
    document = {
        'file_id': file_id, 'document_id': file_id, 'record_kind': 'saved_page' if is_html else 'downloaded_underlying_file',
        'url': row['url'], 'final_url': receipt.get('final_url') or row['url'], 'host': row['host'],
        'title_as_published': title or None,
        'type': typed['type'], 'type_label': classifier.LABELS.get(typed['type'], typed['type'].replace('_', ' ')),
        'type_basis': typed['type_basis'], 'type_evidence': typed['type_evidence'],
        'mime': mime, 'detected_type': detected or None, 'filename': file_id + suffix,
        'sha256': digest, 'byte_count': len(raw), 'text_char_count': len(flat),
        'http_status': row['last_http_status'], 'fetch_id': row['last_fetch_id'],
        'raw_path': 'packet1/corpus/' + row['raw_path'], 'receipt_path': 'packet1/corpus/' + (row['metadata_path'] or ''),
        'source_family': seed.get('source_family'),
        'seed_evidence_kind': ((seed.get('seed') or {}).get('audit_evidence') or {}).get('kind'),
        'settlement_ids': [], 'link_basis': None,
        'mass_tort_signals': signals(title, 'captured_page_title'),
        'temporal': temporal(captured_at=(receipt.get('fetched_at'), 'collector receipt fetched_at (UTC)')),
    }
    return document, None


# ----------------------------------------------------------------------------- main
def build():
    catalog_bytes = CATALOG.read_bytes()
    catalog = json.loads(catalog_bytes.decode('utf-8'))
    meta = catalog.get('metadata') or {}
    feed_generated = meta.get('generated')
    records = catalog['records']

    rows, seeds = load_captures()
    documents, rejected = [], []
    for row in rows:
        document, rejection = evaluate_capture(row, seeds)
        if document:
            documents.append(document)
        else:
            rejected.append(rejection)

    by_url = {}
    for document in documents:
        by_url.setdefault(document['url'], []).append(document)

    settlements = []
    for record in records:
        publisher = record['publisher']
        title = publisher.get('title') or ''
        official = publisher.get('official_settlement_url') or None
        linked = by_url.get(official, []) if official else []
        sig = signals(title, 'publisher_title')
        for document in linked:
            for item in document['mass_tort_signals']:
                if (item['category'], item['term'].lower()) not in {(s['category'], s['term'].lower()) for s in sig}:
                    sig.append(item)
        provisional_family, _ = family_for(publisher, False)
        mass_tort = bool(sig) and provisional_family != 'data_breach'
        if sig and not mass_tort:
            mass_tort_basis = 'keyword(s) present but suppressed: the publisher labels this record a data breach'
        elif mass_tort:
            mass_tort_basis = 'keyword mention: ' + ', '.join(sorted({s['term'] for s in sig}))
        else:
            mass_tort_basis = None
        family, family_basis = family_for(publisher, mass_tort)
        for document in linked:
            document['settlement_ids'].append(record['id'])
            document['link_basis'] = 'captured URL is exactly the publisher official_settlement_url'
        review = record.get('review') or None
        review_public = None
        if review:
            review_public = {key: review.get(key) for key in ('id', 'title', 'court', 'case_number', 'jurisdiction', 'status',
                                                              'claim_deadline', 'assessment_date', 'summary')}
            review_public['limitations'] = (review.get('independent_review') or {}).get('limitations')
            review_public['label'] = 'Bounded manual review of selected fields (not a docket review)'
        deadline = publisher.get('claim_deadline') or None
        settlements.append({
            'settlement_id': record['id'], 'record_layer': 'publisher_reference',
            'title': title, 'publisher': publisher, 'publisher_label': PUBLISHER_LABEL,
            'publisher_record_sha256': record.get('publisher_record_sha256'),
            'source_snapshot_sha256': record.get('source_snapshot_sha256'),
            'verification': {'label': VERIFICATION_LABEL, 'publisher_verification_status': publisher.get('verification_status'),
                             'publisher_accepted_official_evidence': publisher.get('accepted_official_evidence'),
                             'publisher_last_verified': publisher.get('last_verified'), 'independent': False},
            'family': family, 'family_label': FAMILY_LABELS[family], 'family_basis': family_basis,
            'caption': caption_from_title(title), 'amount': amounts_from_title(title),
            'states': list(publisher.get('applicable_states') or []),
            'states_basis': 'publisher applicable_states as published; an empty list means the publisher listed none, not a class definition',
            'claim_deadline': deadline, 'claim_deadline_basis': 'publisher-reported date (no time, zone or cut-off method)' if deadline else None,
            'deadline_state': deadline_state(deadline), 'deadline_state_as_of': AS_OF.isoformat(),
            'publisher_status': publisher.get('status'),
            'mass_tort': mass_tort, 'mass_tort_basis': mass_tort_basis, 'mass_tort_signals': sig,
            'official_url': official, 'official_host': urlsplit(official or '').hostname,
            'document_ids': [d['file_id'] for d in linked],
            'document_types': sorted({d['type'] for d in linked}),
            'review': review_public,
            'temporal': temporal(source_as_of=(feed_generated, "publisher feed 'generated' timestamp")),
        })

    # Passed captures whose URL is not a catalog official URL become clearly labelled page references.
    for document in documents:
        if document['settlement_ids']:
            continue
        title = document['title_as_published'] or document['url']
        sig = list(document['mass_tort_signals'])
        if is_gov_host(document['host']):
            family, basis = 'ag_government', 'captured page host %s is a .gov host' % document['host']
        elif re.search(r'data breach', title, re.I):
            family, basis = 'data_breach', 'captured page title contains "data breach"'
        elif sig:
            family, basis = 'mdl_mass_tort', 'explicit mass-tort keyword in captured page title (keyword mention only)'
        else:
            family, basis = 'other', 'no publisher type and no explicit keyword in the captured page title'
        mass_tort = bool(sig) and family != 'data_breach'
        sid = 'settlement-page-' + document['sha256'][:20]
        document['settlement_ids'].append(sid)
        document['link_basis'] = 'page reference created from this capture (no catalog record with this official URL)'
        settlements.append({
            'settlement_id': sid, 'record_layer': 'captured_official_page',
            'title': title, 'publisher': None,
            'publisher_label': 'No publisher record: this row is a saved page of a program, court or agency site',
            'publisher_record_sha256': None, 'source_snapshot_sha256': None,
            'verification': {'label': 'Saved page only; site ownership and court-approved status were not verified',
                             'publisher_verification_status': None, 'publisher_accepted_official_evidence': None,
                             'publisher_last_verified': None, 'independent': False},
            'family': family, 'family_label': FAMILY_LABELS[family], 'family_basis': basis,
            'caption': caption_from_title(title), 'amount': amounts_from_title(title),
            'states': [], 'states_basis': 'none published',
            'claim_deadline': None, 'claim_deadline_basis': None,
            'deadline_state': 'unknown', 'deadline_state_as_of': AS_OF.isoformat(), 'publisher_status': None,
            'mass_tort': mass_tort,
            'mass_tort_basis': ('keyword mention: ' + ', '.join(sorted({s['term'] for s in sig}))) if mass_tort else None,
            'mass_tort_signals': sig,
            'official_url': document['url'], 'official_host': document['host'],
            'document_ids': [document['file_id']], 'document_types': [document['type']],
            'review': None,
            'temporal': temporal(captured_at=(document['temporal']['captured_at'], 'collector receipt fetched_at (UTC)')),
        })

    # Court-side evidence for mass-tort MDLs: docket-entry references from saved CourtListener connector receipts.
    court_documents, mdl_rows, court_edges, court_inputs, court_problems, court_edges_withheld = court_docs.build(signals)
    for row in mdl_rows:
        row['deadline_state_as_of'] = AS_OF.isoformat()
    settlements.extend(mdl_rows)

    def dump(name, items):
        data = ''.join(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n' for item in items).encode('utf-8')
        (HERE / name).write_bytes(data)
        return {'path': name, 'sha256': sha256_bytes(data), 'rows': len(items)}

    data_files = [dump('settlements.jsonl', settlements), dump('documents.jsonl', documents),
                  dump('rejected_captures.jsonl', rejected), dump('court_documents.jsonl', court_documents),
                  dump('edges.jsonl', court_edges)]

    publisher_rows = [s for s in settlements if s['record_layer'] == 'publisher_reference']
    page_rows = [s for s in settlements if s['record_layer'] == 'captured_official_page']
    ids = [s['settlement_id'] for s in settlements]
    file_ids = [d['file_id'] for d in documents]
    checks = [
        {'name': 'publisher_reference_rows_equal_catalog_records', 'passed': len(publisher_rows) == len(records) == 848,
         'detail': '%d rows / %d catalog records' % (len(publisher_rows), len(records))},
        {'name': 'settlement_ids_unique', 'passed': len(ids) == len(set(ids)), 'detail': str(len(ids))},
        {'name': 'file_ids_unique', 'passed': len(file_ids) == len(set(file_ids)), 'detail': str(len(file_ids))},
        {'name': 'publisher_fields_verbatim', 'passed': all(s['publisher'] == r['publisher'] for s, r in zip(publisher_rows, records)),
         'detail': 'publisher dict equals catalog publisher dict for every row'},
        {'name': 'every_document_rehashed_against_receipt', 'passed': all(len(d['sha256']) == 64 for d in documents),
         'detail': '%d passed captures re-hashed from raw bytes at build time' % len(documents)},
        {'name': 'every_document_linked', 'passed': all(d['settlement_ids'] for d in documents), 'detail': 'exact URL or page reference'},
        {'name': 'captures_accounted_for', 'passed': len(documents) + len(rejected) == len(rows),
         'detail': '%d passed + %d rejected/not fetched = %d collector resources' % (len(documents), len(rejected), len(rows))},
        {'name': 'deadline_state_matches_deadline', 'passed': all((s['deadline_state'] == 'unknown') == (not s['claim_deadline']) for s in settlements),
         'detail': 'unknown only when no publisher claim_deadline'},
        {'name': 'court_receipts_match_verified_master_dockets', 'passed': not court_problems,
         'detail': '; '.join(court_problems) or 'every receipt docket id equals the JPML-layer master docket id and every result belongs to it'},
        {'name': 'court_document_ids_unique', 'passed': len({d['court_document_id'] for d in court_documents}) == len(court_documents),
         'detail': str(len(court_documents))},
        {'name': 'court_documents_complete_and_not_downloaded',
         'passed': all(d['date_filed'] and d['description_as_recorded'] and d['courtlistener_url'].startswith('https://www.courtlistener.com/')
                       and d['documents_downloaded'] is False and len(d['receipt_sha256']) == 64 for d in court_documents),
         'detail': 'date filed, description as recorded, CourtListener URL and receipt hash on every row; no PACER/RECAP document fetched'},
        {'name': 'mdl_rows_are_labelled_docket_evidence',
         'passed': all(r['family'] == court_docs.FAMILY and r['amount'] is None and r['mdl_ref'] == '#mdl/%d' % r['mdl_number']
                       and r['title'] == court_docs.row_title(r['mdl_number'], r['settlement_specific_entries'])
                       and 'settlement activity' not in r['title'].lower()
                       and set(r['court_document_ids']) == {d['court_document_id'] for d in court_documents if d['mdl_number'] == r['mdl_number']}
                       for r in mdl_rows),
         'detail': '%d MDL rows titled "Settlement-phrase docket search for MDL <n>"; none carries an amount, caption, deadline or saved document' % len(mdl_rows)},
        {'name': 'settlement_specific_counts_match_docket_entries',
         'passed': all(r['entries_total'] == len(r['court_document_ids'])
                       and r['settlement_specific_entries'] == sum(1 for d in court_documents if d['mdl_number'] == r['mdl_number'] and d['settlement_specific'])
                       and all(d['settlement_specific'] == bool(d['matched_settlement_specific_terms']) for d in court_documents)
                       for r in mdl_rows),
         'detail': 'per-row "entries matching a settlement-specific phrase: N of M" recomputed from court_documents.jsonl'},
        {'name': 'no_settlement_claim_without_settlement_specific_entry',
         'passed': (all((r['settlement_specific_entries'] == 0) == r['title'].endswith(court_docs.NO_SPECIFIC_TITLE_SUFFIX)
                        and (r['settlement_specific_entries'] == 0) == bool(r['settlement_specific_finding']) for r in mdl_rows)
                    and {e['from']['id'] for e in court_edges} == {'settlement:' + r['settlement_id'] for r in mdl_rows if r['settlement_specific_entries'] > 0}
                    and all(e['relation'] == court_docs.EDGE_RELATION and e['evidence']['settlement_specific_entries'] > 0 for e in court_edges)
                    and all('settlement' not in d['type'] and 'settlement-administration' not in d['type_label']
                            for d in court_documents if not d['settlement_specific'])),
         'detail': 'rows without a settlement-specific entry (%s) say so in the title and finding and have no edge; %d edges emitted, relation %s' % (
             ', '.join('MDL %d' % w['mdl_number'] for w in court_edges_withheld) or 'none', len(court_edges), court_docs.EDGE_RELATION)},
    ]
    literal = sum(1 for d in court_documents if d['matched_terms'])
    counts = {
        'settlement_rows': len(settlements), 'publisher_references': len(publisher_rows), 'captured_page_references': len(page_rows),
        'family': dict(Counter(s['family'] for s in settlements)),
        'deadline_state_as_of_%s' % AS_OF.isoformat(): dict(Counter(s['deadline_state'] for s in settlements)),
        'publisher_status': dict(Counter(s['publisher_status'] or '(none)' for s in settlements)),
        'mass_tort_keyword_rows': sum(1 for s in settlements if s['mass_tort']),
        'rows_with_caption_in_title': sum(1 for s in settlements if s['caption']),
        'rows_with_dollar_figure_in_title': sum(1 for s in settlements if s['amount']),
        'rows_with_published_states': sum(1 for s in settlements if s['states']),
        'rows_with_saved_documents': sum(1 for s in settlements if s['document_ids']),
        'publisher_rows_with_saved_documents': sum(1 for s in publisher_rows if s['document_ids']),
        'reviewed_rows': sum(1 for s in settlements if s['review']),
        'collector_resources': len(rows), 'documents_passed': len(documents), 'captures_rejected_or_not_fetched': len(rejected),
        'rejection_reasons': dict(Counter(re.sub(r'[:(].*$', '', r['reason']).strip() for r in rejected)),
        'document_types': dict(Counter(d['type'] for d in documents)),
        'document_bytes': sum(d['byte_count'] for d in documents),
        'mdl_court_docket_rows': len(mdl_rows),
        'court_docket_entries': len(court_documents),
        'court_docket_entries_by_mdl': {str(r['mdl_number']): len(r['court_document_ids']) for r in mdl_rows},
        'court_search_matches_reported_by_mdl': {str(r['mdl_number']): sum(s['matches_reported_by_search'] or 0 for s in r['court_search']['searches']) for r in mdl_rows},
        'court_searches_incomplete_first_page_only': sorted(str(r['mdl_number']) for r in mdl_rows if not all(s['complete'] for s in r['court_search']['searches'])),
        'court_docket_entry_types': dict(Counter(d['type'] for d in court_documents)),
        'court_docket_entries_main_document_in_recap': sum(1 for d in court_documents if d['is_available'] is True),
        'court_docket_entries_with_literal_search_term': literal,
        'court_docket_entries_with_settlement_specific_phrase': sum(1 for d in court_documents if d['settlement_specific']),
        'court_docket_entries_non_specific_phrase_only': sum(1 for d in court_documents if not d['settlement_specific']),
        'settlement_specific_entries_by_mdl': {str(r['mdl_number']): '%d of %d' % (r['settlement_specific_entries'], r['entries_total']) for r in mdl_rows},
        'mdl_rows_without_settlement_specific_entry': sorted(str(r['mdl_number']) for r in mdl_rows if not r['settlement_specific_entries']),
        'edges': len(court_edges),
        'edges_withheld_no_settlement_specific_entry': court_edges_withheld,
        'court_connector_receipts': len([i for i in court_inputs if i['path'].startswith('receipts_court/')]),
    }
    passed = all(check['passed'] for check in checks)
    validation = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': passed,
        'validated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'data_files': data_files, 'counts': counts, 'checks': checks,
        'qualification': (
            'Discovery layer, not a mass-tort settlement corpus: 848 references come from one third-party consumer aggregator '
            '(SettleSignal feed generated %s); status, deadline and verification fields are the publisher\'s own assertions. '
            'Deadline states are arithmetic against %s. %d official administrator/government pages were saved on 2026-09-19; '
            'a saved page is not a settlement agreement or court order. Licence conflict unresolved: the feed says free use with '
            'attribution, while the publisher\'s general terms (June 3, 2026) restrict commercial use and bulk republication - '
            'internal research use only until a person decides. Separately, %d rows titled "Settlement-phrase docket search for MDL <n>" '
            'list %d docket entries found by a CourtListener (RECAP) phrase search of verified MDL master dockets on 2026-09-19: '
            'docket-entry evidence only, not settlement records or amounts; no court document was opened or downloaded, and for some '
            'MDLs only the first result page was captured. %d of those entries matched a settlement-specific phrase; for MDL %s no '
            'captured entry did (only "common benefit" / "order approving"), so those rows say "no settlement-specific entry found" '
            'and are not evidence of settlement activity.' % (
                feed_generated, AS_OF.isoformat(), len(documents), len(mdl_rows), len(court_documents),
                sum(1 for d in court_documents if d['settlement_specific']),
                ' and '.join(str(r['mdl_number']) for r in mdl_rows if not r['settlement_specific_entries']) or '(none)')),
        'license_ref': 'Feed metadata: "%s" CONFLICTS with the publisher general terms dated 2026-06-03 (commercial use and bulk '
                       'republication restricted); see Court-Document-Library/07-Settlement-References FINDINGS.md / INTEGRATION.md. '
                       'Saved official pages remain the property of their site operators; U.S. government pages are public.' % (meta.get('license') or ''),
        'inputs': [{'path': CATALOG.as_posix(), 'sha256': sha256_bytes(catalog_bytes)},
                   {'path': 'packet1/seeds.jsonl', 'sha256': sha256_bytes((HERE / 'packet1' / 'seeds.jsonl').read_bytes())},
                   {'path': 'classifier.py', 'sha256': sha256_bytes((HERE / 'classifier.py').read_bytes())}] + court_inputs,
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return validation


if __name__ == '__main__':
    result = build()
    print(json.dumps({'status': result['status'], 'ready': result['ready'], 'counts': result['counts'],
                      'failed_checks': [c for c in result['checks'] if not c['passed']]}, indent=1))
