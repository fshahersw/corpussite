#!/usr/bin/env python3
"""Judge evidence layer (offline; local inputs only, no network).

Attaches citable, publisher-labelled facts to saved judge entities:
  cjra         per-judge Civil Justice Reform Act counts as printed (as of 2026-03-31)
  assignments  matters in the saved SW-BULK docket release, joined ONLY by CourtListener person id
  education / positions   as recorded by CourtListener, joined ONLY by CourtListener person id
Nothing is merged by name alone: a CJRA row attaches only when surname + given-name initial(s) agree with the FJC export
name of an entity that holds an FJC appointment to the SAME district court, and exactly one entity qualifies.
Everything else goes to unresolved.jsonl with a reason. No rates, predictions or rankings are computed.
"""
from __future__ import annotations
import csv
import gzip
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
CJRA_DIR = ROOT / 'sources/federal_court_statistics_20260919'
CJRA_ROWS = CJRA_DIR / 'cjra_rows.jsonl'
OVERLAY_DIR = ROOT / 'sources/judge_structured_20260919'
OVERLAY = OVERLAY_DIR / 'overlay.jsonl'
FJC_CSV = ROOT / 'sources/judges/enrichment_20260914/federal_biographies/judges.csv'
CL_DB = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
SW = Path('C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc')
SW_FILES = ('judges', 'judicial_assignments', 'matters', 'matter_relationships', 'courts', 'source_records')

ENTITIES_EXPECTED = 10669
CJRA_CAVEAT = 'publisher-reported counts for the period; MDL member cases inflate counts; never a rate'
CJRA_RULE = ('exact normalized surname + given-name initial(s) of the name as printed agree with the FJC export name of the entity '
             '(looked up by the entity\'s native FJC nid), the entity holds an FJC appointment to the same U.S. district court, '
             'an FJC appointment of the entity is open on the as-of date, and exactly one entity qualifies')
DIRECTIONS = {'NORTHERN', 'SOUTHERN', 'EASTERN', 'WESTERN', 'MIDDLE', 'CENTRAL'}
SUFFIXES = {'JR', 'SR', 'II', 'III', 'IV'}
REASONS = {
    'magistrate_judge_not_in_fjc_directory': 'Magistrate judges are not in the FJC Article III directory, so no same-court FJC appointment can corroborate the printed name.',
    'bankruptcy_judge_not_in_fjc_directory': 'Bankruptcy judges are not in the FJC Article III directory, so no same-court FJC appointment can corroborate the printed name.',
    'name_unparseable': 'The printed label is not a personal name in "SURNAME, GIVEN" form (for example "Unassigned" or "Bankruptcy Appeal").',
    'court_label_not_understood': 'The printed court label could not be normalised to a U.S. district court.',
    'no_entity_with_same_surname_and_court': 'No saved entity with this exact surname holds an FJC appointment to the same district court.',
    'given_name_does_not_agree': 'An entity shares surname and court, but the given-name initial(s) or full given names do not agree.',
    'ambiguous_multiple_entities': 'More than one saved entity agrees on surname, given-name initial(s) and district court; never guessed.',
    'no_fjc_appointment_open_on_as_of_date': 'The only agreeing entity has no FJC appointment open on the CJRA as-of date.',
    'multiple_printed_names_claim_one_entity': 'Two differently printed judges in the same table resolved to one entity; all of them are withheld.',
}
FORBIDDEN_KEY_WORDS = ('rate', 'percent', 'predict', 'rank', 'score', 'likelihood', 'average')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def plain(value) -> str:
    text = unicodedata.normalize('NFKD', str(value or ''))
    return ''.join(c for c in text if not unicodedata.combining(c))


def letters(value) -> str:
    return re.sub(r'[^A-Z]', '', plain(value).upper())


def name_tokens(value) -> list:
    return [t for t in re.split(r'[^A-Z]+', plain(value).upper().replace("'", '')) if t]


