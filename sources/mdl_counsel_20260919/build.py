"""Build the MDL counsel layer (firms / attorneys / parties) from saved CourtListener connector receipts.

Offline and re-runnable: reads receipts/ (manifest + verbatim connector responses) and the JPML MDL list
(sources/jpml_mdl_20260919/mdls.jsonl, which carries the verified CourtListener docket id per MDL).
Writes attorneys.jsonl, firms.jsonl, parties.jsonl, coverage.json, edges.jsonl, unresolved.jsonl, validation.json.

PRIVACY: e-mail addresses, telephone/fax numbers and street addresses exist ONLY in receipts/. They are never
copied into the data files. A final scan fails the build if an e-mail or phone pattern is found in a data file.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECEIPTS = HERE / 'receipts'
JPML = HERE.parent / 'jpml_mdl_20260919' / 'mdls.jsonl'
LICENSE_REF = ('CourtListener / Free Law Project API terms of service (https://www.courtlistener.com/terms/); data obtained '
               'through the user\'s own CourtListener membership connector; underlying docket data originates from PACER/RECAP '
               'public court records. Connector responses, not original court bytes.')

# CourtListener does not return role labels through this connector (get_choices has no choice map for the nested
# role field). Labels below are the published choices of CourtListener's open-source Role model
# (cl/people_db/models.py); the integer code is what the source recorded and is always kept.
ROLE_LABELS = {1: 'Attorney to be noticed', 2: 'Lead attorney', 3: 'Attorney in sealed group', 4: 'Pro hac vice',
               5: 'Self-terminated', 6: 'Terminated', 7: 'Suspended', 8: 'Inactive', 9: 'Disbarred', 10: 'Unknown'}
ROLE_LABEL_BASIS = 'label from CourtListener open-source Role choices; the connector returned only the integer code'

# Party names are published only when the docket's party set is small. Four of the six dockets list 9,664-49,273
# parties, overwhelmingly individual personal-injury plaintiffs; those names stay in receipts/ only.
PARTY_NAME_PUBLISH_LIMIT = 1000
NAME_ONLY_ID_BASIS = 'name_only_unpaired_search_index_set'  # explained in coverage.json -> id_basis_notes
ROLES_NOT_RETRIEVED = ('roles and parties represented were not retrieved: the record was requested by id with '
                       'fields=id,name,contact_raw,date_modified because a by-id request cannot restrict parties_represented to one docket')
NAME_ROW_SUPPRESSION_RULE = ('a name-only row is not emitted when an attorney record retrieved for the SAME master docket has exactly the same '
                             'name text (NFKC, whitespace collapsed, case-folded); same source and same docket, not a cross-docket or '
                             'cross-source person merge')
ATTORNEY_COUNT_BASIS = ('attorney records in attorneys.jsonl whose parsed firm line has exactly this firm_key; search-index firm names are '
                        'not linked to attorneys by the source and therefore count 0')

EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?<!\d)(?:\(?\d{3}\)?[-.\s])\d{3}[-.\s]\d{4}(?!\d)')
ADDRESSY = re.compile(r'^\s*(\d{1,6}\s+\S+|p\.?\s*o\.?\s*(box|drawer)\b)', re.I)
ADDRESS_IN_TEXT = re.compile(r'\b\d{1,6}\s+[A-Za-z0-9.\' ]+\b(street|st|avenue|ave|blvd|boulevard|road|rd|suite|ste|floor|fl|drive|dr|'
                             r'broadway|way|lane|ln|pkwy|parkway|highway|hwy)\b\.?', re.I)
CITY_STATE_ZIP = re.compile(r'^(?P<city>[A-Za-z .\'-]+),\s*(?P<state>[A-Z]{2})\s+\d{5}(?:-\d{4})?$')
NOTE_LINE = re.compile(r'^(counsel not admitted|not admitted|pro hac vice|lead attorney|attorney to be noticed|'
                       r'designation:|bar status:)', re.I)
CONTACT_LINE = re.compile(r'^(email|e-mail|fax|phone|tel|telephone)\s*:', re.I)
FIRM_RULE = ('firm_text = first non-empty line of contact_raw after skipping admission-note lines (e.g. "COUNSEL NOT ADMITTED '
             'TO ... BAR"); null when that line looks like a street address, PO box, city/state/ZIP, phone, fax or e-mail line. '
             'city/state = first line matching "City, ST 12345". No other line of contact_raw is stored.')
FIRM_KEY_RULE = ('firm_key = Unicode NFKC, whitespace collapsed, trimmed, case-folded firm text after the admission-note rule; '
                 'no punctuation removal, no fuzzy merging')

# Repair 2026-09-19 (reviewer defects): the search-index firm field also carries (a) a bar-admission note about an individual
# attorney glued to the firm text with a comma, and (b) address lines of self-represented / incarcerated litigants.
ADMISSION_NOTE = re.compile(r'\b(?:not|no)\s+(?:a\s+)?(?:ad[a-z]*m[a-z]*t[a-z]*d|member)\b|\bu[sd]{2}c\w*|^\s*mdl\s*\d+\s*$', re.I)
ADMISSION_NOTE_RULE = ('a firm text is split on commas; every segment that is a clerk note rather than firm text is removed and the remaining '
                       'segments are re-joined unchanged. A note segment contains "not admitted" / "no admitted" (any spelling of admitted), '
                       '"not (a) member", a "USDC"/"UDSC" court token (the source\'s bar-admission note about an individual attorney, e.g. '
                       '"COUNSEL NOT ADMITTED TO USDC-NJ BAR"), or is exactly "MDL <number>". A text with nothing left (only the note, or note '
                       'and firm text in one segment) is not published and is counted in unresolved.jsonl. The text as recorded is kept in source_texts.')
INMATE_OR_HASH = re.compile(r'#')
CUSTODY_WORDS = re.compile(r'\b(correction(?:al|s)?|prison|penitentiary|detention|inmate|jail|reformatory)\b', re.I)
CARE_OF = re.compile(r'^\s*c\s*/\s*o\b|\bcare of\b', re.I)
UNIT_TOKEN = re.compile(r'\b(suite|ste|floor|flr|room|rm|apt|apartment)\b\.?|\b\d+(?:st|nd|rd|th)?\s*fl\b\.?', re.I)
SEGMENT_STARTS_WITH_NUMBER = re.compile(r'^(?:\d+[A-Za-z]?|one|two|three|four|five|six|seven|eight|nine|ten)\s+[A-Za-z]', re.I)
SEGMENT_BUILDING_WORD = re.compile(r'\b(building|bldg|tower|towers|plaza|center|centre|ctr|square|house|place)\b|\b[A-Za-z]+\s+\d+\s*$', re.I)
FIRM_TEXT_FILTER_RULE = ('a firm text is not published (reason only, no text, in unresolved.jsonl) when it contains "#", a custody word '
                         '(correctional, prison, penitentiary, detention, inmate, jail, reformatory), a care-of marker (c/o), a suite / floor / '
                         'room / apartment token, a comma segment after the first that starts with a street number or spelled-out number '
                         '(e.g. ", One ... Ctr.") or names an office building (building, tower, plaza, center, square, house, place, or a '
                         'trailing "<word> <number>"), or any e-mail, telephone or street-address pattern')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def clean(text) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(text or ''))).strip()


def firm_key(text: str) -> str:
    return clean(text).casefold()


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:n]


def parse_contact(contact_raw: str):
    """Documented rule (FIRM_RULE). Returns (firm_text|None, city|None, state|None)."""
    lines = [clean(l) for l in (contact_raw or '').splitlines()]
    lines = [l for l in lines if l]
    firm = None
    for line in lines:
        if NOTE_LINE.match(line):
            continue
        if not (ADDRESSY.match(line) or CITY_STATE_ZIP.match(line) or CONTACT_LINE.match(line)
                or EMAIL.search(line) or PHONE.search(line)):
            firm = line
        break
    city = state = None
    for line in lines:
        m = CITY_STATE_ZIP.match(line)
        if m:
            city, state = clean(m.group('city')), m.group('state')
            break
    return firm, city, state


def has_private(text: str) -> str | None:
    if EMAIL.search(text):
        return 'text contains an e-mail pattern'
    if PHONE.search(text):
        return 'text contains a telephone pattern'
    if ADDRESS_IN_TEXT.search(text) or ADDRESSY.match(text):
        return 'text contains street-address-like text'
    return None


def strip_admission_note(text: str) -> str:
    """ADMISSION_NOTE_RULE. Returns the firm text without note segments ('' when nothing publishable is left)."""
    text = clean(text)
    if not has_note(text):
        return text
    kept = [seg.strip() for seg in text.split(',') if seg.strip() and not ADMISSION_NOTE.search(seg)]
    return clean(', '.join(kept))


def has_note(text: str) -> bool:
    """True when any comma segment of the text is a clerk note under ADMISSION_NOTE_RULE."""
    return any(ADMISSION_NOTE.search(seg) for seg in clean(text).split(','))


def firm_text_problem(text: str) -> str | None:
    """FIRM_TEXT_FILTER_RULE. Firm texts only (names of attorneys and parties keep using has_private)."""
    why = has_private(text)
    if why:
        return why
    if INMATE_OR_HASH.search(text):
        return 'text contains a hash sign (identifier-like, not a firm name)'
    if CUSTODY_WORDS.search(text):
        return 'text names a custodial institution (address line of a self-represented litigant, not a firm)'
    if CARE_OF.search(text):
        return 'text is a care-of address line'
    if UNIT_TOKEN.search(text):
        return 'text contains a suite/floor/room token (address fragment)'
    if any(SEGMENT_STARTS_WITH_NUMBER.match(seg.strip()) for seg in text.split(',')[1:]):
        return 'text contains a comma segment starting with a street number (address fragment)'
    if any(SEGMENT_BUILDING_WORD.search(seg) for seg in text.split(',')[1:]):
        return 'text contains a comma segment naming an office building (address fragment)'
    return None


def id_from_url(url: str) -> int | None:
    m = re.search(r'/(\d+)/?$', url or '')
    return int(m.group(1)) if m else None


def temporal(captured_at, source_as_of=None):
    return {
        'captured_at': captured_at, 'captured_at_basis': 'harness log time the connector response was received (UTC)',
        'source_as_of': source_as_of,
        'source_as_of_basis': 'CourtListener date_modified of this record' if source_as_of else None,
        'published_at': None, 'published_at_basis': None,
        'effective_from': None, 'effective_from_basis': None,
        'effective_to': None, 'effective_to_basis': None,
    }


def write_jsonl(path: Path, rows):
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def main():
    checks, unresolved = [], []
    manifest_path = RECEIPTS / 'manifest.json'
    if not manifest_path.exists():
        raise SystemExit('no receipts/manifest.json - connector responses were never saved; nothing to build')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    bad = [e['file'] for e in manifest['responses'] if sha256_file(RECEIPTS / e['file']) != e['sha256']]
    checks.append({'name': 'receipt_hashes_match_manifest', 'passed': not bad, 'detail': f"{len(manifest['responses'])} receipts, {len(bad)} mismatched"})
    if bad:
        raise SystemExit(f'receipt hash mismatch: {bad}')

    mdl_by_docket, mdl_info = {}, {}
    for line in JPML.open(encoding='utf-8'):
        if not line.strip():
            continue
        row = json.loads(line)
        docket_id = (row.get('cl_links') or {}).get('docket_id')
        if docket_id:
            mdl_by_docket[int(docket_id)] = int(row['mdl_number'])
            mdl_info[int(row['mdl_number'])] = {
                'mdl_number': int(row['mdl_number']), 'mdl_id': row['id'], 'title': row['title'],
                'cl_docket_id': int(docket_id), 'cl_court_id': row['cl_links'].get('court_id'),
                'docket_number': row['cl_links'].get('docket_number'),
                'docket_link_basis': row['cl_links'].get('basis'),
                'actions_pending': row.get('actions_pending'), 'counts_label': row.get('counts_label'),
            }

    index, att_records, party_records = {}, {}, {}
    for entry in manifest['responses']:
        if entry['is_error']:
            continue
        tool = entry['tool'].split('__')[-1]
        src = {'receipt_file': entry['file'], 'receipt_sha256': entry['sha256'], 'tool': tool,
               'arguments': entry['arguments'], 'received_utc': entry['received_utc']}
        if tool == 'search':
            data = json.loads((RECEIPTS / entry['file']).read_text(encoding='utf-8'))
            for res in data.get('results') or []:
                mdl = mdl_by_docket.get(int(res.get('docket_id') or 0))
                if mdl is None:
                    unresolved.append({'reason': 'search result docket id is not a verified JPML master docket', 'docket_id': res.get('docket_id'), 'receipt_file': entry['file']})
                    continue
                index[mdl] = {'src': src, 'result_count': data.get('count'), **{k: res.get(k) or [] for k in
                              ('attorney', 'attorney_id', 'firm', 'firm_id', 'party', 'party_id')}}
        elif tool == 'get_endpoint_item':
            data = json.loads((RECEIPTS / entry['file']).read_text(encoding='utf-8'))
            endpoint = entry['arguments'].get('endpoint_id')
            if endpoint == 'attorneys':
                att_records[int(data['id'])] = {'src': src, 'data': data}
            elif endpoint == 'parties':
                party_records[int(data['id'])] = {'src': src, 'data': data}

    # ---- parties -------------------------------------------------------------------------------------------
    parties, party_lookup = [], {}
    for pid, rec in sorted(party_records.items()):
        data = rec['data']
        dockets = {int(a['docket_id']) for a in data.get('attorneys') or [] if a.get('docket_id')}
        dockets |= {int(t['docket_id']) for t in data.get('party_types') or [] if t.get('docket_id')}
        mdls = sorted({mdl_by_docket[d] for d in dockets if d in mdl_by_docket})
        if not mdls:  # party fetched because an attorney record of that MDL docket points at it
            for aid, arec in att_records.items():
                for pr in arec['data'].get('parties_represented') or []:
                    if id_from_url(pr.get('party')) == pid and id_from_url(pr.get('docket')) in mdl_by_docket:
                        mdls = sorted(set(mdls) | {mdl_by_docket[id_from_url(pr['docket'])]})
        for mdl in mdls:
            docket_id = mdl_info[mdl]['cl_docket_id']
            types = [clean(t.get('name')) for t in data.get('party_types') or [] if int(t.get('docket_id') or 0) == docket_id and t.get('name')]
            name = clean(data.get('name'))
            row = {
                'id': f'clparty-{pid}-mdl{mdl}', 'kind': 'party', 'detail_level': 'party_record', 'cl_party_id': pid,
                'id_basis': 'CourtListener party id (source-native)', 'name': name, 'mdl_number': mdl, 'mdl_id': f'mdl:{mdl}',
                'cl_docket_id': docket_id, 'party_types': types,
                'party_type_note': None if types else 'no party type was returned for this docket in the party record',
                'attorney_links_in_party_record': len([a for a in data.get('attorneys') or [] if int(a.get('docket_id') or 0) == docket_id]),
                'temporal': temporal(rec['src']['received_utc'], data.get('date_modified')),
                'source': {k: rec['src'][k] for k in ('receipt_file', 'receipt_sha256', 'tool')},
            }
            parties.append(row)
            party_lookup[(pid, mdl)] = row

    # ---- attorneys -----------------------------------------------------------------------------------------
    attorneys = []
    for aid, rec in sorted(att_records.items()):
        data = rec['data']
        firm_text, city, state = parse_contact(data.get('contact_raw') or '')
        if firm_text and has_note(firm_text):
            firm_text = strip_admission_note(firm_text) or None
            if firm_text is None:
                unresolved.append({'reason': 'firm line rejected: only a bar-admission note, no separable firm text', 'cl_attorney_id': aid, 'receipt_file': rec['src']['receipt_file']})
        if firm_text and firm_text_problem(firm_text):
            unresolved.append({'reason': 'firm line rejected: ' + firm_text_problem(firm_text), 'cl_attorney_id': aid, 'receipt_file': rec['src']['receipt_file']})
            firm_text = None
        by_mdl = {}
        for pr in data.get('parties_represented') or []:
            mdl = mdl_by_docket.get(id_from_url(pr.get('docket')) or 0)
            if mdl is None:
                continue
            pid = id_from_url(pr.get('party'))
            prow = party_lookup.get((pid, mdl))
            code = pr.get('role')
            by_mdl.setdefault(mdl, []).append({
                'role_code': code, 'role_label': ROLE_LABELS.get(code), 'role_label_basis': ROLE_LABEL_BASIS,
                'cl_party_id': pid, 'party_name': prow['name'] if prow else None,
                'party_types': prow['party_types'] if prow else None,
                'party_basis': 'party record retrieved by id' if prow else 'party record not retrieved',
                'date_action': pr.get('date_action'),
            })
        has_roles_field = 'parties_represented' in data
        if not has_roles_field:
            # Round 3 by-id packet: requested with fields=id,name,contact_raw,date_modified (no parties_represented, which
            # get_endpoint_item cannot restrict to one docket). The MDL link is the search-index attorney_id set.
            for mdl, idx in sorted(index.items()):
                if aid in {int(i) for i in idx['attorney_id']}:
                    by_mdl[mdl] = []
            if not by_mdl:
                unresolved.append({'reason': 'attorney record without parties_represented is not listed in any master-docket search-index attorney_id set',
                                   'cl_attorney_id': aid, 'receipt_file': rec['src']['receipt_file']})
        for mdl, roles in sorted(by_mdl.items()):
            attorneys.append({
                'id': f'clatt-{aid}-mdl{mdl}', 'kind': 'attorney', 'detail_level': 'attorney_record', 'cl_attorney_id': aid,
                'id_basis': 'CourtListener attorney id (source-native)', 'name': clean(data.get('name')),
                'mdl_number': mdl, 'mdl_id': f'mdl:{mdl}', 'cl_docket_id': mdl_info[mdl]['cl_docket_id'],
                'record_scope': 'full_record_with_roles' if has_roles_field else 'id_name_firm_line_only',
                'mdl_link_basis': ('parties_represented entry of the attorney record names this master docket' if has_roles_field else
                                   f"attorney id is listed in the CourtListener search-index attorney_id set of this master docket (receipt {index[mdl]['src']['receipt_file']})"),
                'roles_note': None if has_roles_field else ROLES_NOT_RETRIEVED,
                'roles': roles, 'firm_text': firm_text, 'firm_key': firm_key(firm_text) if firm_text else None,
                'firm_rule': FIRM_RULE, 'city': city, 'state': state,
                'temporal': temporal(rec['src']['received_utc'], data.get('date_modified')),
                'source': {k: rec['src'][k] for k in ('receipt_file', 'receipt_sha256', 'tool')},
            })

    coverage = {}
    for mdl, idx in sorted(index.items()):
        docket_id = mdl_info[mdl]['cl_docket_id']
        rec_ids = {a['cl_attorney_id'] for a in attorneys if a['mdl_number'] == mdl}
        idx_att_ids = {int(i) for i in idx['attorney_id']}
        all_att_covered = bool(idx_att_ids) and idx_att_ids <= rec_ids
        name_rows = 0
        suppressed = 0
        record_names = {a['name'].casefold() for a in attorneys if a['mdl_number'] == mdl and a['detail_level'] == 'attorney_record'}
        if not all_att_covered:
            for name in sorted({clean(n) for n in idx['attorney'] if clean(n)}):
                if name.casefold() in record_names:
                    suppressed += 1
                    continue
                why = has_private(name)
                if why:
                    unresolved.append({'reason': 'attorney name rejected: ' + why, 'mdl_number': mdl, 'receipt_file': idx['src']['receipt_file']})
                    continue
                attorneys.append({
                    'id': f'clattname-mdl{mdl}-{short_hash(name)}', 'kind': 'attorney', 'detail_level': 'search_index_name_only',
                    'cl_attorney_id': None,
                    'id_basis': NAME_ONLY_ID_BASIS,
                    'name': name, 'mdl_number': mdl, 'mdl_id': f'mdl:{mdl}', 'cl_docket_id': docket_id,
                    'roles': [], 'firm_text': None, 'firm_key': None, 'city': None, 'state': None,
                    'temporal': temporal(idx['src']['received_utc']),
                    'source': {k: idx['src'][k] for k in ('receipt_file', 'receipt_sha256', 'tool')},
                })
                name_rows += 1
        prec_ids = {p['cl_party_id'] for p in parties if p['mdl_number'] == mdl}
        idx_party_ids = {int(i) for i in idx['party_id']}
        all_party_covered = bool(idx_party_ids) and idx_party_ids <= prec_ids
        party_names = sorted({clean(n) for n in idx['party'] if clean(n)})
        party_name_rows = 0
        party_names_withheld = False
        if not all_party_covered:
            if len(party_names) <= PARTY_NAME_PUBLISH_LIMIT:
                for name in party_names:
                    why = has_private(name)
                    if why:
                        unresolved.append({'reason': 'party name rejected: ' + why, 'mdl_number': mdl, 'receipt_file': idx['src']['receipt_file']})
                        continue
                    parties.append({
                        'id': f'clpartyname-mdl{mdl}-{short_hash(name)}', 'kind': 'party', 'detail_level': 'search_index_name_only',
                        'cl_party_id': None,
                        'id_basis': NAME_ONLY_ID_BASIS,
                        'name': name, 'mdl_number': mdl, 'mdl_id': f'mdl:{mdl}', 'cl_docket_id': docket_id, 'party_types': [],
                        'party_type_note': 'party type not retrieved (name-only search-index row)', 'attorney_links_in_party_record': None,
                        'temporal': temporal(idx['src']['received_utc']),
                        'source': {k: idx['src'][k] for k in ('receipt_file', 'receipt_sha256', 'tool')},
                    })
                    party_name_rows += 1
            else:
                party_names_withheld = True
        n_rec, m_att = len(rec_ids), len(idx_att_ids)
        n_roles = len({a['cl_attorney_id'] for a in attorneys if a['mdl_number'] == mdl and a.get('record_scope') == 'full_record_with_roles'})
        coverage[str(mdl)] = {
            **mdl_info[mdl],
            'captured_at': idx['src']['received_utc'],
            'attorney_ids_reported_by_search_index': m_att,
            'attorney_names_reported_by_search_index': len(idx['attorney']),
            'attorney_records_retrieved': n_rec, 'attorney_name_only_rows': name_rows,
            'attorney_records_with_roles': n_roles, 'attorney_records_id_name_firm_line_only': n_rec - n_roles,
            'attorney_name_only_rows_suppressed_as_same_name_as_a_record': suppressed,
            'attorney_coverage_label': (f'{n_rec} attorney records retrieved of {m_att} attorney ids reported by the CourtListener search index for master docket '
                                        f'{docket_id} on {idx["src"]["received_utc"][:10]} ({n_roles} with roles and parties represented, {n_rec - n_roles} with id, name and firm line only); '
                                        f'plus {name_rows} name-only rows (distinct remaining names in the search index; the source does not pair names with ids)'),
            'firm_names_reported_by_search_index': len(idx['firm']), 'firm_ids_reported_by_search_index': len(idx['firm_id']),
            'party_ids_reported_by_search_index': len(idx_party_ids), 'party_names_reported_by_search_index': len(idx['party']),
            'party_records_retrieved': len(prec_ids), 'party_name_only_rows': party_name_rows,
            'party_names_withheld': party_names_withheld,
            'party_coverage_label': (f'{len(prec_ids)} party records and {party_name_rows} name-only party rows published of {len(idx_party_ids)} party ids reported by the search index'
                                     + ('; names withheld from the published file (more than %d parties, overwhelmingly individual plaintiffs) and kept in receipts only' % PARTY_NAME_PUBLISH_LIMIT if party_names_withheld else '')),
            'scope_note': 'master (lead) MDL docket only, as held by CourtListener/RECAP; counsel appearing only in member cases are not included',
            'search_receipt': idx['src']['receipt_file'],
        }

    # ---- firms ---------------------------------------------------------------------------------------------
    firms = {}

    def firm_entry(text, source_text=None):
        key = firm_key(text)
        entry = firms.setdefault(key, {'firm_key': key, 'variants': {}, 'source_texts': set(), 'mdls': {}})
        entry['variants'][clean(text)] = entry['variants'].get(clean(text), 0) + 1
        entry['source_texts'].add(clean(source_text if source_text is not None else text))
        return entry

    firm_filter = {}  # mdl -> counters, published in coverage.json and validation.json
    for mdl, idx in sorted(index.items()):
        stats = firm_filter.setdefault(str(mdl), {'search_index_firm_texts': 0, 'published_as_recorded': 0, 'published_after_admission_note_removed': 0,
                                                 'not_published_only_admission_note': 0, 'not_published_address_or_litigant_line': 0})
        for source_text in idx['firm']:
            source_text = clean(source_text)
            if not source_text:
                continue
            stats['search_index_firm_texts'] += 1
            text = strip_admission_note(source_text)
            if not text:
                stats['not_published_only_admission_note'] += 1
                unresolved.append({'reason': 'search-index firm text rejected: only a bar-admission note, no separable firm text',
                                   'mdl_number': mdl, 'receipt_file': idx['src']['receipt_file']})
                continue
            why = firm_text_problem(text)
            if why:
                stats['not_published_address_or_litigant_line'] += 1
                unresolved.append({'reason': 'search-index firm text rejected: ' + why, 'mdl_number': mdl, 'receipt_file': idx['src']['receipt_file']})
                continue
            stats['published_after_admission_note_removed' if text != source_text else 'published_as_recorded'] += 1
            slot = firm_entry(text, source_text)['mdls'].setdefault(mdl, {'mdl_number': mdl, 'in_search_index': False, 'attorney_count': 0, 'attorneys': []})
            slot['in_search_index'] = True
    for att in attorneys:
        if att['firm_key']:
            slot = firm_entry(att['firm_text'])['mdls'].setdefault(att['mdl_number'], {'mdl_number': att['mdl_number'], 'in_search_index': False, 'attorney_count': 0, 'attorneys': []})
            slot['attorney_count'] += 1
            slot['attorneys'].append({'attorney_row_id': att['id'], 'cl_attorney_id': att['cl_attorney_id'], 'name': att['name'],
                                      'role_labels': sorted({r['role_label'] or f"code {r['role_code']}" for r in att['roles']})})
    captured_default = max((v['src']['received_utc'] for v in index.values()), default=None)
    firm_rows = []
    for key, entry in sorted(firms.items()):
        display = sorted(entry['variants'].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        mdls = [entry['mdls'][m] for m in sorted(entry['mdls'])]
        firm_rows.append({
            'id': 'firm-' + short_hash(key, 16), 'kind': 'firm', 'firm_key': key, 'name': display,
            'name_variants': sorted(entry['variants']), 'source_texts': sorted(entry['source_texts']),
            'admission_note_source_texts': sum(1 for t in entry['source_texts'] if has_note(t)),
            'admission_note_rule': ADMISSION_NOTE_RULE if any(has_note(t) for t in entry['source_texts']) else None,
            'mdls': mdls, 'mdl_numbers': [m['mdl_number'] for m in mdls],
            'attorney_count_total': sum(m['attorney_count'] for m in mdls),
            'evidence': sorted({'search_index_firm_field' for m in mdls if m['in_search_index']} | {'attorney_contact_firm_line' for m in mdls if m['attorney_count']}),
            'temporal': temporal(captured_default),
        })

    for key, stats in firm_filter.items():
        coverage[key]['search_index_firm_text_filter'] = stats
        coverage[key]['firm_rows_published'] = sum(1 for f in firm_rows if int(key) in f['mdl_numbers'])
        coverage[key]['firm_coverage_label'] = (
            f"{coverage[key]['firm_rows_published']} firm-text rows published for this master docket; of {stats['search_index_firm_texts']} firm texts in the "
            f"CourtListener search index, {stats['published_as_recorded']} published as recorded, {stats['published_after_admission_note_removed']} published after "
            f"removing a bar-admission note, {stats['not_published_only_admission_note']} not published (only the note), "
            f"{stats['not_published_address_or_litigant_line']} not published (address or litigant line)")

    # ---- edges ---------------------------------------------------------------------------------------------
    edges = []
    for att in attorneys:
        if att['cl_attorney_id'] is None:
            continue
        edges.append({'from': {'type': 'attorney', 'id': f"attorney:cl_attorney:{att['cl_attorney_id']}"}, 'to': {'type': 'mdl', 'id': att['mdl_id']},
                      'relation': 'counsel_of_record_in_master_docket',
                      'basis': 'courtlistener_attorney_record' if att['record_scope'] == 'full_record_with_roles' else 'courtlistener_search_index_attorney_id_set',
                      'evidence': {'receipt_file': att['source']['receipt_file'], 'sha256': att['source']['receipt_sha256'], 'cl_docket_id': att['cl_docket_id'],
                                   'mdl_link_basis': att['mdl_link_basis'], 'role_codes': sorted({r['role_code'] for r in att['roles']})}})
        if att['firm_key']:
            edges.append({'from': {'type': 'attorney', 'id': f"attorney:cl_attorney:{att['cl_attorney_id']}"},
                          'to': {'type': 'firm', 'id': 'firm:' + re.sub(r'[^a-z0-9]+', '-', att['firm_key']).strip('-')},
                          'relation': 'firm_line_in_contact_block', 'basis': 'contact_raw_first_line_rule',
                          'evidence': {'receipt_file': att['source']['receipt_file'], 'sha256': att['source']['receipt_sha256'], 'firm_text': att['firm_text']}})

    write_jsonl(HERE / 'attorneys.jsonl', attorneys)
    write_jsonl(HERE / 'firms.jsonl', firm_rows)
    write_jsonl(HERE / 'parties.jsonl', parties)
    write_jsonl(HERE / 'edges.jsonl', edges)
    write_jsonl(HERE / 'unresolved.jsonl', unresolved)
    (HERE / 'coverage.json').write_text(json.dumps({'id_basis_notes': {NAME_ONLY_ID_BASIS: 'no source-native id: the CourtListener search index returns names and ids as separate unordered sets that cannot be paired; row id is mdl + sha1(name)'}, 'attorney_count_basis': ATTORNEY_COUNT_BASIS, 'name_row_suppression_rule': NAME_ROW_SUPPRESSION_RULE, 'roles_not_retrieved_note': ROLES_NOT_RETRIEVED, 'role_labels': {str(k): v for k, v in ROLE_LABELS.items()}, 'role_label_basis': ROLE_LABEL_BASIS,
                                                    'firm_rule': FIRM_RULE, 'firm_key_rule': FIRM_KEY_RULE, 'admission_note_rule': ADMISSION_NOTE_RULE,
                                                    'firm_text_filter_rule': FIRM_TEXT_FILTER_RULE, 'mdls': coverage}, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')

    data_names = ['attorneys.jsonl', 'firms.jsonl', 'parties.jsonl', 'coverage.json', 'edges.jsonl']
    leaks = []
    for name in data_names + ['unresolved.jsonl']:
        text = (HERE / name).read_text(encoding='utf-8')
        if EMAIL.search(text):
            leaks.append(name + ': e-mail pattern')
        if name != 'coverage.json' and PHONE.search(text):
            leaks.append(name + ': telephone pattern')
        if 'contact_raw":' in text:
            leaks.append(name + ': contact_raw field')
    checks.append({'name': 'no_email_phone_or_contact_raw_in_data_files', 'passed': not leaks, 'detail': '; '.join(leaks) or 'regex scan of all data files clean'})
    ids = [r['id'] for r in attorneys + firm_rows + parties]
    checks.append({'name': 'ids_unique', 'passed': len(ids) == len(set(ids)), 'detail': f'{len(ids)} ids'})
    checks.append({'name': 'every_row_has_verified_mdl', 'passed': all(r['mdl_number'] in mdl_info for r in attorneys + parties), 'detail': 'mdl_number joins to jpml_mdl_20260919 via the verified cl_docket id only'})
    checks.append({'name': 'six_mdls_covered', 'passed': len(coverage) == 6, 'detail': ', '.join(sorted(coverage))})
    rec_att = [a for a in attorneys if a['detail_level'] == 'attorney_record']
    full_att = [a for a in rec_att if a['record_scope'] == 'full_record_with_roles']
    slim_att = [a for a in rec_att if a['record_scope'] != 'full_record_with_roles']
    checks.append({'name': 'attorney_records_have_roles', 'passed': all(a['roles'] for a in full_att) and all(not a['roles'] and a['roles_note'] for a in slim_att),
                   'detail': f'{len(full_att)} full attorney records all carry roles; {len(slim_att)} id/name/firm-line records carry no roles and say so (roles_note)'})
    checks.append({'name': 'attorney_record_ids_unique_per_mdl', 'passed': len({(a['cl_attorney_id'], a['mdl_number']) for a in rec_att}) == len(rec_att),
                   'detail': 'source-native CourtListener attorney id; one row per attorney id per MDL; no name-based merging'})
    checks.append({'name': 'every_mdl_has_attorney_records', 'passed': all(v['attorney_records_retrieved'] > 0 for v in coverage.values()),
                   'detail': ', '.join(f"{k}: {v['attorney_records_retrieved']} of {v['attorney_ids_reported_by_search_index']}" for k, v in coverage.items())})
    firm_texts = [t for f in firm_rows for t in [f['name'], f['firm_key']] + f['name_variants']] + [a['firm_text'] for a in attorneys if a['firm_text']]
    noted = [t for t in firm_texts if has_note(t)]
    checks.append({'name': 'no_admission_note_in_firm_names', 'passed': not noted,
                   'detail': f'{len(firm_texts)} firm names, keys, variants and attorney firm lines scanned; {len(noted)} contain a bar-admission note'})
    flagged = [t for t in firm_texts if firm_text_problem(t)]
    checks.append({'name': 'no_litigant_address_line_or_address_fragment_in_firm_names', 'passed': not flagged,
                   'detail': f'{len(flagged)} firm texts match the firm-text filter (hash sign, custody word, c/o, suite/floor token, numbered comma segment, e-mail, telephone, street address)'})
    allowed_unresolved_keys = {'reason', 'mdl_number', 'receipt_file', 'cl_attorney_id', 'docket_id'}
    checks.append({'name': 'unresolved_rows_carry_reason_only', 'passed': all(set(u) <= allowed_unresolved_keys for u in unresolved),
                   'detail': f'{len(unresolved)} unresolved rows; keys limited to reason, mdl_number, receipt_file, cl_attorney_id, docket_id (rejected text stays in receipts/)'})
    building_words = re.compile(r'\b(building|tower|plaza|center|centre|ctr|square)\b', re.I)
    filter_totals = {k: sum(s[k] for s in firm_filter.values()) for k in next(iter(firm_filter.values()))} if firm_filter else {}
    passed = all(c['passed'] for c in checks)
    errors =[e for e in manifest['responses'] if e['is_error']]
    counted = [e for e in manifest['responses'] if e['tool'].split('__')[-1] in ('search', 'get_endpoint_item', 'call_endpoint') and not (e['is_error'] and 'Invalid arguments' in (RECEIPTS / e['file']).read_text(encoding='utf-8'))]
    validation = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': bool(passed),
        'validated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'data_files': [{'path': n, 'sha256': sha256_file(HERE / n), 'rows': (sum(1 for _ in (HERE / n).open(encoding='utf-8')) if n.endswith('.jsonl') else 1)} for n in data_names],
        'counts': {
            'mdls': len(coverage), 'attorney_rows': len(attorneys), 'attorney_records': len(rec_att),
            'attorney_records_with_roles': len(full_att), 'attorney_records_id_name_firm_line_only': len(slim_att),
            'attorney_name_only_rows': len(attorneys) - len(rec_att), 'firm_rows': len(firm_rows),
            'firms_with_linked_attorney_records': sum(1 for f in firm_rows if f['attorney_count_total']),
            'firm_rows_before_repair_20260919': 2441,
            'firm_rows_with_an_admission_note_removed_from_a_source_text': sum(1 for f in firm_rows if f['admission_note_source_texts']),
            'firm_rows_whose_name_contains_a_building_word_in_its_first_segment': sum(1 for f in firm_rows if building_words.search(f['name'])),
            'search_index_firm_text_filter': filter_totals,
            'firm_rows_per_mdl': {k: v.get('firm_rows_published', 0) for k, v in coverage.items()},
            'party_rows': len(parties), 'party_records': sum(1 for p in parties if p['detail_level'] == 'party_record'),
            'edges': len(edges), 'unresolved': len(unresolved), 'receipts': len(manifest['responses']),
            'receipts_error_responses': len(errors), 'connector_requests_sent_including_throttled': len(counted),
            'per_mdl': {k: {'attorney': v['attorney_coverage_label'], 'party': v['party_coverage_label'], 'firm': v.get('firm_coverage_label')} for k, v in coverage.items()},
        },
        'checks': checks,
        'qualification': ('PARTIAL COVERAGE. Counsel layer for 6 products-liability MDL master dockets from CourtListener (connector responses '
                          'captured 2026-09-19 UTC; not original court bytes). Only MDL 2666 has full attorney records (source-native ids, roles, parsed firm '
                          'line, parties represented). For MDLs 2738, 2846, 2873, 3060 and 2789 a by-id packet retrieved 6 attorney records each (the 6 lowest '
                          'CourtListener attorney ids of each master docket: id, name, parsed firm line, city/state; roles NOT retrieved; not a leadership roster, '
                          'not a random sample); all their other attorneys, firms and parties remain name-only sets from the CourtListener search index, because '
                          'the connector\'s paged attorneys query could not be issued from this harness (arguments are sent untyped; see README). '
                          'Master dockets only; member-case counsel are absent. Firm rows are the firm text as recorded, never fuzzy-merged, so one firm '
                          'can appear under several spellings; the only edits are rule-based: a leading or trailing bar-admission note about an individual '
                          'attorney ("COUNSEL NOT ADMITTED TO ... BAR") is removed from the firm text, and firm-field texts that are only that note, or that '
                          'are address lines or carry an address fragment (custodial institution, c/o, suite/floor, numbered or office-building segment, '
                          'hash sign), are not published (counted in counts.search_index_firm_text_filter; text stays in receipts/). The filter is '
                          'pattern-based, not a guarantee that every remaining firm text is a law firm. Party names are withheld for four MDLs with 9,664-49,273 parties. Not a leadership roster '
                          'and not a complete appearance list.'),
        'license_ref': LICENSE_REF,
        'inputs': [{'path': 'receipts/manifest.json', 'sha256': sha256_file(manifest_path)},
                   {'path': '../jpml_mdl_20260919/mdls.jsonl', 'sha256': sha256_file(JPML)}],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': validation['status'], **{k: v for k, v in validation['counts'].items() if k != 'per_mdl'}}, indent=1))
    for c in checks:
        print(('PASS ' if c['passed'] else 'FAIL ') + c['name'] + ' - ' + c['detail'])


if __name__ == '__main__':
    main()
