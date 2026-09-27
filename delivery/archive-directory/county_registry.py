"""Read-only, hash-gated historical KY/TX county supplement; no person merging."""
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/county_registry_integration_20260919'


def _load(folder=DATA):
    """Reject an incomplete or altered publication. No external files are served."""
    gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    payload = (folder / 'counties.json').read_bytes()
    if (gate.get('status') != 'passed' or gate.get('ready') is not True
            or hashlib.sha256(payload).hexdigest() != gate.get('counties_sha256')):
        raise ValueError('County supplement failed its publication hash gate')
    rows = json.loads(payload)
    if len(rows) != gate.get('county_count') or any(k != v.get('geoid') for k, v in rows.items()):
        raise ValueError('County supplement identity mismatch')
    return rows


def county(geoid):
    empty = {'available': False, 'geoid': str(geoid), 'county': None, 'state': None,
             'source_as_of': None, 'saved_at': None,
             'counts': {'courts': 0, 'clerks': 0, 'judge_seats': 0},
             'courts': [], 'clerks': [], 'links': [], 'limitations': [], 'source': None}
    if not isinstance(geoid, str) or not re.fullmatch(r'\d{5}', geoid):
        return empty
    try:
        row = _load().get(geoid)
    except (OSError, ValueError, TypeError, KeyError):
        empty['limitations'] = ['County registry supplement is unavailable or awaiting validation.']
        return empty
    return copy.deepcopy(row) if row else empty


def covered_geoids():
    """Only verified county IDs may participate in live inventory filters."""
    try:
        return tuple(key for key in _load() if isinstance(key, str) and re.fullmatch(r'\d{5}', key))
    except (OSError, ValueError, TypeError, KeyError):
        return ()
