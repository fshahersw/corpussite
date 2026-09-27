#!/usr/bin/env python3
"""Judge aliases: names as printed in the JPML pending-MDL report, resolved to saved judge entities by native ids only.

Offline and re-runnable. Reads only saved files:
  - sources/jpml_mdl_20260919/mdls.jsonl (printed transferee judge, district, master docket; hash-gated by its validation.json)
  - saved CourtListener MCP docket-search responses: sources/jpml_mdl_20260919/connector/ and ./receipts/
  - sources/judge_structured_20260919/overlay.jsonl (entity -> FJC nid/jid -> CourtListener person id, native-id bridge; hash-gated)
Rule: an alias exists only when the CourtListener person assigned to the MDL master docket equals the cl_person_id an entity is
bridged to (FJC jid == CourtListener people.fjc_id) AND the surname agrees. Names alone never create a link.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
JPML = ROOT / 'sources/jpml_mdl_20260919'
STRUCT = ROOT / 'sources/judge_structured_20260919'
CL_DB = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
RECEIPTS = HERE / 'receipts'
SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}
DESIGNATION = 'sitting_by_designation_or_intercircuit_assignment'
MAX_CONNECTOR_REQUESTS = 12


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def canonical_sha(obj) -> str:
    return sha_bytes(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))


def name_tokens(name) -> list:
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(name or '')).casefold() if not unicodedata.combining(c))
    return [t for t in re.sub(r'[^0-9a-z]+', ' ', text).split() if t and t not in SUFFIXES]


def compare_names(printed, entity_name) -> dict:
    """Corroboration only (never creates a link): surname and first initial of the printed name vs the FJC-published name."""
    p, e = name_tokens(printed), name_tokens(entity_name)
    if not p or not e:
        return {'surname_agrees': False, 'first_initial_agrees': False, 'printed_given_name_found_in_entity_given_names': False}
    given = e[:-1]
    first = p[0]
    found = any((g.startswith(first) if len(first) == 1 else (first in g or g in first)) for g in given)
    return {'surname_agrees': p[-1] == e[-1], 'first_initial_agrees': bool(given) and p[0][0] == given[0][0],
            'printed_given_name_found_in_entity_given_names': found}


def norm_docket(number):
    if not number:
        return None
    parts = str(number).lower().split('-')
    if len(parts) < 3:
        return str(number).lower()
    seq = re.sub(r'^0+', '', re.match(r'\d*', parts[2]).group(0)) or '0'
    return '%s-%s-%s' % (parts[0], parts[1], seq)


def gated_file(folder: Path, name: str) -> Path:
    """Return folder/name only when that folder's validation.json passed and the file re-hashes to the recorded value."""
    validation = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    if validation.get('status') != 'passed' or validation.get('ready') is not True:
        raise SystemExit('input layer not ready: %s' % folder.name)
    entry = next((f for f in validation.get('data_files') or [] if f.get('path') == name), None)
    if not entry or sha_file(folder / name) != entry.get('sha256'):
        raise SystemExit('input hash mismatch: %s/%s' % (folder.name, name))
    return folder / name


def load_bridge(path: Path) -> dict:
    """cl_person_id (str) -> [entity dicts] from the structured overlay; only rows whose bridge_status is linked_native_id."""
    out = defaultdict(list)
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            o = json.loads(line)
            ids = o.get('ids') or {}
            if ids.get('bridge_status') != 'linked_native_id' or not ids.get('cl_person_id') or not ids.get('fjc_jid') or not ids.get('fjc_nid'):
                continue
            fjc = o.get('fjc') or {}
            out[str(ids['cl_person_id'])].append({
                'entity_id': o['entity_id'], 'fjc_jid': str(ids['fjc_jid']), 'fjc_nid': str(ids['fjc_nid']),
                'fjc_name': fjc.get('name_as_published') or o.get('name'),
                'fjc_courts': sorted({a.get('court') for a in fjc.get('appointments') or [] if a.get('court')})})
    return out


