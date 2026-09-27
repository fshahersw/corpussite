#!/usr/bin/env python3
"""One-off, already applied (2026-09-19): folds the native-id alias links into sources/jpml_mdl_20260919/build.py.
Kept as a record of exactly what was changed there. It refuses to run twice (every anchor must occur exactly once)."""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'jpml_mdl_20260919' / 'build.py'
raw = p.read_bytes()
crlf = b'\r\n' in raw
s = raw.decode('utf-8').replace('\r\n', '\n')
if 'NATIVE_BRIDGE' in s:
    raise SystemExit('already applied')


def rep(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:70])
    s = s.replace(old, new)


rep("MDL3080 = ROOT / 'sources/local_library_presentation_20260918/mdl-3080.jsonl'\n",
    "MDL3080 = ROOT / 'sources/local_library_presentation_20260918/mdl-3080.jsonl'\n"
    "ALIASES = ROOT / 'sources/judge_aliases_20260919'  # printed-name aliases resolved by native ids (CourtListener person == FJC jid bridge)\n"
    "NATIVE_BRIDGE = 'cl_person_native_bridge'\n"
    "DESIGNATION = 'sitting_by_designation_or_intercircuit_assignment'\n")

ALIAS_BLOCK = '''# ----------------------------------------------------------------------------- native-id aliases (optional layer)
def load_aliases():
    """Alias rows of sources/judge_aliases_20260919, fail-closed: validation passed + file hash + native-id evidence on every row."""
    try:
        validation = json.loads((ALIASES / 'validation.json').read_text(encoding='utf-8'))
        data = (ALIASES / 'aliases.jsonl').read_bytes()
    except (OSError, ValueError):
        return [], None
    entry = next((f for f in validation.get('data_files') or [] if f.get('path') == 'aliases.jsonl'), None)
    if validation.get('status') != 'passed' or validation.get('ready') is not True or not entry or entry.get('sha256') != sha256_bytes(data):
        return [], None
    rows = [json.loads(l) for l in data.decode('utf-8').splitlines() if l.strip()]
    for r in rows:
        ev = r.get('evidence') or {}
        if not (str(ev.get('cl_person_id') or '').isdigit() and str(ev.get('fjc_jid') or '').isdigit() and str(ev.get('fjc_nid') or '').isdigit()
                and ev.get('surname_agrees') is True and ev.get('dockets')):
            return [], None
        if ev.get('court_agrees') is not True and r.get('relation') != DESIGNATION:
            return [], None
    return rows, {'path': 'sources/judge_aliases_20260919/aliases.jsonl', 'sha256': sha256_bytes(data)}


def apply_aliases(links, unresolved, alias_rows, judge_keys, court_map, fjc_names, nid_to_entities, ui):
    """Resolve still-unresolved printed names through native-id aliases. A key is resolved only when EVERY one of its MDLs
    has its own master-docket evidence in the alias row and the entity is a UI entity of that FJC nid. Never by name."""
    by_key = {(r.get('alias'), r.get('district_code')): r for r in alias_rows}
    kept = []
    for u in unresolved:
        key = (u.get('judge_name_as_printed'), u.get('district_code'))
        r = by_key.get(key)
        ev = (r or {}).get('evidence') or {}
        nid = str(ev.get('fjc_nid') or '')
        ok = (r is not None and u.get('kind') == 'judge' and key[0]
              and set('mdl:%d' % n for n in judge_keys.get(key, [])) <= set(r.get('mdls') or [])
              and r.get('entity_id') in ui and r.get('entity_id') in nid_to_entities.get(nid, ()) and nid in fjc_names)
        if not ok:
            kept.append(u)
            continue
        link = {'entity_id': r['entity_id'], 'display_name': ui[r['entity_id']]['name'], 'fjc_nid': nid, 'fjc_name': fjc_names[nid]['name'],
                'fjc_court': (court_map.get(key[1]) or {}).get('fjc_court_name'), 'match_kind': NATIVE_BRIDGE, 'basis': NATIVE_BRIDGE,
                'relation': r.get('relation'), 'cl_person_id': int(ev['cl_person_id']), 'fjc_jid': str(ev['fjc_jid']),
                'entity_fjc_courts': ev.get('entity_fjc_courts'), 'court_agrees': ev.get('court_agrees'), 'surname_agrees': ev.get('surname_agrees'),
                'first_initial_agrees': ev.get('first_initial_agrees'), 'alias_basis': r.get('basis'),
                'dockets': {d['mdl']: {'docket_id': d.get('docket_id'), 'docket_number': d.get('docket_number'), 'receipt_file': d.get('receipt_file'),
                                       'receipt_sha256': d.get('receipt_sha256')} for d in ev.get('dockets') or []}}
        if link['relation'] == DESIGNATION:
            link['relation_note'] = 'FJC court of this judge differs from the transferee court; kept only as sitting by designation or intercircuit assignment.'
        links[key] = link
    return links, kept


def judge_link_public(link, num):
    out = {'entity_id': link['entity_id'], 'display_name': link['display_name'], 'basis': link.get('basis') or 'jpml_name_court', 'match_kind': link['match_kind'],
           'fjc_nid': link['fjc_nid'], 'fjc_name': link['fjc_name'], 'fjc_court': link['fjc_court']}
    if link.get('basis') == NATIVE_BRIDGE:
        docket = (link.get('dockets') or {}).get('mdl:%d' % num) or {}
        out.update({'relation': link.get('relation'), 'cl_person_id': link.get('cl_person_id'), 'fjc_jid': link.get('fjc_jid'),
                    'entity_fjc_courts': link.get('entity_fjc_courts'), 'court_agrees': link.get('court_agrees'), 'surname_agrees': link.get('surname_agrees'),
                    'first_initial_agrees': link.get('first_initial_agrees'), 'alias_basis': link.get('alias_basis'),
                    'cl_docket_id': docket.get('docket_id'), 'connector_file': docket.get('receipt_file'), 'connector_file_sha256': docket.get('receipt_sha256'),
                    'label': 'native-id bridge from a saved CourtListener connector response, not original HTTP bytes. The printed name was not used to make the link.'})
        if link.get('relation_note'):
            out['relation_note'] = link['relation_note']
    return out


# ----------------------------------------------------------------------------- connector responses
'''
rep("# ----------------------------------------------------------------------------- connector responses\n", ALIAS_BLOCK)

