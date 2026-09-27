"""Expose saved API documentation context; this adapter never calls an API."""
import copy
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

DATA = Path(__file__).resolve().parents[2] / 'reports/public_law_api_review_20260919'
QUALIFICATION = ('This classifies an observed API, bulk-data, feed or related reference. '
                 'Documentation review does not establish a callable endpoint, successful authentication or unrestricted access. '
                 'No live API query was performed.')


def _url(value):
    if not isinstance(value, str): return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def _load(folder=DATA):
    gate = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    payload = (folder / 'api_sources.jsonl').read_bytes()
    digest = gate.get('mapping_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError('API context mapping hash mismatch')
    rows = [json.loads(line) for line in payload.decode('utf-8-sig').splitlines() if line.strip()]
    result, urls = {}, set()
    for row in rows:
        ident, url = row.get('registry_id'), row.get('url')
        source = row.get('source_record')
        citation = row.get('source_citation')
        requirements = row.get('access_requirements')
        if (not isinstance(ident, str) or not ident or ident in result or not _url(url) or url in urls
                or not isinstance(source, dict) or source.get('id') != ident or source.get('url') != url
                or not isinstance(citation, dict) or citation.get('registry_id') != ident
                or not isinstance(requirements, dict) or not _url(requirements.get('source'))
                or not isinstance(requirements.get('status'), str) or not requirements['status']
                or not isinstance(row.get('reference_class'), str) or not row['reference_class']
                or row.get('live_api_query_performed') is not False):
            raise ValueError('API context identity or provenance mismatch')
        for key in ('method', 'note'):
            if requirements.get(key) is not None and not isinstance(requirements[key], str):
                raise ValueError('Invalid access requirement field')
        reviewed = row.get('requirements_reviewed_at')
        if reviewed is not None:
            if not isinstance(reviewed, str): raise ValueError('Invalid documentation review date')
            datetime.fromisoformat(reviewed.replace('Z', '+00:00'))
        result[ident] = row
        urls.add(url)
    counts = Counter(row['reference_class'] for row in rows)
    if (gate.get('mapped_references') != len(rows) or gate.get('classification_counts') != dict(counts)
            or gate.get('live_documentation_reviews') != sum(bool(row.get('requirements_reviewed_at')) for row in rows)
            or gate.get('api_calls_performed') != 0):
        raise ValueError('API review counts do not reconcile')
    for key in ('typed_api_rows', 'api_bulk_layer_rows'):
        if type(gate.get(key)) is not int or gate[key] < 0:
            raise ValueError('Invalid source taxonomy count')
    return gate, result


def detail(source_native_id, url):
    if not isinstance(source_native_id, str) or not isinstance(url, str): return None
    try:
        _, rows = _load()
        row = rows.get(source_native_id)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not row or row['url'] != url: return None
    req = row['access_requirements']
    return {'reference_class': row['reference_class'],
            'access_requirements': {key: copy.deepcopy(req.get(key)) for key in ('status', 'method', 'source', 'note')},
            'requirements_reviewed_at': row.get('requirements_reviewed_at'),
            'live_api_query_performed': False, 'qualification': QUALIFICATION}


def summary():
    try: gate, _ = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return {'ready': False, 'mapped_references': 0, 'live_documentation_reviews': 0,
                'typed_api_rows': 0, 'api_bulk_layer_rows': 0, 'api_calls_performed': 0}
    return {'ready': True, **{key: gate.get(key) for key in
            ('mapped_references', 'live_documentation_reviews', 'typed_api_rows', 'api_bulk_layer_rows', 'api_calls_performed', 'generated_at')},
            'qualification': (f"The source taxonomy contains {gate['typed_api_rows']} typed API entries and "
                              f"{gate['api_bulk_layer_rows']} API/bulk-tagged entries; these are separate measures from "
                              f"the {gate['mapped_references']} mapped API/bulk/feed and related references. "
                              f"{gate['live_documentation_reviews']} documentation reviews were performed; no live API queries were performed.")}