def load_receipts(folders) -> tuple:
    """mdl_number -> receipt (later folders win). Every receipt's response is re-hashed when it states a response_sha256."""
    by_mdl, problems, registry = {}, [], []
    for folder, prefix in folders:
        for p in sorted(folder.glob('search_mdl_*.json')):
            d = json.loads(p.read_text(encoding='utf-8'))
            if d.get('exploratory') or d.get('tool') != 'search' or not isinstance(d.get('mdl_number'), int):
                continue
            stated = d.get('response_sha256')
            actual = canonical_sha(d.get('response'))
            if stated and stated != actual:
                problems.append(prefix + p.name)
                continue
            rec = {'file': prefix + p.name, 'file_sha256': sha_file(p), 'response_sha256': actual, 'tool': d.get('tool'), 'arguments': d.get('arguments'),
                   'requested_after_utc': d.get('requested_after_utc'), 'saved_at_utc': d.get('saved_at_utc'), 'response': d.get('response') or {}}
            by_mdl[d['mdl_number']] = rec
            registry.append({k: rec[k] for k in ('file', 'file_sha256', 'response_sha256', 'tool', 'arguments', 'requested_after_utc', 'saved_at_utc')})
    return by_mdl, problems, registry


def master_docket_hit(receipt, row):
    results = (receipt.get('response') or {}).get('results') or []
    want = norm_docket(row.get('master_docket'))
    hits = [r for r in results if isinstance(r, dict) and r.get('court_id') == row.get('cl_court_id') and norm_docket(r.get('docketNumber')) == want]
    return hits[0] if len(hits) == 1 else None


