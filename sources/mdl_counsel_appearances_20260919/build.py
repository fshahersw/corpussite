"""Build sources/mdl_counsel_appearances_20260919 from the AWS release b2b-cdbb8d040b95c7b65cfc's
counsel-appearance tables (a different, more complete pipeline output than sources/mdl_counsel_20260919's
CourtListener search-index scrape). Deterministic, re-runnable, streaming, offline.

Inputs (read-only, never modified):
  C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc/
      attorneys.jsonl.gz (181), firms.jsonl.gz (12), counsel_appearances.jsonl.gz (878), parties.jsonl.gz (503)
  sources/mdl_docket_crosswalk_20260919/crosswalk.jsonl + edges.jsonl (slice 1's id spine; read-only)

MDL linkage: a matter_id resolves to an MDL two ways, and BOTH counts are reported (BUILD_CONTRACT):
  - "direct": crosswalk.jsonl aws_matter_id -> mdl_number (59-matter crosswalk)
  - "extended": direct, plus edges.jsonl member_of_mdl edges (from.aws_matter -> to.mdl) for member matters
    whose parent resolves to an MDL in the crosswalk.
Direct-only reaches 3 MDLs / 192 appearances / 67 parties. Extended reaches 14 MDLs / 607 appearances /
369 parties. The adapter and UI use "extended" as the primary figure and always show both.

Role normalisation: 9 raw spellings measured in counsel_appearances.jsonl.gz. Code '10' is CourtListener's
"Unknown" role integer (see sources/mdl_counsel_20260919/coverage.json role_labels) and is merged with the
literal string 'unknown'. The raw value is always kept alongside the normalised label.

Firms: kept by native firm_id (the source pipeline's own canonical slug). Never merged by name.

Parties: a conservative, name-only person/organisation heuristic. Organisations and named defendants (any
party_types entry containing "Defendant") may be listed by name. A party that is neither is a natural-person
plaintiff-side party and is counted only -- never listed by name. When unsure, the heuristic returns "not an
organisation" (i.e. count, don't list): it requires an explicit business/entity/government-body signal token
and treats any "estate of / personal representative / administrator of / individually / next of kin" wrapper
as evidence that a natural person is inside, overriding an organisation-looking token.
"""
import datetime
import gzip
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
AWS_RELEASE = Path(r'C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc')
CROSSWALK_DIR = ROOT / 'sources/mdl_docket_crosswalk_20260919'

AWS_FILES = ('attorneys.jsonl.gz', 'firms.jsonl.gz', 'counsel_appearances.jsonl.gz', 'parties.jsonl.gz',
              'matter_relationships.jsonl.gz')

ATTORNEY_NAME_NORMALIZATION_RE = re.compile(r'[^a-z]')


def normalize_attorney_name(name):
    """Lowercase, letters-only normalisation used ONLY to count distinct name spellings for the
    'attorney_records_distinct_normalized_names' metric. Never used to merge/join attorney records --
    the 181 source UUIDs are always kept separate (BUILD_CONTRACT: never join people by name)."""
    return ATTORNEY_NAME_NORMALIZATION_RE.sub('', (name or '').casefold())

# ---- role normalisation --------------------------------------------------------------------------------
ROLE_NORMALIZATION = {
    'attorney_to_be_noticed': 'Attorney to be noticed',
    'ATTORNEY TO BE NOTICED': 'Attorney to be noticed',
    'LEAD ATTORNEY': 'Lead attorney',
    'lead_attorney': 'Lead attorney',
    'PRO HAC VICE': 'Pro hac vice',
    'terminated': 'Terminated',
    '10': 'Unknown',
    'unknown': 'Unknown',
}
TERMINATED_DATE_RE = re.compile(r'^TERMINATED:\s*(\d{2}/\d{2}/\d{4})$')
ROLE_NORMALIZATION_BASIS = (
    "9 raw role spellings measured in counsel_appearances.jsonl.gz on 2026-09-19: attorney_to_be_noticed (397), "
    "ATTORNEY TO BE NOTICED (205), LEAD ATTORNEY (140), lead_attorney (64), PRO HAC VICE (43), terminated (19), "
    "'10' (8), 'TERMINATED: 06/29/2010' (1), 'unknown' (1). '10' is CourtListener's Role choice 10 ('Unknown'; "
    "see sources/mdl_counsel_20260919/coverage.json role_labels) and is merged with the literal string 'unknown'. "
    "'TERMINATED: MM/DD/YYYY' normalises to 'Terminated' with the date parsed out separately as terminated_date_raw; "
    "the other 8 spellings map onto 4 normalised labels case/format-insensitively. The raw value is always kept."
)