def court_key(label):
    """('NEW JERSEY', '') / ('ALABAMA', 'MIDDLE') for both the FJC and the CJRA way of printing a district court; else None."""
    match = re.match(r'^\s*U\.S\. District Court for (.+?)\s*$', str(label or ''), re.I)
    if not match:
        return None
    body = re.sub(r'\s+', ' ', match.group(1))
    if body.lower().startswith('the '):
        fjc = re.match(r'^(?:(Northern|Southern|Eastern|Western|Middle|Central) )?District of (.+)$', body[4:], re.I)
        if not fjc:
            return None
        return (fjc.group(2).upper(), (fjc.group(1) or '').upper())
    if body.lower().startswith('district of '):
        return (body[12:].upper(), '')
    parts = body.split(' ')
    if len(parts) > 1 and parts[-1].upper() in DIRECTIONS:
        return (' '.join(parts[:-1]).upper(), parts[-1].upper())
    return (body.upper(), '')


def parse_printed_name(name):
    """'LACOUR, EDMUND G. JR.' -> {'surname': 'LACOUR', 'given': ['EDMUND', 'G'], 'suffix': ['JR']}; None when not 'SURNAME, GIVEN'."""
    text = str(name or '')
    if ',' not in text:
        return None
    surname_raw, rest = text.split(',', 1)
    surname_tokens = [t for t in name_tokens(surname_raw)]
    suffix = []
    while len(surname_tokens) > 1 and surname_tokens[-1] in SUFFIXES:
        suffix.append(surname_tokens.pop())
    given = []
    for token in name_tokens(rest):
        (suffix if token in SUFFIXES else given).append(token)
    surname = ''.join(surname_tokens)
    if not surname or not given:
        return None
    return {'surname': surname, 'given': given, 'suffix': sorted(suffix)}


def _token_agrees(printed: str, recorded: str) -> bool:
    if printed[0] != recorded[0]:
        return False
    if len(printed) > 1 and len(recorded) > 1:
        return printed == recorded or printed.startswith(recorded) or recorded.startswith(printed)
    return True


def given_agrees(printed_given: list, entity: dict) -> bool:
    recorded = name_tokens(entity.get('first'))
    if not printed_given or not recorded:
        return False
    if not _token_agrees(printed_given[0], recorded[0]):
        return False
    printed_middle = printed_given[1:]
    recorded_middle = recorded[1:] + name_tokens(entity.get('middle'))
    if printed_middle and recorded_middle and not _token_agrees(printed_middle[0], recorded_middle[0]):
        return False
    return True


def open_on(entity: dict, as_of: str) -> bool:
    for appointment in entity.get('appointments') or []:
        end = appointment.get('termination_date')
        if not end or str(end) >= as_of:
            return True
    return False


def build_entity_index(entities) -> dict:
    index = defaultdict(list)
    for entity in entities:
        surname = letters(entity.get('last'))
        if not surname:
            continue
        keys = set()
        for appointment in entity.get('appointments') or []:
            if appointment.get('court_type') != 'U.S. District Court':
                continue
            key = court_key(appointment.get('court'))
            if key:
                keys.add(key)
        for key in keys:
            index[(surname, key)].append(entity)
    return index


def match_printed_judge(name, judge_type, court, index, as_of='2026-03-31') -> dict:
    out = {'entity_id': None, 'reason_code': None, 'candidate_entity_ids': []}
    kind = str(judge_type or '').lower()
    if 'magistrate' in kind:
        out['reason_code'] = 'magistrate_judge_not_in_fjc_directory'
        return out
    if 'bankruptcy' in kind:
        out['reason_code'] = 'bankruptcy_judge_not_in_fjc_directory'
        return out
    parsed = parse_printed_name(name)
    if not parsed:
        out['reason_code'] = 'name_unparseable'
        return out
    key = court_key(court)
    if not key:
        out['reason_code'] = 'court_label_not_understood'
        return out
    pool = index.get((parsed['surname'], key)) or []
    if not pool:
        out['reason_code'] = 'no_entity_with_same_surname_and_court'
        return out
    agreeing = [e for e in pool if given_agrees(parsed['given'], e)]
    if not agreeing:
        out['reason_code'] = 'given_name_does_not_agree'
        out['candidate_entity_ids'] = sorted(e['entity_id'] for e in pool)
        return out
    if len(agreeing) > 1:
        out['reason_code'] = 'ambiguous_multiple_entities'
        out['candidate_entity_ids'] = sorted(e['entity_id'] for e in agreeing)
        return out
    entity = agreeing[0]
    if not open_on(entity, as_of):
        out['reason_code'] = 'no_fjc_appointment_open_on_as_of_date'
        out['candidate_entity_ids'] = [entity['entity_id']]
        return out
    out['entity_id'] = entity['entity_id']
    out['entity'] = entity
    out['parsed'] = parsed
    out['court_key'] = key
    return out