def resolve(groups, receipts, bridge, cl_fjc_ids):
    aliases, unresolved = [], []
    for (printed, code), rows in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        base = {'alias': printed, 'district_code': code, 'mdls': ['mdl:%d' % r['mdl_number'] for r in rows], 'kind': 'judge_alias'}
        dockets = []
        for r in rows:
            rec = receipts.get(r['mdl_number'])
            hit = master_docket_hit(rec, r) if rec else None
            dockets.append({'mdl': 'mdl:%d' % r['mdl_number'], 'master_docket': r.get('master_docket'), 'cl_court_id': r.get('cl_court_id'),
                            'docket_id': hit.get('docket_id') if hit else None, 'docket_number': hit.get('docketNumber') if hit else None,
                            'assigned_to_id': hit.get('assigned_to_id') if hit else None, 'assigned_to_string_as_recorded': hit.get('assignedTo') if hit else None,
                            'receipt_file': rec['file'] if rec else None, 'receipt_sha256': rec['file_sha256'] if rec else None,
                            'response_sha256': rec['response_sha256'] if rec else None, 'requested_after_utc': rec['requested_after_utc'] if rec else None,
                            'label': 'connector response (CourtListener MCP), not original HTTP bytes'})
        with_person = [d for d in dockets if d['assigned_to_id'] is not None]
        if not any(d['receipt_file'] for d in dockets):
            unresolved.append(dict(base, reason='no_saved_courtlistener_docket_response', dockets=dockets)); continue
        if not any(d['docket_id'] for d in dockets):
            unresolved.append(dict(base, reason='master_docket_not_found_in_saved_response', dockets=dockets)); continue
        if not with_person:
            unresolved.append(dict(base, reason='cl_docket_has_no_assigned_to_id', dockets=dockets,
                                   detail='CourtListener records the assigned judge only as a text string on this docket; a string is a name, not a native id, so no link is made.')); continue
        persons = sorted({int(d['assigned_to_id']) for d in with_person})
        if len(persons) != 1:
            unresolved.append(dict(base, reason='cl_assigned_persons_differ_across_mdls', cl_person_ids=persons, dockets=dockets)); continue
        pid = persons[0]
        ents = bridge.get(str(pid), [])
        if len(ents) != 1:
            unresolved.append(dict(base, reason='cl_person_has_no_native_fjc_bridge' if not ents else 'cl_person_bridges_to_multiple_entities', cl_person_id=pid,
                                   cl_people_fjc_id_in_saved_snapshot=cl_fjc_ids.get(pid), entity_ids=[e['entity_id'] for e in ents], dockets=dockets,
                                   detail='The saved CourtListener people snapshot gives this person no FJC id that equals an FJC jid of a saved judge entity, so the native-id bridge cannot be made.')); continue
        ent = ents[0]
        cmp_ = compare_names(printed, ent['fjc_name'])
        if not cmp_['surname_agrees']:
            unresolved.append(dict(base, reason='cl_assigned_person_surname_differs_from_printed_name', cl_person_id=pid, cl_person_entity_id=ent['entity_id'],
                                   cl_person_fjc_name=ent['fjc_name'], dockets=dockets,
                                   detail='The CourtListener master docket is assigned to a different person than the judge the JPML report prints; no link is made.')); continue
        jpml_court = rows[0].get('fjc_court_name')
        court_agrees = bool(jpml_court) and jpml_court in ent['fjc_courts']
        used = [d for d in with_person]
        mdl_list = ', '.join(d['mdl'].split(':')[1] for d in used)
        aliases.append({
            'alias': printed, 'entity_id': ent['entity_id'], 'display_name': ent['fjc_name'], 'district_code': code,
            'source': 'JPML pending MDL report dated %s' % rows[0]['as_of'],
            'basis': 'native-id bridge: CourtListener person %d assigned to MDL %s master docket == FJC jid %s' % (pid, mdl_list, ent['fjc_jid']),
            'relation': 'transferee_judge' if court_agrees else DESIGNATION,
            'relation_note': None if court_agrees else 'The entity\'s FJC court differs from the transferee court printed by the JPML; kept only as sitting by designation or intercircuit assignment. The profile\'s court is not changed.',
            'mdls': [d['mdl'] for d in used],
            'evidence': {'cl_person_id': pid, 'fjc_jid': ent['fjc_jid'], 'fjc_nid': ent['fjc_nid'], 'fjc_name_as_published': ent['fjc_name'],
                         'entity_fjc_courts': ent['fjc_courts'],
                         'jpml_printed_court': {'district_code': code, 'fjc_court_name': jpml_court, 'cl_court_id': rows[0].get('cl_court_id')},
                         'court_agrees': court_agrees, **cmp_, 'dockets': used,
                         'bridge': 'judge entity -> FJC nid -> FJC jid == CourtListener people.fjc_id -> CourtListener person id (sources/judge_structured_20260919/overlay.jsonl)'},
            'temporal': {'captured_at': max((d['requested_after_utc'] or '') for d in used) or None, 'captured_at_basis': 'requested_after_utc of the saved CourtListener connector response(s)',
                         'source_as_of': rows[0]['as_of'], 'source_as_of_basis': 'printed Report Date of the JPML report that prints this name',
                         'published_at': None, 'published_at_basis': None, 'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None},
        })
    return aliases, unresolved


def main() -> int:
    mdls_path = gated_file(JPML, 'mdls.jsonl')
    overlay_path = gated_file(STRUCT, 'overlay.jsonl')
    groups = defaultdict(list)
    with mdls_path.open(encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            printed = (r.get('transferee_judge') or {}).get('name_as_printed')
            # Only stable fields are used, so this build gives the same aliases before and after the MDL registry folds them in.
            if not printed or any(l.get('basis') == 'jpml_name_court' for l in r.get('judge_links') or []):
                continue
            groups[(printed, r.get('district_code') or '')].append({'mdl_number': r['mdl_number'], 'master_docket': r.get('master_docket'), 'cl_court_id': r.get('cl_court_id'),
                                                                      'fjc_court_name': r.get('fjc_court_name'), 'as_of': (r.get('temporal') or {}).get('source_as_of')})
    receipts, bad_receipts, registry = load_receipts([(JPML / 'connector', 'sources/jpml_mdl_20260919/connector/'), (RECEIPTS, 'sources/judge_aliases_20260919/receipts/')])
    bridge = load_bridge(overlay_path)
    cl_fjc_ids = {}
    if CL_DB.is_file():
        con = sqlite3.connect(CL_DB.as_uri() + '?mode=ro', uri=True)
        try:
            cl_fjc_ids = {int(pid): (str(fid) if fid not in (None, '') else None) for pid, fid in con.execute('select id, fjc_id from people')}
        finally:
            con.close()
    aliases, unresolved = resolve(groups, receipts, bridge, cl_fjc_ids)

    own = sorted(RECEIPTS.glob('*.json')) if RECEIPTS.is_dir() else []
    checks = []

    def check(name, ok, detail=None):
        checks.append({'name': name, 'status': 'passed' if ok else 'failed', 'detail': detail})

    check('receipt_response_hashes_verified', not bad_receipts, bad_receipts or '%d saved docket-search responses re-hashed' % len(registry))
    check('connector_requests_within_budget', len(own) <= MAX_CONNECTOR_REQUESTS, '%d receipts in receipts/ (budget %d, includes the usage pre-check)' % (len(own), MAX_CONNECTOR_REQUESTS))
    check('every_alias_has_native_id_and_surname', all(a['evidence']['cl_person_id'] and a['evidence']['fjc_jid'] and a['evidence']['surname_agrees'] for a in aliases))
    check('other_court_links_carry_designation_relation', all(a['evidence']['court_agrees'] or a['relation'] == DESIGNATION for a in aliases))
    check('one_entity_per_printed_name_and_district', len({(a['alias'], a['district_code']) for a in aliases}) == len(aliases))
    check('every_candidate_accounted_for', len(aliases) + len(unresolved) == len(groups), {'candidates': len(groups), 'aliases': len(aliases), 'unresolved': len(unresolved)})
    rodgers = [a for a in aliases if a['alias'] == 'M. Casey Rodgers']
    check('rodgers_alias_resolves_to_saved_entity', len(rodgers) == 1 and rodgers[0]['entity_id'] == 'judge-entity-24222174cb13b7162bfcfcbc' and rodgers[0]['evidence']['cl_person_id'] == 2755)

    def write_jsonl(name, rows):
        payload = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows).encode('utf-8')
        (HERE / name).write_bytes(payload)
        return {'path': name, 'sha256': sha_bytes(payload), 'rows': len(rows)}

    files = [write_jsonl('aliases.jsonl', aliases), write_jsonl('unresolved.jsonl', unresolved)]
    ok = all(c['status'] == 'passed' for c in checks)
    inputs = [{'path': 'sources/jpml_mdl_20260919/mdls.jsonl', 'sha256': sha_file(mdls_path),
               'note': 'only stable fields are read (MDL number, printed judge, district, master docket, whether a name+court link exists)'},
              {'path': 'sources/judge_structured_20260919/overlay.jsonl', 'sha256': sha_file(overlay_path)}]
    inputs += [{'path': r['file'], 'sha256': r['file_sha256']} for r in registry]
    inputs += [{'path': 'sources/judge_aliases_20260919/receipts/' + p.name, 'sha256': sha_file(p)} for p in own if not p.name.startswith('search_mdl_')]
    validation = {
        'schema_version': '1', 'status': 'passed' if ok else 'failed', 'ready': ok,
        'validated_at': dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), 'data_files': files,
        'counts': {'candidate_printed_names': len(groups), 'aliases': len(aliases), 'alias_entities': len({a['entity_id'] for a in aliases}),
                   'alias_mdls': sum(len(a['mdls']) for a in aliases), 'unresolved': len(unresolved),
                   'unresolved_reasons': dict(Counter(u['reason'] for u in unresolved)), 'relations': dict(Counter(a['relation'] for a in aliases)),
                   'first_initial_disagrees': [a['alias'] for a in aliases if not a['evidence']['first_initial_agrees']],
                   'connector_receipts_in_this_folder': len(own), 'docket_search_responses_read': len(registry),
                   'source_as_of': sorted({a['temporal']['source_as_of'] for a in aliases if a['temporal']['source_as_of']})},
        'checks': checks,
        'qualification': ('Names exactly as printed for transferee judges in the JPML pending-MDL report, attached to a saved judge profile only when the CourtListener person '
                          'assigned to that MDL master docket is the same person the profile is bridged to by FJC ids, and the surname agrees. CourtListener data are saved '
                          'connector responses (not original HTTP bytes) as of 2026-09-19; printed names with no native-id evidence stay unresolved and are listed, not guessed.'),
        'license_ref': 'JPML report: U.S. Government work. CourtListener data: Free Law Project (CC0/public domain dedication for bulk data; API responses via the user\'s connector).',
        'inputs': inputs,
    }
    (HERE / 'validation.json').write_bytes(json.dumps(validation, ensure_ascii=False, indent=1).encode('utf-8'))
    print('status', validation['status'], 'aliases', len(aliases), 'unresolved', len(unresolved))
    for a in aliases:
        print(' +', a['alias'], '->', a['entity_id'], a['display_name'], a['relation'], a['mdls'])
    for u in unresolved:
        print(' -', u['alias'], u['district_code'], u['reason'])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