rep("    links, unresolved = join_judges(judge_keys, court_map, fjc_names, fjc_appts, nid_to_entities, ui)\n",
    "    links, unresolved = join_judges(judge_keys, court_map, fjc_names, fjc_appts, nid_to_entities, ui)\n"
    "    name_join_unresolved = len(unresolved)\n"
    "    alias_rows, alias_input = load_aliases()\n"
    "    links, unresolved = apply_aliases(links, unresolved, alias_rows, judge_keys, court_map, fjc_names, nid_to_entities, ui)\n")

rep("            'judge_links': ([{'entity_id': link['entity_id'], 'display_name': link['display_name'], 'basis': 'jpml_name_court', 'match_kind': link['match_kind'],\n"
    "                              'fjc_nid': link['fjc_nid'], 'fjc_name': link['fjc_name'], 'fjc_court': link['fjc_court']}] if link else []),\n",
    "            'judge_links': ([judge_link_public(link, num)] if link else []),\n")

rep("            link_nid = row['judge_links'][0]['fjc_nid'] if row['judge_links'] else None\n            if not link_nid:\n",
    "            name_links = [l for l in row['judge_links'] if l.get('basis') == 'jpml_name_court']\n"
    "            link_nid = name_links[0]['fjc_nid'] if name_links else None\n"
    "            if row['judge_links'] and not name_links:\n"
    "                agree = 'judge_link_is_the_native_bridge_itself'  # nothing independent to compare, so no agreement is claimed\n"
    "            elif not link_nid:\n")

rep("            edges.append({'from': src, 'to': {'type': 'judge_entity', 'id': 'judge_entity:%s' % jl['entity_id']}, 'relation': 'transferee_judge',\n"
    "                          'basis': 'jpml_name_court', 'evidence': {'judge_name_as_printed': row['transferee_judge']['name_as_printed'], 'district_code': row['district_code'],\n"
    "                          'fjc_nid': jl['fjc_nid'], 'fjc_court': jl['fjc_court'], 'match_kind': jl['match_kind'], 'document_id': row['reports'][0]['document_id'], 'as_of': row['temporal']['source_as_of']}})\n",
    "            evidence = {'judge_name_as_printed': row['transferee_judge']['name_as_printed'], 'district_code': row['district_code'],\n"
    "                        'fjc_nid': jl['fjc_nid'], 'fjc_court': jl['fjc_court'], 'match_kind': jl['match_kind'], 'document_id': row['reports'][0]['document_id'], 'as_of': row['temporal']['source_as_of']}\n"
    "            if jl['basis'] == NATIVE_BRIDGE:\n"
    "                evidence.update({k: jl.get(k) for k in ('cl_person_id', 'fjc_jid', 'entity_fjc_courts', 'court_agrees', 'surname_agrees', 'first_initial_agrees',\n"
    "                                                        'cl_docket_id', 'connector_file', 'connector_file_sha256')})\n"
    "            edges.append({'from': src, 'to': {'type': 'judge_entity', 'id': 'judge_entity:%s' % jl['entity_id']},\n"
    "                          'relation': jl.get('relation') or 'transferee_judge', 'basis': jl['basis'], 'evidence': evidence})\n")

rep("                        'match_kinds': dict(Counter(l['match_kind'] for l in links.values())), 'unresolved_reasons': dict(Counter(u['reason'] for u in unresolved))}\n",
    "                        'match_kinds': dict(Counter(l['match_kind'] for l in links.values())), 'unresolved_reasons': dict(Counter(u['reason'] for u in unresolved)),\n"
    "                        'unresolved_keys_after_name_join_only': name_join_unresolved, 'resolved_by_native_bridge_keys': sum(1 for l in links.values() if l.get('basis') == NATIVE_BRIDGE),\n"
    "                        'resolved_by_native_bridge_mdls': sum(1 for r in mdls.values() if any(l['basis'] == NATIVE_BRIDGE for l in r['judge_links'])),\n"
    "                        'alias_layer_used': bool(alias_input)}\n")

rep("    for d in conn_files:\n        inputs.append({'path': 'sources/jpml_mdl_20260919/' + d['_file'], 'sha256': d['_sha256']})\n",
    "    for d in conn_files:\n        inputs.append({'path': 'sources/jpml_mdl_20260919/' + d['_file'], 'sha256': d['_sha256']})\n"
    "    if alias_input:\n        inputs.append(alias_input)\n")

rep("'not independently verified docket counts. Judge links are conservative name+court joins to FJC appointments (see match_kind); '",
    "'not independently verified docket counts. Judge links are conservative name+court joins to FJC appointments (see match_kind); where the printed name '\n"
    "                          'differs from the FJC name a link is made only by native ids (match_kind cl_person_native_bridge: the CourtListener person assigned to the master '\n"
    "                          'docket is the person the profile is bridged to by FJC ids), never by name; '")

out = s.replace('\n', '\r\n') if crlf else s
p.write_bytes(out.encode('utf-8'))
print('patched; crlf =', crlf)