def normalize_role(raw):
    if raw in ROLE_NORMALIZATION:
        return ROLE_NORMALIZATION[raw], None
    m = TERMINATED_DATE_RE.match(raw or '')
    if m:
        return 'Terminated', m.group(1)
    return 'Unknown', None


# ---- party person/organisation heuristic (conservative: unsure -> count, don't list) ------------------
PERSON_WRAPPER_MARKERS = (
    'estate of', 'personal representative', 'administrator of', 'administratrix', 'guardian of',
    'next of kin', 'individually', 'surviving spouse', 'anticipated personal representative',
    'as parent', 'as mother', 'as father', 'special administrator', 'conservator of',
)
ORG_TOKENS = (
    ' inc', ' inc.', ' incorporated', ' llc', ' l.l.c', ' llp', ' l.l.p', ' corp', ' corp.', ' corporation',
    ' co.', ' company', ' ltd', ' limited', ' l.p', ' plc', ' p.c', ' n.a', ' trust', ' partners',
    ' partnership', ' association', ' assn', ' board of', ' bank', ' university', ' hospital',
    ' pharmaceutical', ' pharmaceuticals', ' healthcare', ' laborator', ' holding', ' holdings',
    ' group', ' industries', ' systems', ' solutions', ' enterprises', ' foundation', ' society',
    ' institute', ' diocese', ' district', ' municipality', 'city of ', 'county of ', 'state of ',
    'commonwealth of ', 'united states', 'department of ', ' agency', ' authority', ' committee',
    ' union', ' cooperative',
)
ORG_HEURISTIC_BASIS = (
    "Conservative name-only heuristic (no external registry): a party is treated as an organisation only when "
    "its name contains an explicit business/entity/government-body token (Inc, LLC, Corp, Company, Ltd, Trust, "
    "Partners, Association, Bank, University, Hospital, Pharmaceutical(s), Healthcare, Holdings, Foundation, "
    "'City of'/'County of'/'State of', Department of, Agency, Authority, ...), UNLESS the name also contains a "
    "wrapper phrase that indicates a natural person is inside ('estate of', 'personal representative', "
    "'administrator of', 'individually', 'next of kin', ...), in which case it is always treated as a natural "
    "person regardless of the token. Anything that matches neither rule is treated as a natural person. "
    "When unsure, the party is counted, never listed by name."
)


def is_wrapped_person(low_name):
    return any(marker in low_name for marker in PERSON_WRAPPER_MARKERS)


def is_organization(name):
    low = ' ' + (name or '').casefold() + ' '
    if is_wrapped_person(low):
        return False
    return any(token in low for token in ORG_TOKENS)


def is_named_defendant(party_types):
    return any('defendant' in (t or '').casefold() for t in (party_types or []))


def party_listable(name, party_types):
    """Returns (listable, category). Organisations and named defendants may be listed; a natural-person
    plaintiff-side party (no defendant role, not an organisation) is never listed -- counted only."""
    org = is_organization(name)
    defendant = is_named_defendant(party_types)
    if org:
        return True, 'organization'
    if defendant:
        return True, 'named_defendant'
    return False, 'natural_person_count_only'


# ---- IO helpers -----------------------------------------------------------------------------------------