# ----------------------------------------------------------------------------------------------------------------- loading
def read_jsonl(path: Path) -> list:
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_gz(name: str) -> list:
    with gzip.open(SW / (name + '.jsonl.gz'), 'rt', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def none_if_blank(value):
    value = (value or '').strip() if isinstance(value, str) else value
    return value or None


def display_date(value, granularity):
    value = none_if_blank(value)
    if not value:
        return None
    if granularity == '%Y':
        return value[:4]
    if granularity == '%Y-%m':
        return value[:7]
    return value


def load_entities():
    fjc = {}
    with open(FJC_CSV, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            fjc[str(row['nid']).strip()] = row
    entities, by_cl, overlay_rows = [], defaultdict(list), {}
    for row in read_jsonl(OVERLAY):
        ids = row.get('ids') or {}
        overlay_rows[row['entity_id']] = row
        cl = none_if_blank(str(ids.get('cl_person_id') or ''))
        if cl:
            by_cl[cl].append(row['entity_id'])
        nid = none_if_blank(str(ids.get('fjc_nid') or ''))
        source = fjc.get(nid) if nid else None
        if source:
            entities.append({'entity_id': row['entity_id'], 'fjc_nid': nid, 'last': source.get('Last Name'),
                             'first': source.get('First Name'), 'middle': source.get('Middle Name'), 'suffix': source.get('Suffix'),
                             'appointments': (row.get('fjc') or {}).get('appointments') or []})
    return overlay_rows, entities, by_cl


# ------------------------------------------------------------------------------------------------------------------- CJRA
def build_cjra(entities):
    index = build_entity_index(entities)
    groups = defaultdict(list)
    for row in read_jsonl(CJRA_ROWS):
        parsed = parse_printed_name(row.get('judge_name_as_printed'))
        key = (court_key(row.get('court_as_printed')) or row.get('court_as_printed'), row.get('judge_type_as_printed'),
               (parsed['surname'], tuple(parsed['given']), tuple(parsed['suffix'])) if parsed else row.get('judge_name_as_printed'))
        groups[key].append(row)
    claims, unresolved = defaultdict(list), []
    for key in sorted(groups, key=repr):
        rows = sorted(groups[key], key=lambda r: r['row_id'])
        first = rows[0]
        result = match_printed_judge(first['judge_name_as_printed'], first['judge_type_as_printed'], first['court_as_printed'], index,
                                     as_of=first.get('source_as_of') or '2026-03-31')
        if result['entity_id']:
            claims[result['entity_id']].append((rows, result))
        else:
            unresolved.append(unresolved_row(rows, result['reason_code'], result['candidate_entity_ids']))
    attached = {}
    for entity_id, claim_list in claims.items():
        per_court = defaultdict(list)
        for rows, result in claim_list:
            per_court[result['court_key']].append((rows, result))
        blocks = []
        for key in sorted(per_court):
            court_claims = per_court[key]
            tables = [r['table_number'] for rows, _ in court_claims for r in rows]
            if len(tables) != len(set(tables)):
                for rows, _ in court_claims:
                    unresolved.append(unresolved_row(rows, 'multiple_printed_names_claim_one_entity', [entity_id]))
                continue
            rows = sorted((r for rws, _ in court_claims for r in rws), key=lambda r: (r['table_number'], r['row_id']))
            blocks.append(cjra_block(rows, court_claims[0][1]))
        if blocks:
            # A judge appointed to two districts (FJC lists both courts) is printed once per court; counts are never summed.
            attached[entity_id] = dict(blocks[0], other_courts=blocks[1:])
    return attached, unresolved


def given_name_agreement(printed_given, entity) -> str:
    printed, recorded = printed_given[0], name_tokens(entity.get('first'))[0]
    if printed == recorded:
        return 'identical_first_name' if len(printed) > 1 else 'identical_initial'
    if len(printed) == 1 or len(recorded) == 1:
        return 'initial_only'
    return 'one_first_name_is_a_prefix_of_the_other'


def cjra_block(rows, result):
        entity = result['entity']
        fjc_court = next(a.get('court') for a in entity['appointments']
                         if a.get('court_type') == 'U.S. District Court' and court_key(a.get('court')) == result['court_key'])
        return {
            'as_of': rows[0].get('source_as_of'),
            'as_of_basis': rows[0].get('source_as_of_basis'),
            'period_label': rows[0].get('period_label'),
            'publisher': 'Administrative Office of the U.S. Courts, Civil Justice Reform Act report (tables CJRA 7, 8, 9)',
            'tables': [{'table': r['table_number'], 'label': r['count_label'], 'count': r['count'], 'count_basis': r.get('count_basis'),
                        'judge_name_as_printed': r['judge_name_as_printed'], 'pdf_pages': r.get('pdf_pages'),
                        'statistics_file_id': r.get('file_id'), 'row_id': r['row_id']} for r in rows],
            'court_as_printed': rows[0]['court_as_printed'],
            'judge_name_as_printed': rows[0]['judge_name_as_printed'],
            'judge_type_as_printed': rows[0]['judge_type_as_printed'],
            'row_ids': [r['row_id'] for r in rows],
            'captured_at': max(r.get('captured_at') or '' for r in rows) or None,
            'match_basis': {'rule': CJRA_RULE, 'surname_normalized': result['parsed']['surname'],
                            'given_tokens_as_printed': result['parsed']['given'],
                            'given_name_agreement': given_name_agreement(result['parsed']['given'], entity), 'fjc_nid': entity['fjc_nid'],
                            'fjc_name': ' '.join(p for p in (str(entity.get(k) or '').strip() for k in ('first', 'middle', 'last', 'suffix')) if p),
                            'fjc_court': fjc_court, 'qualifying_entities': 1,
                            'verified_independently': False},
            'caveat': CJRA_CAVEAT,
        }


def unresolved_row(rows, reason_code, candidates):
    first = rows[0]
    return {'kind': 'cjra', 'reason_code': reason_code, 'reason': REASONS[reason_code],
            'court_as_printed': first['court_as_printed'], 'judge_type_as_printed': first['judge_type_as_printed'],
            'judge_name_as_printed': first['judge_name_as_printed'],
            'names_as_printed': sorted({r['judge_name_as_printed'] for r in rows}),
            'as_of': first.get('source_as_of'), 'candidate_entity_ids': sorted(candidates or []),
            'tables': [{'table': r['table_number'], 'label': r['count_label'], 'count': r['count']} for r in sorted(rows, key=lambda r: r['table_number'])],
            'row_ids': sorted(r['row_id'] for r in rows), 'caveat': CJRA_CAVEAT}


# ------------------------------------------------------------------------------------------------------------ assignments
def build_assignments(by_cl, manifest):
    built = str(manifest.get('built_at') or '')[:10]
    judges = read_gz('judges')
    matters = {m['matter_id']: m for m in read_gz('matters')}
    total = len(matters)
    courts = {c['court_id']: c.get('name') for c in read_gz('courts')}
    nature, docket_ids = {}, {}
    for record in read_gz('source_records'):
        if record.get('source_type') != 'courtlistener_bulk_dockets':
            continue
        attributes = record.get('attributes') or {}
        value = none_if_blank(attributes.get('nature_of_suit'))
        if value and record.get('matter_id') not in nature:
            nature[record['matter_id']] = value
        if attributes.get('docket_id') and record.get('matter_id') not in docket_ids:
            docket_ids[record['matter_id']] = attributes['docket_id']
    parents_of, members_of = defaultdict(set), defaultdict(set)
    for rel in read_gz('matter_relationships'):
        if rel.get('relationship_type') == 'member_of_mdl':
            parents_of[rel['from_matter_id']].add(rel['to_matter_id'])
            members_of[rel['to_matter_id']].add(rel['from_matter_id'])
    roles_by_judge = defaultdict(lambda: defaultdict(set))
    for row in read_gz('judicial_assignments'):
        if row.get('record_status') == 'active' and row.get('matter_id') in matters:
            roles_by_judge[row['judge_id']][row['matter_id']].add(row['role'])
    blocks, unresolved = {}, []
    sw_by_cl = defaultdict(list)
    for judge in judges:
        native = none_if_blank(str(judge.get('native_judge_id') or ''))
        if not native:
            unresolved.append({'kind': 'sw_bulk_judge', 'reason_code': 'no_courtlistener_person_id_in_release',
                               'reason': 'The release row carries no CourtListener person id; names are never used to attach matters.',
                               'judge_name_as_recorded': judge.get('name'), 'sw_judge_id': judge['judge_id'],
                               'matters': len(roles_by_judge.get(judge['judge_id']) or {}), 'candidate_entity_ids': []})
        else:
            sw_by_cl[native].append(judge)
    for native in sorted(sw_by_cl, key=lambda v: (len(v), v)):
        rows = sw_by_cl[native]
        entity_ids = by_cl.get(native) or []
        merged = defaultdict(set)
        for judge in rows:
            for matter_id, roles in (roles_by_judge.get(judge['judge_id']) or {}).items():
                merged[matter_id] |= roles
        if len(entity_ids) != 1:
            code = 'courtlistener_person_id_not_bridged_to_an_entity' if not entity_ids else 'courtlistener_person_id_shared_by_multiple_entities'
            unresolved.append({'kind': 'sw_bulk_judge', 'reason_code': code,
                               'reason': 'Matters attach only when exactly one saved entity carries this CourtListener person id by native-id bridge.',
                               'judge_name_as_recorded': rows[0].get('name'), 'cl_person_id': native,
                               'sw_judge_id': rows[0]['judge_id'], 'matters': len(merged), 'candidate_entity_ids': sorted(entity_ids)})
            continue
        if not merged:
            continue
        listed = []
        for matter_id, roles in merged.items():
            m = matters[matter_id]
            listed.append({'docket_number': m.get('docket_number'), 'case_name': m.get('case_name'), 'court_id': m.get('court_id'),
                           'court_name': courts.get(m.get('court_id')), 'date_filed': none_if_blank(m.get('date_filed')),
                           'date_terminated': none_if_blank(m.get('date_terminated')), 'case_status': m.get('case_status'),
                           'roles': sorted(roles), 'dataset_node_role': m.get('node_role'), 'nature_of_suit': nature.get(matter_id),
                           'cl_docket_id': docket_ids.get(matter_id), '_matter_id': matter_id})
        listed.sort(key=lambda m: (m['date_filed'] or '', m['docket_number'] or ''), reverse=True)
        by_court = Counter(m['court_id'] for m in listed)
        by_nature = Counter(m['nature_of_suit'] for m in listed if m['nature_of_suit'])
        parent_ids = {}
        for m in listed:
            if m['_matter_id'] in members_of:
                parent_ids.setdefault(m['_matter_id'], set()).add('assigned_to_mdl_parent_docket')
            for parent in parents_of.get(m['_matter_id'], ()):
                parent_ids.setdefault(parent, set()).add('assigned_to_member_matter')
        mdl_parents = []
        for parent, relations in sorted(parent_ids.items()):
            p = matters.get(parent)
            if p:
                mdl_parents.append({'docket_number': p.get('docket_number'), 'case_name': p.get('case_name'), 'court_id': p.get('court_id'),
                                    'court_name': courts.get(p.get('court_id')), 'relations': sorted(relations),
                                    'member_matters_in_dataset': len(members_of.get(parent) or ()),
                                    'member_matters_of_this_judge': sum(1 for m in listed if parent in parents_of.get(m['_matter_id'], ()))})
        filed = sorted(m['date_filed'] for m in listed if m['date_filed'])
        terminated = sorted(m['date_terminated'] for m in listed if m['date_terminated'])
        recent = [{k: v for k, v in m.items() if not k.startswith('_')} for m in listed[:25]]
        blocks[entity_ids[0]] = {
            'dataset_label': 'matters in the saved docket dataset (release built %s; %s of %s matters)' % (built, format(len(listed), ','), format(total, ',')),
            'dataset_note': 'A saved, firm-focused docket dataset, not a complete docket history of the judge; counts describe this dataset only.',
            'release_built_at': manifest.get('built_at'), 'matters_in_dataset': total, 'matters_for_this_judge': len(listed),
            'assigned_count': sum(1 for m in listed if 'assigned_judge' in m['roles']),
            'referred_count': sum(1 for m in listed if 'referred_judge' in m['roles']),
            'by_court': [{'court_id': c, 'court_name': courts.get(c), 'matters': n} for c, n in sorted(by_court.items(), key=lambda kv: (-kv[1], kv[0] or ''))],
            'by_nature_of_suit': [{'nature_of_suit': k, 'matters': n} for k, n in sorted(by_nature.items(), key=lambda kv: (-kv[1], kv[0]))[:8]],
            'nature_of_suit_basis': 'nature_of_suit as recorded in the CourtListener bulk docket source records of the release',
            'nature_of_suit_not_recorded': sum(1 for m in listed if not m['nature_of_suit']),
            'date_filed_first': filed[0] if filed else None, 'date_filed_last': filed[-1] if filed else None,
            'date_terminated_last': terminated[-1] if terminated else None,
            'recent_matters': recent, 'recent_matters_basis': 'up to 25 matters with the latest date_filed in the dataset',
            'mdl_parents': mdl_parents,
            'join': {'cl_person_id': native, 'basis': 'release judges.native_judge_id == overlay ids.cl_person_id (native-id bridge; no name matching)',
                     'judge_name_in_release': rows[0].get('name')},
        }
    return blocks, unresolved, total


# ---------------------------------------------------------------------------------------------------------- CourtListener
def build_courtlistener(by_cl):
    wanted = {cl: ids[0] for cl, ids in by_cl.items() if len(ids) == 1}
    con = sqlite3.connect('file:' + CL_DB.as_posix() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    schools = {r['id']: r['name'] for r in con.execute('select id, name from schools')}
    courts = {r['id']: r['full_name'] for r in con.execute('select id, full_name from courts')}
    education, positions = defaultdict(list), defaultdict(list)
    for r in con.execute('select * from educations order by person_id, degree_year, cast(id as integer)'):
        entity_id = wanted.get(r['person_id'])
        if entity_id:
            education[entity_id].append({'school': schools.get(r['school_id']), 'degree_level': none_if_blank(r['degree_level']),
                                         'degree_detail': none_if_blank(r['degree_detail']), 'degree_year': none_if_blank(r['degree_year']),
                                         'cl_education_id': r['id']})
    for r in con.execute('select * from positions order by person_id, date_start, cast(id as integer)'):
        entity_id = wanted.get(r['person_id'])
        if not entity_id:
            continue
        positions[entity_id].append({
            'position_type': none_if_blank(r['position_type']), 'job_title': none_if_blank(r['job_title']),
            'organization_name': none_if_blank(r['organization_name']), 'court_id': none_if_blank(r['court_id']),
            'court_name': courts.get(r['court_id']), 'school': schools.get(r['school_id']),
            'location_city': none_if_blank(r['location_city']), 'location_state': none_if_blank(r['location_state']),
            'date_start': none_if_blank(r['date_start']), 'date_start_granularity': none_if_blank(r['date_granularity_start']),
            'date_start_display': display_date(r['date_start'], r['date_granularity_start']),
            'date_termination': none_if_blank(r['date_termination']), 'date_termination_granularity': none_if_blank(r['date_granularity_termination']),
            'date_termination_display': display_date(r['date_termination'], r['date_granularity_termination']),
            'termination_reason': none_if_blank(r['termination_reason']), 'how_selected': none_if_blank(r['how_selected']),
            'date_nominated': none_if_blank(r['date_nominated']), 'date_confirmation': none_if_blank(r['date_confirmation']),
            'date_elected': none_if_blank(r['date_elected']), 'has_inferred_values': r['has_inferred_values'] == 't',
            'cl_position_id': r['id']})
    con.close()
    return education, positions


def forbidden_keys(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if any(word in key.lower() for word in FORBIDDEN_KEY_WORDS):
                found.add(key)
            forbidden_keys(value, found)
    elif isinstance(node, list):
        for value in node:
            forbidden_keys(value, found)


def main() -> int:
    checks, inputs = [], []

    def check(name, ok, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)})

    manifest = json.loads((SW / 'manifest.json').read_text(encoding='utf-8'))
    for name in SW_FILES:
        digest = sha256_file(SW / (name + '.jsonl.gz'))
        check('sw_bulk_manifest_hash:' + name, digest == manifest['datasets'][name]['sha256'])
        inputs.append({'path': 'SW-BULK release b2b-cdbb8d040b95c7b65cfc/%s.jsonl.gz' % name, 'sha256': digest})
    for folder, filename in ((CJRA_DIR, 'cjra_rows.jsonl'), (OVERLAY_DIR, 'overlay.jsonl')):
        upstream = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
        digest = sha256_file(folder / filename)
        declared = {f['path']: f['sha256'] for f in upstream.get('data_files') or []}
        check('upstream_gate:' + folder.name, upstream.get('status') == 'passed' and upstream.get('ready') is True and declared.get(filename) == digest)
        inputs.append({'path': 'sources/%s/%s' % (folder.name, filename), 'sha256': digest})
    inputs.append({'path': 'sources/judges/enrichment_20260914/federal_biographies/judges.csv', 'sha256': sha256_file(FJC_CSV)})
    inputs.append({'path': 'sources/courtlistener_people_20260918/catalog.sqlite3', 'sha256': sha256_file(CL_DB)})

    overlay_rows, entities, by_cl = load_entities()
    cjra, unresolved = build_cjra(entities)
    assignments, sw_unresolved, matters_total = build_assignments(by_cl, manifest)
    education, positions = build_courtlistener(by_cl)
    unresolved += sw_unresolved
    snapshot = None
    records = []
    for entity_id in sorted(set(cjra) | set(assignments) | set(education) | set(positions)):
        row = overlay_rows[entity_id]
        ids = row.get('ids') or {}
        cl_meta = row.get('courtlistener') or {}
        snapshot = snapshot or cl_meta.get('source_snapshot')
        record = {'entity_id': entity_id, 'name': row.get('name'),
                  'ids': {'cl_person_id': none_if_blank(str(ids.get('cl_person_id') or '')), 'fjc_nid': none_if_blank(str(ids.get('fjc_nid') or '')),
                          'fjc_jid': none_if_blank(str(ids.get('fjc_jid') or '')), 'bridge_status': ids.get('bridge_status')},
                  'cjra': cjra.get(entity_id), 'assignments': assignments.get(entity_id),
                  'education': education.get(entity_id) or [], 'positions': positions.get(entity_id) or []}
        record['blocks'] = [b for b in ('cjra', 'assignments', 'education', 'positions') if record[b]]
        if record['education'] or record['positions']:
            record['courtlistener_source'] = {'source': cl_meta.get('source') or 'CourtListener people bulk data (Free Law Project)',
                                              'source_snapshot': cl_meta.get('source_snapshot'), 'source_snapshot_basis': cl_meta.get('source_snapshot_basis'),
                                              'join_basis': 'CourtListener person_id == overlay ids.cl_person_id (native-id bridge)',
                                              'note': 'As recorded by CourtListener with its own date granularity; not independently verified.'}
        record['temporal'] = {'captured_at': None, 'captured_at_basis': 'composite record; every block carries its own as-of / snapshot / release date',
                              'source_as_of': None, 'source_as_of_basis': None, 'published_at': None, 'published_at_basis': None,
                              'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None}
        records.append(record)
    unresolved.sort(key=lambda r: (r['kind'], r['reason_code'], r.get('court_as_printed') or '', r.get('judge_name_as_printed') or r.get('judge_name_as_recorded') or ''))
    for number, row in enumerate(unresolved, 1):
        row['unresolved_id'] = 'jev-unresolved-%05d' % number

    attached_rows = [rid for r in records if r['cjra'] for block in [r['cjra']] + r['cjra']['other_courts'] for rid in block['row_ids']]
    unresolved_rows = [rid for r in unresolved if r['kind'] == 'cjra' for rid in r['row_ids']]
    cjra_total = len(read_jsonl(CJRA_ROWS))
    check('overlay_entities_expected', len(overlay_rows) == ENTITIES_EXPECTED, len(overlay_rows))
    check('cjra_rows_accounted_once', len(attached_rows) + len(unresolved_rows) == cjra_total == len(set(attached_rows) | set(unresolved_rows)),
          '%d attached + %d unresolved of %d' % (len(attached_rows), len(unresolved_rows), cjra_total))
    check('no_magistrate_or_bankruptcy_row_attached',
          not any(word in block['judge_type_as_printed'] for r in records if r['cjra'] for block in [r['cjra']] + r['cjra']['other_courts']
                  for word in ('Magistrate', 'Bankruptcy')))
    check('assignments_join_is_cl_person_id', all(r['assignments']['join']['cl_person_id'] == r['ids']['cl_person_id'] for r in records if r['assignments']))
    check('courtlistener_blocks_require_cl_person_id', all(r['ids']['cl_person_id'] for r in records if r['education'] or r['positions']))
    check('recent_matters_capped_at_25', all(len(r['assignments']['recent_matters']) <= 25 and len(r['assignments']['by_nature_of_suit']) <= 8 for r in records if r['assignments']))
    check('entity_ids_unique', len({r['entity_id'] for r in records}) == len(records))
    martinotti = next((r for r in records if r['entity_id'] == 'judge-entity-85b06ec4ad087308b3f7d0f4'), None)
    check('martinotti_assignments_via_cl_person_8598', bool(martinotti and martinotti['assignments'] and martinotti['assignments']['join']['cl_person_id'] == '8598'))
    found = set()
    forbidden_keys(records, found)
    check('no_rate_prediction_or_ranking_fields', not found, sorted(found))

    def dump(path, rows):
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + '\n')

    dump(OUT / 'evidence.jsonl', records)
    dump(OUT / 'unresolved.jsonl', unresolved)
    reasons = Counter((r['kind'], r['reason_code']) for r in unresolved)
    counts = {
        'entities_total': len(overlay_rows), 'entities_with_any_block': len(records),
        'entities_with_cjra': sum(1 for r in records if r['cjra']), 'entities_with_assignments': sum(1 for r in records if r['assignments']),
        'entities_with_education': sum(1 for r in records if r['education']), 'entities_with_positions': sum(1 for r in records if r['positions']),
        'entities_with_all_four_blocks': sum(1 for r in records if len(r['blocks']) == 4),
        'entities_with_cjra_in_two_or_more_courts': sum(1 for r in records if r['cjra'] and r['cjra']['other_courts']),
        'entities_with_cjra_without_cl_bridge': sum(1 for r in records if r['cjra'] and not r['ids']['cl_person_id']),
        'cjra_rows_total': cjra_total, 'cjra_rows_attached': len(attached_rows), 'cjra_rows_unresolved': len(unresolved_rows),
        'cjra_printed_judges_unresolved': sum(1 for r in unresolved if r['kind'] == 'cjra'),
        'sw_bulk_matters_total': matters_total, 'sw_bulk_judges_total': len(read_gz('judges')),
        'sw_bulk_judges_unresolved': sum(1 for r in unresolved if r['kind'] == 'sw_bulk_judge'),
        'sw_bulk_matter_links_attached': sum(r['assignments']['matters_for_this_judge'] for r in records if r['assignments']),
        'education_rows': sum(len(r['education']) for r in records), 'position_rows': sum(len(r['positions']) for r in records),
        'unresolved_rows': len(unresolved),
    }
    agreement = Counter(block['match_basis']['given_name_agreement'] for r in records if r['cjra'] for block in [r['cjra']] + r['cjra']['other_courts'])
    for kind, number in sorted(agreement.items()):
        counts['cjra_match_given_name:' + kind] = number
    for (kind, code), number in sorted(reasons.items()):
        counts['unresolved:%s:%s' % (kind, code)] = number
    passed = all(c['passed'] for c in checks)
    validation = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': passed,
        'validated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'data_files': [{'path': 'evidence.jsonl', 'sha256': sha256_file(OUT / 'evidence.jsonl'), 'rows': len(records)},
                       {'path': 'unresolved.jsonl', 'sha256': sha256_file(OUT / 'unresolved.jsonl'), 'rows': len(unresolved)}],
        'counts': counts, 'checks': checks,
        'qualification': ('Evidence blocks for saved judge entities from local data only: CJRA per-judge counts as printed by the publisher as of '
                          '2026-03-31 (attached only on surname + given-name initial(s) + same FJC district court, exactly one entity), matters in a '
                          'saved firm-focused docket dataset (release built %s, %s matters; joined only by CourtListener person id), and education / '
                          'positions as recorded by CourtListener (bulk snapshot %s). Not a complete record of any judge; no rates, predictions or '
                          'rankings; CJRA name matches are rule-based and not independently verified.') % (str(manifest.get('built_at'))[:10], format(matters_total, ','), snapshot),
        'license_ref': 'CJRA tables: U.S. government work (uscourts.gov); FJC export: U.S. government work; CourtListener bulk data: Free Law Project terms (see sources/courtlistener_people_20260918/README.md); SW-BULK release: internal dataset built from CourtListener/RECAP sources',
        'inputs': inputs,
    }
    (OUT / 'validation.json').write_text(json.dumps(validation, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'failed_checks': [c for c in checks if not c['passed']]}, indent=1))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
