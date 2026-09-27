"""Settlement-phrase docket search of mass-tort MDL master dockets (offline; reads receipts_court/*.json only).

Evidence layer, not a settlement record: each row is one docket entry of an MDL master docket whose description
(as recorded by CourtListener/RECAP) matched a fixed list of phrases in a CourtListener search. Eight phrases are
settlement-specific; "common benefit" and "order approving" are not, and an MDL whose captured entries matched only
those two is published as "no settlement-specific entry found" and gets no edge (MDL 2738 and 3060 on 2026-09-19).
No document was opened or downloaded, no PACER fetch was made, no amount or term is extracted.
The master docket id for every MDL comes from sources/jpml_mdl_20260919 (court id + docket number match).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import classifier

HERE = Path(__file__).resolve().parent
RECEIPTS = HERE / 'receipts_court'
JPML = HERE.parent / 'jpml_mdl_20260919' / 'mdls.jsonl'
CL = 'https://www.courtlistener.com'
FAMILY = 'mdl_court_docket'
FAMILY_LABEL = 'MDL / mass tort (court docket evidence)'
LAYER = 'mdl_court_docket_activity'
# The CourtListener query (see receipts_court/*.json) used all ten phrases. Only the first eight name a settlement step;
# "common benefit" (fee/expense assessments, time-keeping protocols) and "order approving" (any approved schedule, stipulation
# or protocol) also match entries that have nothing to do with a settlement, so they never count as settlement-specific.
SETTLEMENT_SPECIFIC_TERMS = ['settlement agreement', 'master settlement', 'preliminary approval', 'final approval',
                             'qualified settlement fund', 'claims administrator', 'notice plan', 'plan of allocation']
NON_SPECIFIC_TERMS = ['common benefit', 'order approving']
SEARCH_TERMS = SETTLEMENT_SPECIFIC_TERMS + NON_SPECIFIC_TERMS
TITLE = 'Settlement-phrase docket search for MDL %d'
NO_SPECIFIC_TITLE_SUFFIX = ': no settlement-specific entry found'
EDGE_RELATION = 'docket_phrase_search_for'
PLAIN_CMO_TYPE = 'case_management_order'
PLAIN_CMO_LABEL = 'Case-management order (no settlement-specific phrase in the description)'
TYPE_TEXT_CHARS = 220
EVIDENCE_LABEL = ('Docket-entry evidence only: descriptions as recorded by CourtListener (RECAP) for the MDL master docket. '
                  'Not a settlement record, not an amount, not a finding that a settlement exists; documents were not opened.')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_mdls():
    rows = {}
    for line in JPML.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get('cl_links') and row['cl_links'].get('docket_id'):
                rows[int(row['mdl_number'])] = row
    return rows


ORDER_LEAD = re.compile(r'^(?:(?:MDL|LETTER|SEALED|SPECIAL MASTER|CONSENT|AGREED|STIPULATED)\s+)*'
                        r'(?:ORDER|CASE MANAGEMENT ORDER|PRETRIAL ORDER|MEMORANDUM|FINAL APPROVAL ORDER|OPINION)\b', re.I)
TEXT_ORDER_LEAD = re.compile(r'^(?:MDL\s+)?(?:SPECIAL MASTER\s+)?TEXT ORDER\b', re.I)
MOTION_LEAD = re.compile(r'^(?:(?:Joint|Consent|Unopposed|Agreed|Emergency|SEALED|Amended)\s+)*MOTION\b', re.I)
PROCEDURAL = re.compile(r'\bleave to file\b|\bextension of time\b|\bunder seal\b|\bamend|\bwithdraw|\breconsider', re.I)
FIRST_SENTENCE = re.compile(r'(?<![Nn][Oo])\.\s')
ORDER_TYPES = {'preliminary_approval_order', 'final_approval_order', 'fee_order', 'case_management_or_settlement_order',
               'court_opinion', 'dismissal_order', 'final_judgment', 'consent_judgment_or_decree'}
MOTION_TYPES = {'approval_motion', 'fee_motion'}


def filing_kind(description: str) -> str:
    text = ' '.join((description or '').split())
    if TEXT_ORDER_LEAD.match(text):
        return 'text_order'
    if ORDER_LEAD.match(text):
        return 'order'
    if MOTION_LEAD.match(text):
        return 'motion'
    return 'other'


def type_from_description(description: str):
    """Existing classifier (label pass) on the first sentence of the docket description, guarded by the filing kind.

    The classifier was written for link labels, so a type is accepted only when it agrees with the kind of filing the
    description itself leads with (order / motion). Responses, notices, transcripts, minute entries, clerk deadline
    entries, text orders and procedural motions/orders (leave to file, amend, seal, withdraw, reconsider) stay unclassified.
    """
    text = ' '.join((description or '').split())
    lead = FIRST_SENTENCE.split(text[:TYPE_TEXT_CHARS], 1)[0]
    kind = filing_kind(text)
    none = {'type': classifier.UNCLASSIFIED, 'type_basis': 'none', 'filing_kind': kind,
            'type_evidence': {'matched': None, 'where': None, 'source_value': None}}
    if kind in ('other', 'text_order') or PROCEDURAL.search(lead):
        return none
    typed = classifier.classify(link_text=lead)
    chosen, matched = typed['type'], (typed.get('type_evidence') or {}).get('matched')
    allowed = ORDER_TYPES if kind == 'order' else MOTION_TYPES
    if chosen not in allowed:
        chosen = None
        if kind == 'order':
            hit = re.search(r'preliminar(?:y|ily) approv\w*', lead, re.I)
            if hit:
                chosen, matched = 'preliminary_approval_order', hit.group(0)
            else:
                hit = re.search(r'final approval', lead, re.I)
                if hit:
                    chosen, matched = 'final_approval_order', hit.group(0)
    if not chosen:
        return none
    return {'type': chosen, 'type_basis': 'docket_entry_description', 'filing_kind': kind,
            'type_evidence': {'matched': matched, 'where': 'first sentence of the docket entry description (at most %d characters)' % TYPE_TEXT_CHARS,
                              'source_value': lead}}


def phrase_hits(description: str):
    """Return (settlement-specific phrases, non-specific phrases) literally present in the docket description."""
    lowered = (description or '').lower()
    return [t for t in SETTLEMENT_SPECIFIC_TERMS if t in lowered], [t for t in NON_SPECIFIC_TERMS if t in lowered]


def row_title(number: int, settlement_specific_entries: int) -> str:
    """Neutral row title: names the search, never asserts settlement activity; says so when no specific phrase matched."""
    return TITLE % number + ('' if settlement_specific_entries else NO_SPECIFIC_TITLE_SUFFIX)


def court_type(chosen: str, settlement_specific: bool):
    """(type, label) for a docket entry. The classifier's combined 'case-management or settlement-administration order'
    type is kept only when the description also carries a settlement-specific phrase; otherwise it is a plain CMO."""
    if chosen == 'case_management_or_settlement_order' and not settlement_specific:
        return PLAIN_CMO_TYPE, PLAIN_CMO_LABEL
    return chosen, classifier.LABELS.get(chosen, chosen)


def temporal(captured_at, basis):
    block = {}
    for key in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to'):
        block[key] = None
        block[key + '_basis'] = None
    block['captured_at'], block['captured_at_basis'] = captured_at, basis
    return block


def build(signals):
    """Return (court_documents, mdl_rows, edges, inputs, problems, withheld_edges). `signals` is build.signals (keyword mentions).

    `withheld_edges` lists MDL rows for which no edge is emitted because no captured entry matched a settlement-specific phrase."""
    mdls = load_mdls()
    documents, mdl_rows, edges, inputs, problems, withheld = [], [], [], [], [], []
    inputs.append({'path': '../jpml_mdl_20260919/mdls.jsonl', 'sha256': sha256_bytes(JPML.read_bytes())})
    by_mdl = {}
    for file in sorted(RECEIPTS.glob('search_mdl_*.json')):
        raw = file.read_bytes()
        digest = sha256_bytes(raw)
        inputs.append({'path': 'receipts_court/' + file.name, 'sha256': digest})
        receipt = json.loads(raw.decode('utf-8'))
        number = int(receipt['mdl_number'])
        mdl = mdls.get(number)
        if mdl is None or int(mdl['cl_links']['docket_id']) != int(receipt['docket_id']):
            problems.append('receipt %s: docket id is not the verified master docket of MDL %s' % (file.name, number))
            continue
        response = receipt['response']
        groups = {}
        for item in response.get('results') or []:
            if int(item.get('docket_id') or 0) != int(receipt['docket_id']):
                problems.append('receipt %s: result %s belongs to another docket' % (file.name, item.get('id')))
                continue
            key = str(item['entry_number']) if item.get('entry_number') is not None else 'rd%d' % item['id']
            groups.setdefault(key, []).append(item)
        captured_at = (receipt.get('requested_window_utc') or [None, None])[1]
        for key, items in groups.items():
            main = next((i for i in items if i.get('attachment_number') is None), None)
            lead = main or items[0]
            description = lead.get('description') or ''
            specific, non_specific = phrase_hits(description)
            typed = type_from_description(description)
            doc_type, doc_type_label = court_type(typed['type'], bool(specific))
            path = (main or {}).get('absolute_url') or ''
            doc = {
                'court_document_id': 'cldoc-%d-%s' % (receipt['docket_id'], key),
                'record_kind': 'docket_entry_reference', 'mdl_number': number, 'mdl_ref': '#mdl/%d' % number,
                'docket_id': receipt['docket_id'], 'docket_number': mdl['cl_links'].get('docket_number'),
                'court_id': mdl['cl_links'].get('court_id'),
                'entry_number': lead.get('entry_number'),
                'date_filed': lead.get('entry_date_filed'),
                'date_filed_basis': 'CourtListener entry_date_filed of the docket entry (court filing date as recorded in RECAP)',
                'description_as_recorded': description,
                'matched_terms': specific + non_specific,
                'matched_settlement_specific_terms': specific, 'matched_non_specific_terms': non_specific,
                'settlement_specific': bool(specific),
                'settlement_specific_basis': 'literal phrase in the docket entry description (document not opened)',
                'type': doc_type, 'type_label': doc_type_label,
                'type_basis': typed['type_basis'], 'type_evidence': typed['type_evidence'],
                'filing_kind': typed['filing_kind'],
                'filing_kind_basis': 'leading words of the docket entry description',
                'main_document_in_capture': main is not None,
                'is_available': main.get('is_available') if main else None,
                'availability_label': ('In the free RECAP archive when searched' if (main and main.get('is_available')) else
                                       'Not in the RECAP archive when searched (PACER only; not fetched)' if main else
                                       'Main document not on the captured result page; see attachments'),
                'courtlistener_url': (CL + path) if path else '%s/docket/%d/' % (CL, receipt['docket_id']),
                'courtlistener_url_basis': 'absolute_url of the RECAP document' if path else 'docket page (the entry has no document URL)',
                'recap_document_id': (main or {}).get('id'),
                'attachments': [{'attachment_number': i.get('attachment_number'), 'short_description': i.get('short_description'),
                                 'is_available': i.get('is_available'), 'recap_document_id': i.get('id'),
                                 'courtlistener_url': (CL + i['absolute_url']) if i.get('absolute_url') else None}
                                for i in items if i.get('attachment_number') is not None],
                'documents_downloaded': False,
                'receipt_file': 'receipts_court/' + file.name, 'receipt_sha256': digest,
                'source_label': 'CourtListener MCP search (connector response transcribed by the agent; not original HTTP bytes)',
                'temporal': temporal(captured_at, 'upper bound of the agent clock window around the connector call (UTC)'),
            }
            documents.append(doc)
            by_mdl.setdefault(number, {'receipts': [], 'docs': []})
            by_mdl[number]['docs'].append(doc)
        by_mdl.setdefault(number, {'receipts': [], 'docs': []})
        by_mdl[number]['receipts'].append({
            'receipt_file': 'receipts_court/' + file.name, 'receipt_sha256': digest,
            'matches_reported_by_search': response.get('count'), 'results_captured': len(response.get('results') or []),
            'complete': not response.get('has_more'), 'attachments_included_in_query': 'document_type' not in receipt['arguments']['q'],
            'query': receipt['arguments']['q'], 'captured_at': captured_at})

    for number in sorted(by_mdl):
        mdl, pack = mdls[number], by_mdl[number]
        docs = sorted(pack['docs'], key=lambda d: (d['date_filed'] or '', d['entry_number'] or 0), reverse=True)
        mdl_title = mdl.get('title') or ''
        sig = signals(mdl_title, 'jpml_mdl_title')
        sid = 'mdl-docket-%d' % number
        docket_url = '%s/docket/%d/' % (CL, mdl['cl_links']['docket_id'])
        specific_docs = [d for d in docs if d['settlement_specific']]
        complete = all(r['complete'] for r in pack['receipts'])
        specific_terms = Counter(t for d in docs for t in d['matched_settlement_specific_terms'])
        other_terms = Counter(t for d in docs if not d['settlement_specific'] for t in d['matched_non_specific_terms'])
        finding = None
        if not specific_docs:
            finding = ('No settlement-specific entry found among the %d captured docket entries (%s); they matched only the '
                       'non-specific phrase%s %s, which also match%s entries unrelated to any settlement.' % (
                           len(docs), 'complete result set' if complete else 'first result page only, older matches not captured',
                           '' if len(other_terms) == 1 else 's',
                           ', '.join('"%s" (%d)' % (t, other_terms[t]) for t in NON_SPECIFIC_TERMS if other_terms.get(t)),
                           'es' if len(other_terms) == 1 else ''))
        mdl_rows.append({
            'settlement_id': sid, 'record_layer': LAYER,
            'title': row_title(number, len(specific_docs)),
            'entries_total': len(docs), 'settlement_specific_entries': len(specific_docs),
            'non_specific_only_entries': len(docs) - len(specific_docs),
            'settlement_specific_label': 'entries matching a settlement-specific phrase: %d of %d' % (len(specific_docs), len(docs)),
            'settlement_specific_terms_matched': {t: specific_terms[t] for t in SETTLEMENT_SPECIFIC_TERMS if specific_terms.get(t)},
            'non_specific_only_terms_matched': {t: other_terms[t] for t in NON_SPECIFIC_TERMS if other_terms.get(t)},
            'settlement_specific_finding': finding,
            'search_complete': complete,
            'mdl_title': mdl_title, 'mdl_number': number, 'mdl_ref': '#mdl/%d' % number,
            'cl_docket_id': mdl['cl_links']['docket_id'], 'docket_number': mdl['cl_links'].get('docket_number'),
            'court_id': mdl['cl_links'].get('court_id'),
            'master_docket_basis': mdl['cl_links'].get('basis'),
            'evidence_label': EVIDENCE_LABEL,
            'publisher': None, 'publisher_label': 'No publisher record: CourtListener (RECAP) docket-entry search of the MDL master docket',
            'publisher_record_sha256': None, 'source_snapshot_sha256': None,
            'verification': {'label': 'Docket-entry descriptions as recorded by CourtListener; documents not opened; no settlement term or amount verified',
                             'publisher_verification_status': None, 'publisher_accepted_official_evidence': None,
                             'publisher_last_verified': None, 'independent': False},
            'family': FAMILY, 'family_label': FAMILY_LABEL,
            'family_basis': 'MDL master docket verified in the JPML layer (%s)' % (mdl['cl_links'].get('basis') or 'court id and docket number'),
            'caption': None, 'amount': None, 'states': [], 'states_basis': 'none published',
            'claim_deadline': None, 'claim_deadline_basis': None, 'deadline_state': 'unknown', 'publisher_status': None,
            'mass_tort': bool(sig),
            'mass_tort_basis': ('keyword mention in the JPML title: ' + ', '.join(sorted({s['term'] for s in sig}))) if sig else None,
            'mass_tort_signals': sig,
            'official_url': docket_url, 'official_host': 'www.courtlistener.com',
            'document_ids': [], 'document_types': [],
            'court_document_ids': [d['court_document_id'] for d in docs],
            'court_document_types': sorted({d['type'] for d in docs}),
            'court_search': {'terms': SEARCH_TERMS, 'settlement_specific_terms': SETTLEMENT_SPECIFIC_TERMS,
                             'non_specific_terms': NON_SPECIFIC_TERMS, 'searches': pack['receipts']},
            'review': None,
            'temporal': temporal(max((r['captured_at'] or '') for r in pack['receipts']) or None,
                                 'upper bound of the agent clock window around the connector call (UTC)'),
        })
        if not specific_docs:
            # No settlement-specific docket entry in the evidence: the row stays (labelled as such) but no settlement-typed edge is emitted.
            withheld.append({'settlement_id': sid, 'mdl_number': number, 'entries_total': len(docs), 'settlement_specific_entries': 0,
                             'reason': finding})
            continue
        edges.append({'from': {'type': 'settlement', 'id': 'settlement:' + sid}, 'to': {'type': 'mdl', 'id': 'mdl:%d' % number},
                      'relation': EDGE_RELATION, 'basis': 'verified_master_docket_id',
                      'evidence': {'cl_docket_id': mdl['cl_links']['docket_id'], 'docket_number': mdl['cl_links'].get('docket_number'),
                                   'entries_total': len(docs), 'settlement_specific_entries': len(specific_docs),
                                   'settlement_specific_terms_matched': {t: specific_terms[t] for t in SETTLEMENT_SPECIFIC_TERMS if specific_terms.get(t)},
                                   'search_complete': complete,
                                   'note': 'phrase search of docket entry descriptions; not a finding that a settlement exists',
                                   'receipts': [r['receipt_file'] for r in pack['receipts']]}})
    return documents, mdl_rows, edges, inputs, problems, withheld