def read_gz_jsonl(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_crosswalk_extension():
    """Returns (direct_matter_to_mdl, extended_matter_to_mdl, member_edge_count_used)."""
    crosswalk = read_jsonl(CROSSWALK_DIR / 'crosswalk.jsonl')
    edges = read_jsonl(CROSSWALK_DIR / 'edges.jsonl')
    direct = {r['aws_matter_id']: r['mdl_number'] for r in crosswalk if r.get('aws_matter_id')}
    extended = dict(direct)
    used = 0
    for e in edges:
        if e.get('relation') == 'member_of_mdl' and e.get('from', {}).get('type') == 'aws_matter' and e.get('to', {}).get('type') == 'mdl':
            member_id, mdl_number = e['from']['id'], e['to']['id']
            if member_id not in extended:
                extended[member_id] = mdl_number
                used += 1
    return direct, extended, used


def load_member_of_mdl_parent_unresolvable(direct_map):
    """Reads SW-BULK's raw matter_relationships.jsonl.gz (read-only) directly -- a step beyond
    sources/mdl_docket_crosswalk_20260919/edges.jsonl, which only carries the 40 member_of_mdl
    relationships whose parent DID resolve to an MDL in the 59-matter crosswalk. Returns
    {member_aws_matter_id: parent_aws_matter_id} for every member_of_mdl relationship whose parent
    matter is NOT in the crosswalk -- i.e. the source states this is a member case of an MDL parent,
    but that parent itself cannot be resolved to an MDL. Per INTEGRATION_PLAN Verification 1, these must
    carry a distinct unresolved reason instead of the generic 'not present in crosswalk' text."""
    rels = list(read_gz_jsonl(AWS_RELEASE / 'matter_relationships.jsonl.gz'))
    member_rels = [r for r in rels if r.get('relationship_type') == 'member_of_mdl']
    bad_from = {}
    for r in member_rels:
        parent_id = r.get('to_matter_id')
        member_id = r.get('from_matter_id')
        if parent_id and member_id and parent_id not in direct_map:
            bad_from[member_id] = parent_id
    return bad_from


def jsonl_bytes(rows):
    return ''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows).encode('utf-8')


def write_data_file(name, rows):
    blob = jsonl_bytes(rows)
    (OUT / name).write_bytes(blob)
    return {'path': name, 'sha256': hashlib.sha256(blob).hexdigest(), 'rows': len(rows)}


