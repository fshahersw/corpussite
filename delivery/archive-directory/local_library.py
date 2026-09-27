"""Read-only, manifest-allowlisted local library previews for the personal MVP."""
from functools import lru_cache
from pathlib import Path
import copy
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/local_library_presentation_20260918'
_ID = re.compile(r'^[a-zA-Z0-9_-]{1,120}$')


@lru_cache(maxsize=16)
def _load(name):
    path = DATA / name
    if not path.is_file():
        return []
    with path.open(encoding='utf8') as fh:
        if name.endswith('.jsonl'):
            return [json.loads(line) for line in fh if line.strip()]
        return json.load(fh)


def collections():
    return copy.deepcopy(_load('collections.json'))


def _value(params, name, default=''):
    value = (params or {}).get(name, default)
    return value[0] if isinstance(value, (list, tuple)) and value else value


def collection(identifier, params=None):
    """Return a human-readable card plus bounded previews, never an arbitrary file."""
    card = next((r for r in _load('collections.json') if r['id'] == identifier), None)
    if not card or not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        return None
    query = str(_value(params, 'q')).strip().casefold()[:300]
    family = str(_value(params, 'family')).strip().casefold()
    kind = str(_value(params, 'kind')).strip().casefold()
    try:
        size = min(50, max(1, int(_value(params, 'page_size', '20'))))
        page = max(1, int(_value(params, 'page', '1')))
    except (ValueError, TypeError):
        size, page = 20, 1
    items = _load(identifier + '.jsonl')
    if query:
        items = [r for r in items if query in ' '.join(str(r.get(k) or '') for k in
                 ('title', 'description', 'excerpt', 'court_label', 'state', 'resource_kind')).casefold()]
    if family:
        items = [r for r in items if r.get('registry_family', '').casefold() == family]
    if kind:
        items = [r for r in items if r.get('resource_kind', '').casefold() == kind]
    result = copy.deepcopy(card)
    result.update(items=copy.deepcopy(items[(page - 1) * size:page * size]), total=len(items), page=page, page_size=size)
    return result


def county_visual(geoid):
    if not isinstance(geoid, str) or not re.fullmatch(r'\d{5}', geoid):
        return None
    row = next((r for r in _load('county_visuals.json') if r['geoid'] == geoid), None)
    return copy.deepcopy(row) if row else None


@lru_cache(maxsize=256)
def _verified(path_string, expected_hash, size, modified_ns):
    with Path(path_string).open('rb') as fh:
        return hashlib.file_digest(fh, 'sha256').hexdigest() == expected_hash


def asset(identifier):
    """Return (Path, MIME, attachment) only for exact IDs in the frozen allowlist.

    Paths are never accepted from callers. Changed/missing/symlink-retargeted files
    fail closed. Images are copied into the workspace; selected PDFs remain at
    their explicitly authorized, read-only local collection paths.
    """
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        return None
    record = next((r for r in _load('assets.json') if r['id'] == identifier), None)
    if not record:
        return None
    try:
        declared = Path(record['path'])
        path = declared.resolve(strict=True)
        root = Path(record['allowed_root']).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(root) or path != declared.absolute():
            return None
        stat = path.stat()
        if stat.st_size != record['bytes'] or not _verified(str(path), record['sha256'], stat.st_size, stat.st_mtime_ns):
            return None
        return path, record['mime'], bool(record.get('attachment'))
    except (OSError, KeyError, ValueError):
        return None
