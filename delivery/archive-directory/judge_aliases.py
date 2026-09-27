#!/usr/bin/env python3
"""Judge name aliases (names as printed by a source) resolved to saved judge entities by native ids only.

Layer: sources/judge_aliases_20260919 (aliases.jsonl + validation.json, uniform envelope). Fail-closed: the layer is used
only when validation.json says status == "passed" and ready is true and every data file re-hashes to the recorded SHA-256,
and every alias row carries native-id evidence (CourtListener person id bridged to an FJC jid/nid, agreeing surname).
A row that is a name-only guess closes the whole layer. Public dicts carry no filesystem paths.
"""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'sources/judge_aliases_20260919'
DATA_FILES = ('aliases.jsonl', 'unresolved.jsonl')
RELATIONS = ('transferee_judge', 'sitting_by_designation_or_intercircuit_assignment')
DESIGNATION = 'sitting_by_designation_or_intercircuit_assignment'
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def tokens(value) -> list:
    """Casefolded, accent-free, punctuation-insensitive tokens ('M. Casey Rodgers' -> ['m', 'casey', 'rodgers'])."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')).casefold() if not unicodedata.combining(c))
    return [t for t in re.sub(r'[^0-9a-z]+', ' ', text).split() if t]


def _digits(value) -> bool:
    return isinstance(value, (int, str)) and not isinstance(value, bool) and str(value).isdigit()


def row_problem(row):
    """Return None when the alias row carries native-id evidence, else a short reason."""
    if not isinstance(row, dict) or not tokens(row.get('alias')):
        return 'alias_missing'
    if not str(row.get('entity_id') or '').startswith('judge-entity-'):
        return 'entity_id_missing'
    if not str(row.get('basis') or '').startswith('native-id bridge'):
        return 'alias_without_native_id_basis'
    ev = row.get('evidence')
    if not isinstance(ev, dict) or not (_digits(ev.get('cl_person_id')) and _digits(ev.get('fjc_jid')) and _digits(ev.get('fjc_nid'))):
        return 'alias_without_native_id_evidence'
    dockets = ev.get('dockets')
    if not isinstance(dockets, list) or not dockets:
        return 'alias_without_native_id_docket_evidence'
    for d in dockets:
        if not isinstance(d, dict) or str(d.get('assigned_to_id')) != str(ev['cl_person_id']) or not HEX64.match(str(d.get('receipt_sha256') or '')):
            return 'alias_without_native_id_docket_evidence'
    if ev.get('surname_agrees') is not True:
        return 'alias_surname_not_corroborated_beside_native_id'
    if row.get('relation') not in RELATIONS:
        return 'alias_relation_unknown'
    if ev.get('court_agrees') is not True and row.get('relation') != DESIGNATION:
        return 'alias_other_court_without_designation_relation_native_id'
    return None


def _load(folder=None):
    folder = Path(folder) if folder else FOLDER
    try:
        validation = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'validation_missing_or_unreadable'
    if not isinstance(validation, dict) or validation.get('status') != 'passed' or validation.get('ready') is not True:
        return None, 'validation_not_passed'
    declared = {f.get('path'): f for f in validation.get('data_files') or [] if isinstance(f, dict)}
    seen = {}
    for name in DATA_FILES:
        entry = declared.get(name)
        if not entry:
            return None, 'data_file_not_declared:' + name
        try:
            data = (folder / name).read_bytes()
        except OSError:
            return None, 'data_file_missing:' + name
        if hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            return None, 'data_file_hash_mismatch:' + name
        seen[name] = data
    try:
        rows = [json.loads(line) for line in seen['aliases.jsonl'].decode('utf-8').splitlines() if line.strip()]
    except ValueError:
        return None, 'aliases_unreadable'
    for row in rows:
        problem = row_problem(row)
        if problem:
            return None, problem
    return {'rows': rows, 'validated_at': validation.get('validated_at'), 'qualification': validation.get('qualification')}, None


def status(folder=None) -> dict:
    state, reason = _load(folder)
    if not state:
        return {'available': False, 'reason': reason, 'aliases': 0}
    return {'available': True, 'reason': None, 'aliases': len(state['rows']), 'entities': len({r['entity_id'] for r in state['rows']}),
            'validated_at': state['validated_at'], 'qualification': state['qualification']}


def aliases_for(entity_id, folder=None) -> list:
    """Printed-name aliases of one judge entity: [{alias, source, basis}] (empty when the layer is unavailable)."""
    state, _ = _load(folder)
    eid = str(entity_id or '')
    if not state or not eid:
        return []
    out, seen = [], set()
    for row in state['rows']:
        if row['entity_id'] == eid and row['alias'] not in seen:
            seen.add(row['alias'])
            out.append({'alias': row['alias'], 'source': row.get('source'), 'basis': row.get('basis')})
    return out


def entity_ids_for_query(q, folder=None) -> set:
    """Entity ids whose alias contains every query token (casefolded, punctuation-insensitive substring match per token)."""
    want = tokens(str(q or '')[:200])
    if not want:
        return set()
    state, _ = _load(folder)
    if not state:
        return set()
    out = set()
    for row in state['rows']:
        hay = ' '.join(tokens(row['alias']))
        if all(t in hay for t in want):
            out.add(row['entity_id'])
    return out
