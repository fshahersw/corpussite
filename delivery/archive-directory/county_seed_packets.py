"""Read-only, hash-gated status adapter for sources/county_seed_packets_20260919.

That supplement is Firecrawl frontier PREPARATION only (no network, no ingest):
reviewed candidate seeds for the county litigation collector
(sources/county_litigation_firecrawl_20260919), split into seeds_county.jsonl,
seeds_statewide.jsonl and rejected.jsonl, plus coverage_gain.json.

This module exposes exactly one function, `summary()`, which never raises and
returns `{"available": False, "reason": ...}` whenever validation.json is
missing, not `status == "passed"`/`ready == true`, or any registered data file
hash does not match. There is no listing function: this is a status module,
not a browsing surface.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/county_seed_packets_20260919'
REQUIRED = ('seeds_county.jsonl', 'seeds_statewide.jsonl', 'rejected.jsonl', 'coverage_gain.json')

_LOCK = threading.Lock()
_CACHE: dict = {}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(folder: Path):
    sig = []
    for name in ('validation.json',) + REQUIRED:
        try:
            st = (folder / name).stat()
        except OSError:
            return None
        sig.append((name, st.st_size, st.st_mtime_ns))
    return (str(folder),) + tuple(sig)


def _verify(folder: Path):
    """Return (state, None) when the gate is open, else (None, reason). Never raises."""
    try:
        gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'validation.json missing or unreadable'
    if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
        return None, 'supplement not published (status/ready gate closed)'
    files = gate.get('data_files')
    if not isinstance(files, list):
        return None, 'validation.json has no data_files list'
    base = folder.resolve()
    by_name = {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str):
            return None, 'malformed data_files entry'
        file = (folder / Path(entry['path']).name).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return None, 'data file outside the supplement or missing'
        try:
            data = file.read_bytes()
        except OSError:
            return None, 'data file unreadable'
        if _digest(data) != entry['sha256']:
            return None, 'hash mismatch: %s' % Path(entry['path']).name
        by_name[Path(entry['path']).name] = (data, entry)
    missing = [name for name in REQUIRED if name not in by_name]
    if missing:
        return None, 'data files not registered in validation.json: %s' % ', '.join(missing)

    def count_jsonl(name):
        return sum(1 for line in by_name[name][0].decode('utf-8').splitlines() if line.strip())

    try:
        county_rows = count_jsonl('seeds_county.jsonl')
        statewide_rows = count_jsonl('seeds_statewide.jsonl')
        rejected_rows = count_jsonl('rejected.jsonl')
        coverage = json.loads(by_name['coverage_gain.json'][0].decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None, 'a registered data file is not valid JSON/JSON lines'
    if not isinstance(coverage, dict) or 'totals' not in coverage:
        return None, 'coverage_gain.json missing expected shape'

    state = {
        'validated_at': gate.get('validated_at'),
        'counts': gate.get('counts') if isinstance(gate.get('counts'), dict) else {},
        'county_rows': county_rows,
        'statewide_rows': statewide_rows,
        'rejected_rows': rejected_rows,
        'coverage_totals': coverage.get('totals') or {},
        'states_with_new_county_seeds': sorted((coverage.get('by_state') or {}).keys()),
        'qualification': gate.get('qualification', ''),
        'license_ref': gate.get('license_ref', ''),
    }
    return state, None


def _load():
    folder = DATA
    sig = _signature(folder)
    with _LOCK:
        cached = _CACHE.get('state')
        if cached is not None and cached[0] == sig:
            return cached[1]
    state, reason = (None, 'supplement folder missing') if sig is None else _verify(folder)
    with _LOCK:
        _CACHE['state'] = (sig, (state, reason))
    return state, reason


def summary() -> dict:
    """Status only, never a listing. Never raises.

    {"available": False, "reason": str}
    or
    {"available": True, "county_seeds": int, "statewide_seeds": int,
     "rejected": int, "distinct_counties_seeded": int,
     "distinct_states_seeded_county": int, "distinct_states_seeded_statewide": int,
     "zero_coverage_counties_reached": int, "states_with_new_county_seeds": [...],
     "validated_at": str, "qualification": str}
    """
    try:
        state, reason = _load()
    except Exception:  # fail-closed: never raise out of a status call
        return {'available': False, 'reason': 'internal error reading the supplement'}
    if state is None:
        return {'available': False, 'reason': reason or 'unavailable'}
    counts = state['counts']
    totals = state['coverage_totals']
    return {
        'available': True,
        'county_seeds': state['county_rows'],
        'statewide_seeds': state['statewide_rows'],
        'rejected': state['rejected_rows'],
        'distinct_counties_seeded': counts.get('distinct_counties_seeded', totals.get('counties_with_new_seeds')),
        'distinct_states_seeded_county': counts.get('distinct_states_seeded_county'),
        'distinct_states_seeded_statewide': counts.get('distinct_states_seeded_statewide'),
        'zero_coverage_counties_reached': totals.get('zero_coverage_counties_reached'),
        'states_with_new_county_seeds': state['states_with_new_county_seeds'],
        'validated_at': state['validated_at'],
        'qualification': state['qualification'],
    }
