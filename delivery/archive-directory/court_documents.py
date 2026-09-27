"""Read-only, hash-gated adapter over sources/court_document_library_20260919/index.sqlite3.

Local personal-testing view; derived from a private firm dataset built from CourtListener/RECAP API
data; not for redistribution. Fails closed (returns the "not available" shape, never raises) when
validation.json is missing, not status=="passed"/ready, or the data-file hash does not match.
Generic view contract: listing(params), detail(id), original(file_id), for_court(court_id),
for_state(usps). Public dicts carry no filesystem paths.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/court_document_library_20260919'
DB_PATH = DATA / 'index.sqlite3'
VALIDATION_PATH = DATA / 'validation.json'
# The originals sit outside the project on the collecting machine; a transferred copy is looked for inside the project first.
_PROJECT_STAGING = ROOT / 'external/publiclaw_registry_v2/staging'
STAGING = _PROJECT_STAGING if _PROJECT_STAGING.is_dir() else Path('C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging')
ADAPTER = 'court_documents'

ID_RE = re.compile(r'[A-Za-z0-9_:.\-]{1,160}')
SHA_RE = re.compile(r'[0-9a-f]{64}')

DOC_TYPE_LABELS = {
    'court_form': 'Court form',
    'local_rule': 'Local rule',
    'standing_or_general_order': 'Standing / general order',
    'fee_schedule': 'Fee schedule',
    'calendar_notice': 'Calendar / notice',
    'opinion': 'Opinion',
    'administrative_financial': 'Administrative / financial',
    'other_unknown': 'Other / unclassified',
}
MANIFEST_LABELS = {
    'court_expansion_file_download_v2_4_2026-08-20': 'Court expansion download (2026-08-20 snapshot)',
    'state_trial_court_acquisition_v1_2026-08-20': 'State trial court acquisition (2026-08-20 snapshot)',
}
LINK_STATUS_LABELS = {
    'matched': 'Single court (exact host match)',
    'ambiguous': 'Multiple candidate courts (host shared)',
    'none': 'No court match',
}

COLUMNS = [
    {'key': 'document', 'label': 'Document'},
    {'key': 'doc_type', 'label': 'Type'},
    {'key': 'jurisdiction', 'label': 'Jurisdiction'},
    {'key': 'manifest', 'label': 'Manifest'},
    {'key': 'size', 'label': 'Size'},
    {'key': 'host', 'label': 'Source host'},
]

_CACHE = {}


def _gate():
    gate = json.loads(VALIDATION_PATH.read_bytes())
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('court document library validation is not passed/ready')
    data_files = {d.get('path'): d for d in gate.get('data_files', [])}
    entry = data_files.get('index.sqlite3')
    if not entry:
        raise ValueError('court document library validation has no index.sqlite3 entry')
    digest = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    if digest != entry.get('sha256'):
        raise ValueError('court document library hash gate failed')
    return gate, digest


def _connect():
    gate, digest = _gate()
    if digest not in _CACHE:
        _CACHE.clear()
        _CACHE[digest] = gate
    con = sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con, gate


def _one(params, name, default=''):
    value = (params or {}).get(name, default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return value.strip() if isinstance(value, str) else default


def _int(params, name, default, low, high):
    try:
        return max(low, min(high, int(_one(params, name, str(default)))))
    except ValueError:
        return default


def _options(con, column, labeller=None, where='', args=()):
    rows = con.execute('SELECT %s AS v, COUNT(*) AS n FROM documents %s GROUP BY %s ORDER BY n DESC' % (column, where, column), args).fetchall()
    return [{'value': r['v'], 'label': (labeller or (lambda x: x))(r['v']), 'count': r['n']} for r in rows if r['v']]


def _document_title(row):
    if row['title']:
        return row['title']
    return (row['filename'] or 'Untitled document') + ' (title not yet extracted)'


def _size_label(n):
    if not n:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return '%d %s' % (n, unit) if unit == 'B' else '%.1f %s' % (n, unit)
        n /= 1024.0
    return '%.1f TB' % n


def _jurisdiction_cell(row):
    return row['state'] or row['court_state'] or row['court_id'] or '—'


def _result(row):
    badges = [LINK_STATUS_LABELS.get(row['link_status'], row['link_status'])]
    if row['text_extracted']:
        badges.append('Title extracted from first page')
    if row['state']:
        badges.append('State: manifest jurisdiction code')
    elif row['court_state']:
        badges.append('State: state of the matched court')
    links = []
    if row['source_url']:
        links.append({'label': 'Source URL (as recorded, not re-checked)', 'url': row['source_url']})
    links.append({'label': 'Open saved copy', 'url': '/supplement-files/%s/%s' % (ADAPTER, row['id'])})
    return {
        'id': row['id'],
        'title': _document_title(row),
        'subtitle': '%s · %s' % (DOC_TYPE_LABELS.get(row['doc_type'], row['doc_type']), MANIFEST_LABELS.get(row['manifest'], row['manifest'])),
        'cells': {
            'document': _document_title(row),
            'doc_type': DOC_TYPE_LABELS.get(row['doc_type'], row['doc_type']),
            'jurisdiction': _jurisdiction_cell(row),
            'manifest': MANIFEST_LABELS.get(row['manifest'], row['manifest']),
            'size': _size_label(row['bytes']),
            'host': row['host'] or '—',
        },
        'badges': badges,
        'links': links,
    }


def _qualification(gate):
    return gate.get('qualification') or ''


def listing(params=None):
    try:
        con, gate = _connect()
    except (OSError, ValueError, sqlite3.Error) as error:
        return {'available': False, 'reason': 'Court document library is unavailable or failed its hash gate (%s).' % type(error).__name__,
                'total': 0, 'page': 1, 'limit': 0, 'results': []}
    try:
        params = params if isinstance(params, dict) else {}
        where, args = [], []
        state = _one(params, 'state').upper()
        if state:
            where.append('state = ?')
            args.append(state)
        court = _one(params, 'court')
        if court:
            where.append('court_id = ?')
            args.append(court)
        doc_type = _one(params, 'doc_type')
        if doc_type in DOC_TYPE_LABELS:
            where.append('doc_type = ?')
            args.append(doc_type)
        extension = _one(params, 'file_type')
        if extension:
            where.append('extension = ?')
            args.append(extension.lower().lstrip('.'))
        manifest = _one(params, 'manifest')
        if manifest in MANIFEST_LABELS:
            where.append('manifest = ?')
            args.append(manifest)
        link_status = _one(params, 'link_status')
        if link_status in LINK_STATUS_LABELS:
            where.append('link_status = ?')
            args.append(link_status)
        host = _one(params, 'host')
        if host:
            where.append('host = ?')
            args.append(host.lower())
        q = _one(params, 'q')
        if q:
            where.append('(title LIKE ? OR filename LIKE ? OR source_url LIKE ?)')
            like = '%' + q[:200].replace('%', '').replace('_', '') + '%'
            args.extend([like, like, like])
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        total = con.execute('SELECT COUNT(*) FROM documents' + clause, args).fetchone()[0]
        limit, page = _int(params, 'limit', 25, 1, 100), _int(params, 'page', 1, 1, 10 ** 6)
        start = (page - 1) * limit
        rows = con.execute(
            'SELECT * FROM documents' + clause + ' ORDER BY completed_at DESC, id LIMIT ? OFFSET ?',
            args + [limit, start]).fetchall()
        filters = [
            {'name': 'q', 'label': 'Search (title / filename / URL)', 'type': 'search'},
            {'name': 'state', 'label': 'State', 'type': 'select', 'options': _options(con, 'state')},
            {'name': 'doc_type', 'label': 'Document type', 'type': 'select',
             'options': _options(con, 'doc_type', lambda v: DOC_TYPE_LABELS.get(v, v))},
            {'name': 'file_type', 'label': 'File type', 'type': 'select', 'options': _options(con, 'extension')},
            {'name': 'manifest', 'label': 'Manifest', 'type': 'select',
             'options': _options(con, 'manifest', lambda v: MANIFEST_LABELS.get(v, v))},
            {'name': 'link_status', 'label': 'Court match', 'type': 'select',
             'options': _options(con, 'link_status', lambda v: LINK_STATUS_LABELS.get(v, v))},
            {'name': 'host', 'label': 'Source host', 'type': 'select', 'options': _options(con, 'host')},
        ]
        return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': _qualification(gate),
                'filters': filters, 'columns': COLUMNS, 'results': [_result(r) for r in rows]}
    finally:
        con.close()


def detail(item_id):
    if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
        return None
    try:
        con, gate = _connect()
    except (OSError, ValueError, sqlite3.Error):
        return None
    try:
        row = con.execute('SELECT * FROM documents WHERE id = ?', (item_id,)).fetchone()
        if row is None:
            return None
        facts = [
            ['Document type', DOC_TYPE_LABELS.get(row['doc_type'], row['doc_type'])],
            ['Type basis', row['doc_type_basis']],
            ['Manifest', MANIFEST_LABELS.get(row['manifest'], row['manifest'])],
        ]
        if row['wave']:
            facts.append(['Manifest wave', row['wave']])
        facts.append(['Host (as recorded in the download)', row['host'] or 'not recorded'])
        facts.append(['Court match', LINK_STATUS_LABELS.get(row['link_status'], row['link_status'])])
        if row['link_status'] == 'matched':
            facts.append(['Court (court_spine id)', row['court_id']])
        elif row['link_status'] == 'ambiguous':
            candidates = json.loads(row['candidate_court_ids'] or '[]')
            facts.append(['Candidate courts (host is shared, never guessed)', ', '.join(candidates) or 'none recorded'])
        if row['state']:
            facts.append(['State (USPS, from manifest jurisdiction_codes)', row['state']])
        if row['court_state']:
            facts.append(['State of the matched court (from court_spine, independent of jurisdiction_codes)', row['court_state']])
        facts.append(['Bytes', str(row['bytes']) if row['bytes'] is not None else 'not recorded'])
        facts.append(['File type', row['extension'] or 'not recorded'])
        facts.append(['SHA-256 (at manifest build time)', row['sha256']])
        if row['started_at']:
            facts.append(['Download started (download timestamp, not an effective date)', row['started_at']])
        if row['completed_at']:
            facts.append(['Download completed (download timestamp, not an effective date)', row['completed_at']])
        facts.append(['Title extracted from first page text', 'yes' if row['text_extracted'] else 'no'])
        sections = [{'heading': 'Provenance', 'text':
                     'Downloaded government court-website file from the %s manifest (2026-08-20 snapshot). '
                     'Host and file bytes are as recorded by the download pipeline; not re-fetched or re-verified here beyond a '
                     'fresh SHA-256 check when the original is served.' % MANIFEST_LABELS.get(row['manifest'], row['manifest'])}]
        links = []
        if row['source_url']:
            links.append({'label': 'Source URL (as recorded, not re-checked)', 'url': row['source_url']})
        if row['link_status'] == 'matched' and row['court_id']:
            links.append({'label': 'Court detail', 'url': '#courts?id=' + quote(row['court_id'], safe='')})
        links.append({'label': 'Open saved copy', 'url': '/supplement-files/%s/%s' % (ADAPTER, row['id'])})
        return {'id': row['id'], 'title': _document_title(row), 'subtitle': DOC_TYPE_LABELS.get(row['doc_type'], row['doc_type']),
                'qualification': _qualification(gate), 'facts': facts, 'sections': sections, 'links': links}
    finally:
        con.close()


def original(file_id):
    """Serve the saved bytes for one document after a fresh SHA-256 check, confined to the staging root."""
    if not isinstance(file_id, str) or not ID_RE.fullmatch(file_id):
        return None
    try:
        con, _ = _connect()
    except (OSError, ValueError, sqlite3.Error):
        return None
    try:
        row = con.execute('SELECT sha256, local_rel_path, content_type, filename FROM documents WHERE id = ?', (file_id,)).fetchone()
    finally:
        con.close()
    if row is None or not row['local_rel_path'] or not SHA_RE.fullmatch(row['sha256'] or ''):
        return None
    rel = row['local_rel_path']
    if rel.startswith(('/', '\\')) or ':' in rel.split('/', 1)[0]:
        return None
    try:
        full_path = (STAGING / rel).resolve()
        full_path.relative_to(STAGING.resolve())
        if not full_path.is_file():
            return None
        blob = full_path.read_bytes()
    except (OSError, ValueError):
        return None
    if hashlib.sha256(blob).hexdigest() != row['sha256']:
        return None
    filename = row['filename'] or (file_id + '.bin')
    return blob, row['content_type'] or 'application/octet-stream', filename


def _compact(con, where, args, limit=25):
    total = con.execute('SELECT COUNT(*) FROM documents' + where, args).fetchone()[0]
    if total == 0:
        return None
    rows = con.execute('SELECT * FROM documents' + where + ' ORDER BY completed_at DESC, id LIMIT ?', args + [limit]).fetchall()
    return {'total': total, 'results': [_result(r) for r in rows[:limit]]}


def for_court(court_id):
    if not isinstance(court_id, str) or not ID_RE.fullmatch(court_id):
        return None
    try:
        con, gate = _connect()
    except (OSError, ValueError, sqlite3.Error):
        return None
    try:
        matched = _compact(con, ' WHERE court_id = ? AND link_status = "matched"', [court_id])
        # Shared-host bucket: rows where this court is only an ambiguous candidate, never guessed into
        # `matched`. Repair review defect: for_court('txsd') returned bare None for the 198 court_spine
        # courts that only ever appear as ambiguous candidates on a shared host, reading as "no
        # documents" instead of "documents exist but the host is shared with other courts".
        shared_where = ' WHERE link_status = "ambiguous" AND candidate_court_ids LIKE ?'
        shared_args = ['%"' + court_id + '"%']
        shared_total = con.execute('SELECT COUNT(*) FROM documents' + shared_where, shared_args).fetchone()[0]
        # candidate_court_ids LIKE is a coarse pre-filter (JSON array of quoted ids); confirm membership
        # exactly so a substring match on another court's id (e.g. "txsd" inside "txsd2") never counts.
        if shared_total:
            rows = con.execute('SELECT candidate_court_ids FROM documents' + shared_where, shared_args).fetchall()
            shared_total = sum(1 for r in rows if court_id in json.loads(r['candidate_court_ids'] or '[]'))
        if matched is None and not shared_total:
            return None
        payload = matched or {'total': 0, 'results': []}
        payload['qualification'] = ('Local rules, forms and orders downloaded from this court\'s own website host (exact host '
                                     'match, 2026-08-20 snapshot). Not exhaustive; no county field exists in these manifests.')
        payload['link'] = '#documents?court=' + quote(court_id, safe='')
        if shared_total:
            shared_rows = con.execute(
                'SELECT * FROM documents' + shared_where + ' ORDER BY completed_at DESC, id LIMIT 25', shared_args
            ).fetchall()
            shared_results = [_result(r) for r in shared_rows if court_id in json.loads(r['candidate_court_ids'] or '[]')][:25]
            payload['shared_host'] = {
                'total': shared_total,
                'results': shared_results,
                'note': ('served from a website host this court shares with other courts; not attributed to any '
                         'one court'),
                'link': '#documents?link_status=ambiguous',
            }
        return payload
    finally:
        con.close()


def for_state(usps):
    if not isinstance(usps, str) or not re.fullmatch(r'[A-Za-z]{2}', usps or ''):
        return None
    try:
        con, gate = _connect()
    except (OSError, ValueError, sqlite3.Error):
        return None
    try:
        usps = usps.upper()
        # Repair review defect: filtering only on `state` (set only when the manifest's own
        # jurisdiction_codes collapses to exactly one USPS code) hid every matched-court row whose
        # court_spine record itself names this state but whose manifest jurisdiction_codes was
        # null/ambiguous. Widen to state OR court_state, and report both bases as separate counts so
        # the qualification never conflates them.
        payload = _compact(con, ' WHERE state = ? OR court_state = ?', [usps, usps])
        if payload is None:
            return None
        by_manifest_code = con.execute('SELECT COUNT(*) FROM documents WHERE state = ?', [usps]).fetchone()[0]
        by_matched_court = con.execute(
            'SELECT COUNT(*) FROM documents WHERE court_state = ? AND (state IS NULL OR state != ?)', [usps, usps]
        ).fetchone()[0]
        payload['counts_by_basis'] = {'manifest_jurisdiction_code': by_manifest_code, 'court_of_matched_court': by_matched_court}
        payload['qualification'] = (
            'Downloaded court documents for this state from two independent bases, never merged into one guess: '
            '%d rows whose manifest jurisdiction_codes name exactly this one state, plus %d rows that are '
            'link_status=matched to a court_spine court whose own state is this one (2026-08-20 snapshot). '
            'Court joins may still be ambiguous within the state.' % (by_manifest_code, by_matched_court)
        )
        payload['link'] = '#documents?state=' + quote(usps, safe='')
        return payload
    finally:
        con.close()