def build():
    attorneys_raw = list(read_gz_jsonl(AWS_RELEASE / 'attorneys.jsonl.gz'))
    firms_raw = list(read_gz_jsonl(AWS_RELEASE / 'firms.jsonl.gz'))
    appearances_raw = list(read_gz_jsonl(AWS_RELEASE / 'counsel_appearances.jsonl.gz'))
    parties_raw = list(read_gz_jsonl(AWS_RELEASE / 'parties.jsonl.gz'))

    direct_map, extended_map, member_edges_used = load_crosswalk_extension()
    member_of_mdl_parent_unresolvable = load_member_of_mdl_parent_unresolvable(direct_map)

    def unresolved_reason_and_evidence(matter_id):
        parent_id = member_of_mdl_parent_unresolvable.get(matter_id)
        if parent_id is not None:
            return ('member_of_mdl parent matter %s is not resolvable to an MDL in crosswalk.jsonl' % parent_id,
                    {'parent_matter_id': parent_id})
        return ('matter_id not present in crosswalk.jsonl (direct) nor reachable via edges.jsonl member_of_mdl (extended)', None)

    attorney_by_id = {a['attorney_id']: a for a in attorneys_raw}
    firm_by_id = {f['firm_id']: f for f in firms_raw}
    party_by_id = {p['party_id']: p for p in parties_raw}

    # ---- parties: listing eligibility + per-matter side counts -----------------------------------------
    party_records, unresolved = [], []
    matter_party_counts = {}
    for p in parties_raw:
        matter_id = p['matter_id']
        listable, category = party_listable(p['name'], p.get('party_types'))
        mdl_direct = direct_map.get(matter_id)
        mdl_extended = extended_map.get(matter_id)
        counts = matter_party_counts.setdefault(matter_id, {
            'matter_id': matter_id, 'total_parties': 0, 'listed_parties': 0,
            'organization_parties': 0, 'named_defendant_parties': 0, 'natural_person_plaintiff_count': 0,
        })
        counts['total_parties'] += 1
        if category == 'organization':
            counts['organization_parties'] += 1
            counts['listed_parties'] += 1
        elif category == 'named_defendant':
            counts['named_defendant_parties'] += 1
            counts['listed_parties'] += 1
        else:
            counts['natural_person_plaintiff_count'] += 1
        record = {
            'party_id': p['party_id'], 'matter_id': matter_id, 'party_types': p.get('party_types') or [],
            'category': category, 'listable': listable,
            'name': p['name'] if listable else None,
            'name_withheld_reason': None if listable else 'natural-person plaintiff-side party; published as a count only per BUILD_CONTRACT',
            'mdl_number_direct': mdl_direct, 'mdl_number_extended': mdl_extended,
            'organization_heuristic_basis': ORG_HEURISTIC_BASIS,
        }
        party_records.append(record)
        if mdl_extended is None:
            reason, evidence = unresolved_reason_and_evidence(matter_id)
            entry = {'type': 'party', 'party_id': p['party_id'], 'matter_id': matter_id, 'reason': reason}
            if evidence is not None:
                entry['evidence'] = evidence
            unresolved.append(entry)

    # ---- appearances: role normalisation + mdl resolution + edges --------------------------------------
    appearance_records, edges_out = [], []
    firm_appearance_counts, firm_mdl_reach = {}, {}
    attorney_appearance_counts, attorney_firms, attorney_mdl_reach = {}, {}, {}
    for c in appearances_raw:
        matter_id = c['matter_id']
        role_raw = c.get('role')
        role_normalized, terminated_date_raw = normalize_role(role_raw)
        mdl_direct = direct_map.get(matter_id)
        mdl_extended = extended_map.get(matter_id)
        matched_via = None
        if mdl_extended is not None:
            matched_via = 'direct' if matter_id in direct_map else 'member_of_mdl_edge'
        party = party_by_id.get(c.get('party_id'))
        party_side = None
        if party is not None:
            party_side = 'defendant' if is_named_defendant(party.get('party_types')) else 'plaintiff' if party.get('party_types') else None
        attorney = attorney_by_id.get(c['attorney_id'])
        firm = firm_by_id.get(c.get('firm_id'))
        record = {
            'appearance_id': c['appearance_id'], 'matter_id': matter_id,
            'attorney_id': c['attorney_id'], 'attorney_name': attorney['name'] if attorney else None,
            'firm_id': c.get('firm_id'), 'firm_canonical_name': firm['canonical_name'] if firm else None,
            'raw_firm_name': c.get('raw_firm_name'),
            'party_id': c.get('party_id'), 'party_side': party_side,
            'role_raw': role_raw, 'role_normalized': role_normalized, 'terminated_date_raw': terminated_date_raw,
            'role_normalization_basis': ROLE_NORMALIZATION_BASIS,
            'mdl_number_direct': mdl_direct, 'mdl_number_extended': mdl_extended, 'mdl_match_basis': matched_via,
        }
        appearance_records.append(record)

        fid = c.get('firm_id')
        if fid:
            firm_appearance_counts[fid] = firm_appearance_counts.get(fid, 0) + 1
            if mdl_extended is not None:
                firm_mdl_reach.setdefault(fid, set()).add(mdl_extended)
        aid = c['attorney_id']
        attorney_appearance_counts[aid] = attorney_appearance_counts.get(aid, 0) + 1
        if fid:
            attorney_firms.setdefault(aid, set()).add(fid)
        if mdl_extended is not None:
            attorney_mdl_reach.setdefault(aid, set()).add(mdl_extended)

        if mdl_extended is not None:
            edges_out.append({
                'from': {'type': 'attorney', 'id': aid}, 'to': {'type': 'mdl', 'id': mdl_extended},
                'relation': 'appeared_in',
                'basis': ('AWS release b2b-cdbb8d040b95c7b65cfc counsel_appearances.jsonl.gz record %s; matter %s '
                          'resolved to mdl:%d via %s (mdl_docket_crosswalk_20260919)' % (c['appearance_id'], matter_id, mdl_extended, matched_via)),
                'evidence': {'appearance_id': c['appearance_id'], 'matter_id': matter_id, 'firm_id': fid,
                             'role_raw': role_raw, 'matched_via': matched_via},
            })
        else:
            reason, evidence = unresolved_reason_and_evidence(matter_id)
            entry = {'type': 'counsel_appearance', 'appearance_id': c['appearance_id'], 'matter_id': matter_id, 'reason': reason}
            if evidence is not None:
                entry['evidence'] = evidence
            unresolved.append(entry)

    # ---- firms.jsonl / attorneys.jsonl rollups ----------------------------------------------------------
    firm_records = []
    for f in firms_raw:
        fid = f['firm_id']
        firm_records.append({
            'firm_id': fid, 'canonical_name': f['canonical_name'],
            'appearance_count': firm_appearance_counts.get(fid, 0),
            'mdl_numbers_reached_extended': sorted(firm_mdl_reach.get(fid, set())),
        })
    attorney_records = []
    for a in attorneys_raw:
        aid = a['attorney_id']
        attorney_records.append({
            'attorney_id': aid, 'name': a['name'],
            'appearance_count': attorney_appearance_counts.get(aid, 0),
            'firm_ids': sorted(attorney_firms.get(aid, set())),
            'mdl_numbers_reached_extended': sorted(attorney_mdl_reach.get(aid, set())),
        })

    # ---- counts for validation.json ----------------------------------------------------------------------
    role_counts_raw = {}
    for c in appearances_raw:
        role_counts_raw[c.get('role')] = role_counts_raw.get(c.get('role'), 0) + 1
    direct_matters_touched = {c['matter_id'] for c in appearances_raw if c['matter_id'] in direct_map}
    extended_matters_touched = {c['matter_id'] for c in appearances_raw if c['matter_id'] in extended_map}
    direct_appearances = sum(1 for c in appearances_raw if c['matter_id'] in direct_map)
    extended_appearances = sum(1 for c in appearances_raw if c['matter_id'] in extended_map)
    direct_parties = sum(1 for p in parties_raw if p['matter_id'] in direct_map)
    extended_parties = sum(1 for p in parties_raw if p['matter_id'] in extended_map)
    direct_mdls = {direct_map[c['matter_id']] for c in appearances_raw if c['matter_id'] in direct_map}
    extended_mdls = {extended_map[c['matter_id']] for c in appearances_raw if c['matter_id'] in extended_map}

    distinct_attorney_names = {normalize_attorney_name(a['name']) for a in attorneys_raw}
    unresolved_mdl_parent_appearances = sum(1 for u in unresolved if u['type'] == 'counsel_appearance' and 'evidence' in u)
    unresolved_mdl_parent_parties = sum(1 for u in unresolved if u['type'] == 'party' and 'evidence' in u)

    counts = {
        'attorney_records_total': len(attorneys_raw),
        'attorney_records_distinct_normalized_names': len(distinct_attorney_names),
        'firms_total': len(firms_raw),
        'appearances_total': len(appearances_raw), 'parties_total': len(parties_raw),
        'matters_total': len({c['matter_id'] for c in appearances_raw}),
        'appearances_direct_mdl_linked': direct_appearances, 'appearances_extended_mdl_linked': extended_appearances,
        'parties_direct_mdl_linked': direct_parties, 'parties_extended_mdl_linked': extended_parties,
        'mdls_reached_direct': len(direct_mdls), 'mdls_reached_extended': len(extended_mdls),
        'matters_direct_mdl_linked': len(direct_matters_touched), 'matters_extended_mdl_linked': len(extended_matters_touched),
        'member_of_mdl_edges_used_for_extension': member_edges_used,
        'appearance_firm_counts': dict(sorted(firm_appearance_counts.items(), key=lambda kv: -kv[1])),
        'role_raw_spelling_counts': role_counts_raw,
        'role_raw_spelling_count_distinct': len(role_counts_raw),
        'party_categories': {
            'organization': sum(1 for r in party_records if r['category'] == 'organization'),
            'named_defendant': sum(1 for r in party_records if r['category'] == 'named_defendant'),
            'natural_person_count_only': sum(1 for r in party_records if r['category'] == 'natural_person_count_only'),
        },
        'unresolved_total': len(unresolved),
        'unresolved_member_of_mdl_parent_unresolvable': {
            'appearances': unresolved_mdl_parent_appearances, 'parties': unresolved_mdl_parent_parties,
        },
    }

    data_files = [
        write_data_file('attorneys.jsonl', attorney_records),
        write_data_file('firms.jsonl', firm_records),
        write_data_file('appearances.jsonl', appearance_records),
        write_data_file('parties.jsonl', party_records),
        write_data_file('party_counts_by_matter.jsonl', sorted(matter_party_counts.values(), key=lambda r: r['matter_id'])),
        write_data_file('edges.jsonl', edges_out),
        write_data_file('unresolved.jsonl', unresolved),
    ]

    inputs = []
    for name in AWS_FILES:
        p = AWS_RELEASE / name
        inputs.append({'path': str(p), 'sha256': sha256_of(p)})
    for name in ('crosswalk.jsonl', 'edges.jsonl'):
        p = CROSSWALK_DIR / name
        inputs.append({'path': str(p), 'sha256': sha256_of(p)})

    checks = [
        {'name': 'appearances_row_count_878', 'passed': len(appearances_raw) == 878},
        {'name': 'attorneys_row_count_181', 'passed': len(attorneys_raw) == 181},
        {'name': 'attorney_distinct_normalized_names_74', 'passed': len(distinct_attorney_names) == 74},
        {'name': 'firms_row_count_12', 'passed': len(firms_raw) == 12},
        {'name': 'parties_row_count_503', 'passed': len(parties_raw) == 503},
        {'name': 'role_raw_spellings_9', 'passed': len(role_counts_raw) == 9},
        {'name': 'direct_link_matches_plan_192_appearances_67_parties_3_mdls',
         'passed': direct_appearances == 192 and direct_parties == 67 and len(direct_mdls) == 3},
        {'name': 'extended_link_matches_plan_607_appearances_369_parties_14_mdls',
         'passed': extended_appearances == 607 and extended_parties == 369 and len(extended_mdls) == 14},
        {'name': 'unresolved_member_of_mdl_parent_unresolvable_61_appearances_19_parties',
         'passed': unresolved_mdl_parent_appearances == 61 and unresolved_mdl_parent_parties == 19},
        {'name': 'no_dollar_or_contact_fields_published',
         'passed': not any(k in rec for rec in appearance_records for k in ('email', 'phone', 'address'))},
    ]

    qualification = (
        "Local personal-testing view; derived from a private firm dataset (SW-BULK AWS release "
        "b2b-cdbb8d040b95c7b65cfc) built from CourtListener/RECAP API data; not for redistribution. Counsel "
        "appearances (878), attorney appearance records (181, covering 74 distinct name spellings; the same "
        "person may hold several source UUIDs and records are never merged by name), firms (12) and parties "
        "(503) across 62 matters, as captured in this AWS release; not a bar-admission record and not every "
        "firm appearing in these MDLs -- only the 12 firms this source pipeline tracked. MDL linkage runs "
        "through sources/mdl_docket_crosswalk_20260919: on the 59-matter crosswalk alone this reaches 3 MDLs "
        "(192 appearances / 67 parties); extended through the crosswalk's member_of_mdl edges it reaches 14 "
        "MDLs (607 appearances / 369 parties). Both figures are always shown together. Attorney ids here are "
        "this pipeline's own UUIDs, never CourtListener attorney ids; this layer is published alongside "
        "sources/mdl_counsel_20260919, never merged with it. Natural-person plaintiff-side parties are "
        "published as counts only, never by name; organisations and named defendants may be listed by name "
        "using a conservative, name-only heuristic."
    )
    qualification_short = (
        "Local personal-testing view from a private firm dataset (SW-BULK b2b-cdbb8d040b95c7b65cfc, "
        "CourtListener/RECAP-derived); not for redistribution. Only the 12 plaintiff firms this pipeline "
        "tracked, not every firm in this MDL. MDL linkage: 3 MDLs direct / 14 crosswalk-extended. "
        "Natural-person plaintiffs are counts only."
    )
    assert len(qualification_short) < 500, 'qualification_short must stay under the 500-char embedding ceiling'

    validation = {
        'schema_version': '1', 'status': 'passed', 'ready': True,
        'validated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'data_files': data_files, 'counts': counts, 'checks': checks,
        'qualification': qualification, 'qualification_short': qualification_short,
        'license_ref': 'sw_bulk_private_firm_work_product',
        'export_allowed': False, 'inputs': inputs,
    }
    if not all(c['passed'] for c in checks):
        validation['status'] = 'failed'
        validation['ready'] = False
    (OUT / 'validation.json').write_text(json.dumps(validation, indent=2, sort_keys=True), encoding='utf-8')
    return validation


if __name__ == '__main__':
    result = build()
    print(json.dumps({'status': result['status'], 'ready': result['ready'], 'counts': result['counts']}, indent=2))
