"""Read-only, agency-centric join across the regulation, Federal Register, safety-data, document and address layers.

Contract used by server.py:
    GET /api/agency-hub            -> agency_hub.listing()          -> list[dict]
    GET /api/agency-hub/item?key=  -> agency_hub.detail(key)        -> dict | None

Both functions are plain JSON-serialisable, never raise, and cache their result in-process for
TTL_SECONDS (10 minutes). A layer that is missing, closed by its hash gate or unreadable simply
contributes ``None`` counts and an explanatory line in ``unavailable``; the other layers still answer.

What the numbers mean
---------------------
Every figure is a count of what THIS archive holds, on the collection date printed beside it. No
rate, share, average or trend is computed here. An agency is joined to the Federal Register index
only by EXACT slug in that index's own ``agencies`` table (the slugs are listed per agency in
AGENCIES below and echoed back in every payload as ``federal_register.slugs``), to the URL
directory by its exact ``agency_key``, to the downloaded-document set by its exact publisher
``family``, and to the safety datasets by their exact dataset ids. Nothing is matched by name
similarity.

One thing the Federal Register index does that the reader has to be told: it lists every agency the
publisher named on a document, so an FDA rule is listed under both "Food and Drug Administration"
and "Health and Human Services Department". A department's figures therefore already contain its
components' documents, the rows of listing() overlap, and adding them together would count the same
document more than once. Rows carrying ``covers_components`` say so, and nothing in this module ever
sums across agencies.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = ROOT / 'sources'
TTL_SECONDS = 600

FR_DB = SOURCES / 'federal_register_history_20260919' / 'federal_register.sqlite3'
URL_DB = SOURCES / 'url_directory_20260919' / 'url_directory.sqlite3'
DOC_DB = SOURCES / 'agency_science_documents_20260919' / 'documents.sqlite3'

# The Federal Register index was collected in one pass; the date is printed wherever its counts are.
FR_COLLECTED = '2026-08-20'
FR_SPAN = '1994-2026'

_LOCK = threading.Lock()
_CACHE: dict = {}      # name -> (expires_at_monotonic, value)
_BUILDING: set = set()  # names whose build is in flight


# --------------------------------------------------------------------------- the curated registry
# key              stable route parameter (#agencies?agency=<key>)
# fr_slugs         EXACT slug values in federal_register.sqlite3 -> agencies.slug
# url_key          EXACT urls.agency_key in url_directory.sqlite3
# family           EXACT docs.family in the downloaded agency/science document set
# safety           EXACT dataset ids in agency_safety
# reg_slug         EXACT slug in the regulations layer's eCFR agency list (CFR titles/chapters)
AGENCIES = [
    {'key': 'fda', 'name': 'Food and Drug Administration', 'short_name': 'FDA', 'parent': 'Department of Health and Human Services',
     'fr_slugs': ['food-and-drug-administration'], 'url_key': 'fda', 'family': 'fda', 'reg_slug': 'food-and-drug-administration',
     'safety': ['openfda_drug_enforcement', 'openfda_device_enforcement', 'openfda_food_enforcement', 'openfda_drugsfda',
                'openfda_device_pma', 'openfda_device_classification', 'openfda_crl', 'openfda_drug_shortages',
                'openfda_orangebook', 'fda_warning_letters', 'fda_press_recalls'],
     'note': 'Drugs, biologics, devices, food and tobacco. Title 21 of the CFR is held with official GPO text.'},
    {'key': 'epa', 'name': 'Environmental Protection Agency', 'short_name': 'EPA', 'parent': None,
     'fr_slugs': ['environmental-protection-agency'], 'url_key': 'epa', 'family': 'epa', 'reg_slug': 'environmental-protection-agency',
     'safety': [], 'note': 'Pesticides, toxic substances, water and hazardous waste; Title 40 parts are in the CFR slice.'},
    {'key': 'cpsc', 'name': 'Consumer Product Safety Commission', 'short_name': 'CPSC', 'parent': None,
     'fr_slugs': ['consumer-product-safety-commission'], 'url_key': 'cpsc', 'family': 'cpsc', 'reg_slug': 'consumer-product-safety-commission',
     'safety': ['cpsc_recalls_local'], 'note': 'Consumer product recalls and substantial-product-hazard reporting under Title 16.'},
    {'key': 'nhtsa', 'name': 'National Highway Traffic Safety Administration', 'short_name': 'NHTSA', 'parent': 'Department of Transportation',
     'fr_slugs': ['national-highway-traffic-safety-administration'], 'url_key': 'nhtsa', 'family': 'nhtsa',
     'reg_slug': 'national-highway-traffic-safety-administration', 'safety': [],
     'note': 'Motor-vehicle safety standards and defect investigations; Title 49 parts are in the CFR slice.'},
    {'key': 'osha', 'name': 'Occupational Safety and Health Administration', 'short_name': 'OSHA', 'parent': 'Department of Labor',
     'fr_slugs': ['occupational-safety-and-health-administration'], 'url_key': 'osha', 'family': 'osha',
     'reg_slug': 'occupational-safety-and-health-administration', 'safety': [],
     'note': 'Workplace exposure limits and safety standards (29 CFR chapter XVII).'},
    {'key': 'cms', 'name': 'Centers for Medicare & Medicaid Services', 'short_name': 'CMS', 'parent': 'Department of Health and Human Services',
     # Two exact slugs: the agency was named the Health Care Financing Administration until 2001 and the index
     # keeps that identity separately, so its 1994-2001 rules would otherwise be invisible under CMS.
     'fr_slugs': ['centers-for-medicare-medicaid-services', 'health-care-finance-administration'], 'url_key': 'cms', 'family': 'cms',
     'reg_slug': 'centers-for-medicare-medicaid-services', 'safety': [],
     'note': 'Medicare and Medicaid payment and coverage rules (42 CFR chapter IV).'},
    {'key': 'cdc', 'name': 'Centers for Disease Control and Prevention', 'short_name': 'CDC', 'parent': 'Department of Health and Human Services',
     'fr_slugs': ['centers-for-disease-control-and-prevention'], 'url_key': 'cdc', 'family': 'cdc', 'reg_slug': None, 'safety': [],
     'note': 'Surveillance, exposure and health-effect publications.'},
    {'key': 'atsdr', 'name': 'Agency for Toxic Substances and Disease Registry', 'short_name': 'ATSDR', 'parent': 'Department of Health and Human Services',
     'fr_slugs': ['agency-for-toxic-substances-and-disease-registry'], 'url_key': 'atsdr', 'family': 'atsdr', 'reg_slug': None, 'safety': [],
     'note': 'Toxicological profiles and site health assessments.'},
    {'key': 'nih', 'name': 'National Institutes of Health', 'short_name': 'NIH', 'parent': 'Department of Health and Human Services',
     'fr_slugs': ['national-institutes-of-health'], 'url_key': 'nih', 'family': 'ntp', 'reg_slug': None, 'safety': [],
     'note': 'Includes the National Toxicology Program: the downloaded documents for this agency are the NTP publisher family. '
             'The Federal Register index lists no separate National Toxicology Program agency, so its documents are counted under NIH.'},
    {'key': 'ftc', 'name': 'Federal Trade Commission', 'short_name': 'FTC', 'parent': None,
     'fr_slugs': ['federal-trade-commission'], 'url_key': 'ftc', 'family': None, 'reg_slug': 'federal-trade-commission', 'safety': [],
     'note': 'Advertising, labelling and unfair-practice rules (16 CFR chapter I).'},
    {'key': 'sec', 'name': 'Securities and Exchange Commission', 'short_name': 'SEC', 'parent': None,
     'fr_slugs': ['securities-and-exchange-commission'], 'url_key': 'sec', 'family': None, 'reg_slug': 'securities-and-exchange-commission',
     'safety': [], 'note': 'Disclosure and reporting rules (17 CFR chapter II).'},
    {'key': 'cfpb', 'name': 'Consumer Financial Protection Bureau', 'short_name': 'CFPB', 'parent': None,
     'fr_slugs': ['consumer-financial-protection-bureau'], 'url_key': 'cfpb', 'family': None,
     'reg_slug': 'consumer-financial-protection-bureau', 'safety': [], 'note': 'Consumer financial rules (12 CFR chapter X).'},
    {'key': 'doj', 'name': 'Department of Justice', 'short_name': 'DOJ', 'parent': None,
     'fr_slugs': ['justice-department'], 'url_key': 'doj', 'family': None, 'reg_slug': 'justice-department', 'safety': [],
     'covers': 'the DEA, the FBI, the Antitrust Division and its other components',
     'note': 'Federal Register figures include the documents its component agencies file: the publisher names the department and the component on the same document.'},
    {'key': 'dol', 'name': 'Department of Labor', 'short_name': 'DOL', 'parent': None,
     'fr_slugs': ['labor-department'], 'url_key': 'dol', 'family': None, 'reg_slug': 'labor-department', 'safety': [],
     'covers': 'OSHA, MSHA, the Wage and Hour Division and its other components',
     'note': 'Federal Register figures include the documents its component agencies file: the publisher names the department and the component on the same document.'},
    {'key': 'hhs', 'name': 'Department of Health and Human Services', 'short_name': 'HHS', 'parent': None,
     'fr_slugs': ['health-and-human-services-department'], 'url_key': 'hhs', 'family': None,
     'reg_slug': 'health-and-human-services-department', 'safety': [],
     'covers': 'FDA, CDC, CMS, NIH, ATSDR and its other components',
     'note': 'Federal Register figures include the documents its component agencies file: the publisher names the department and the component on the same document.'},
    {'key': 'dot', 'name': 'Department of Transportation', 'short_name': 'DOT', 'parent': None,
     'fr_slugs': ['transportation-department'], 'url_key': 'dot', 'family': None, 'reg_slug': 'transportation-department', 'safety': [],
     'covers': 'the FAA, NHTSA, FMCSA, PHMSA and its other components',
     'note': 'Federal Register figures include the documents its component agencies file: the publisher names the department and the component on the same document.'},
    {'key': 'phmsa', 'name': 'Pipeline and Hazardous Materials Safety Administration', 'short_name': 'PHMSA', 'parent': 'Department of Transportation',
     'fr_slugs': ['pipeline-and-hazardous-materials-safety-administration'], 'url_key': 'phmsa', 'family': None,
     'reg_slug': 'pipeline-and-hazardous-materials-safety-administration', 'safety': [],
     'note': 'Hazardous-materials transport and pipeline safety (49 CFR chapter I).'},
    {'key': 'fmcsa', 'name': 'Federal Motor Carrier Safety Administration', 'short_name': 'FMCSA', 'parent': 'Department of Transportation',
     'fr_slugs': ['federal-motor-carrier-safety-administration'], 'url_key': 'fmcsa', 'family': None,
     'reg_slug': 'federal-motor-carrier-safety-administration', 'safety': [],
     'note': 'Commercial motor-carrier safety regulations (49 CFR chapter III).'},
    {'key': 'msha', 'name': 'Mine Safety and Health Administration', 'short_name': 'MSHA', 'parent': 'Department of Labor',
     'fr_slugs': ['mine-safety-and-health-administration'], 'url_key': 'msha', 'family': None,
     'reg_slug': 'mine-safety-and-health-administration', 'safety': [],
     'note': 'Mine safety and health standards (30 CFR chapter I).'},
]
BY_KEY = {a['key']: a for a in AGENCIES}

TYPE_KEYS = [('Rule', 'rules'), ('Proposed Rule', 'proposed_rules'), ('Notice', 'notices')]


# ------------------------------------------------------------------------------ tiny helpers
def _store(name, build):
    try:
        value, ttl = build(), TTL_SECONDS
    except Exception:
        value, ttl = None, 30
    with _LOCK:
        _CACHE[name] = (time.monotonic() + ttl, value)
        _BUILDING.discard(name)
    return value


def _cached(name, build):
    """build()'s value, recomputed at most once every TTL_SECONDS.

    A value that has just gone stale is served immediately while one background thread refreshes it,
    so only the very first call of a process ever waits for the index scan. Two callers never build
    the same value twice: the second waits for the first. Failure is remembered for 30 seconds.
    """
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(name)
        if hit is not None and hit[0] > now:
            return hit[1]
        if hit is not None:
            if name not in _BUILDING:
                _BUILDING.add(name)
                try:
                    threading.Thread(target=_store, args=(name, build), daemon=True).start()
                except Exception:
                    _BUILDING.discard(name)
            return hit[1]
        waiting = name in _BUILDING
        if not waiting:
            _BUILDING.add(name)
    if not waiting:
        return _store(name, build)
    for _ in range(1200):                       # at most 60 s for the first copy someone else is building
        time.sleep(0.05)
        with _LOCK:
            hit = _CACHE.get(name)
        if hit is not None:
            return hit[1]
    return None


def _ro(path: Path):
    """A read-only connection, or None. Never raises."""
    try:
        if not path.is_file():
            return None
        connection = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection
    except Exception:
        return None


def _gate(module_name, db_path):
    """Open a supplement database through its own adapter so its hash gate still applies.

    Returns (connection, reason). The adapter's _state()/_connect() are used when they are importable;
    if the adapter itself is unavailable the database is opened read-only by path instead, which keeps
    this module working but is recorded in the reason so the page can say so.
    """
    try:
        import importlib
        module = importlib.import_module(module_name)
        state, reason = module._state()
        if state is None:
            return None, (reason or 'data layer not published')
        return module._connect(state), None
    except Exception:
        connection = _ro(db_path)
        return (connection, None) if connection is not None else (None, 'data file missing or unreadable')


def _close(connection):
    try:
        if connection is not None:
            connection.close()
    except Exception:
        pass


def _fr_link(ids, **extra):
    if not ids:
        return None
    query = ['agency=%s' % ids[0]] + ['%s=%s' % (k, str(v).replace(' ', '+')) for k, v in extra.items() if v]
    return '#federal-register?' + '&'.join(query)


def _url_link(url_key, content=None):
    if not url_key:
        return None
    return '#urls?agency=%s' % url_key + (('&content=' + str(content).replace(' ', '+').replace('&', '%26')) if content else '')


# ----------------------------------------------------------------- Federal Register (1994-2026)
def _fr_index():
    """Slug -> {agency_id, name, short_name, parent_agency_id, documents} for every curated slug. Cached."""
    connection, reason = _gate('federal_register_history', FR_DB)
    if connection is None:
        return {'available': False, 'reason': reason, 'by_slug': {}}
    try:
        wanted = sorted({slug for a in AGENCIES for slug in a['fr_slugs']})
        rows = connection.execute(
            'SELECT agency_id, name, short_name, slug, parent_agency_id, documents FROM agencies WHERE slug IN (%s)'
            % ','.join('?' * len(wanted)), wanted).fetchall()
        return {'available': True, 'reason': None,
                'by_slug': {r['slug']: {'agency_id': r['agency_id'], 'name': r['name'], 'short_name': r['short_name'],
                                        'parent_agency_id': r['parent_agency_id'], 'documents': r['documents']} for r in rows}}
    except Exception:
        return {'available': False, 'reason': 'Federal Register index could not be read', 'by_slug': {}}
    finally:
        _close(connection)


def _fr_ids(index):
    """key -> the exact Federal Register agency ids the curated slugs resolve to."""
    return {a['key']: [index['by_slug'][s]['agency_id'] for s in a['fr_slugs'] if s in (index.get('by_slug') or {})]
            for a in AGENCIES}


def _type_lookup(connection):
    """(doc_id -> type) for every type except the most common one, which becomes the default.

    Reading the ids of one type is an index-only scan of docs(type, date DESC), so this costs a
    fraction of a second and spares 1.5 million random reads of the 566 MB ``docs`` table. The
    largest type (Notice, three quarters of the index) is never materialised.
    """
    census = connection.execute('SELECT type, count(*) FROM docs GROUP BY type').fetchall()
    if not census:
        return {}, None
    default = max(census, key=lambda row: row[1])[0]
    lookup = {}
    for kind, _n in census:
        if kind == default:
            continue
        if kind is None:
            for (doc_id,) in connection.execute('SELECT id FROM docs WHERE type IS NULL'):
                lookup[doc_id] = 'Not stated'
            continue
        for (doc_id,) in connection.execute('SELECT id FROM docs WHERE type=?', (kind,)):
            lookup[doc_id] = kind
    return lookup, default


def _fr_counts(index):
    """Per agency: documents by year and type, distinct CFR parts cited, and the latest rule.

    Everything comes from three passes that never touch the wide ``docs`` rows: the type lookup
    above, one read of the doc_agency rows for the curated agencies, and one read of doc_cfr.
    (doc_id, agency_id) pairs are unique and no curated agency maps two ids that share a document,
    so adding counts up across an agency's ids never double-counts.
    """
    out = {a['key']: {'documents': None, 'by_type': {}, 'by_year': [], 'types': [],
                      'cfr_parts_cited': None, 'latest_rule': None} for a in AGENCIES}
    if not index.get('available'):
        return out
    connection, _reason = _gate('federal_register_history', FR_DB)
    if connection is None:
        return out
    try:
        ids_for = _fr_ids(index)
        every = sorted({i for ids in ids_for.values() for i in ids})
        if not every:
            return out
        lookup, default = _type_lookup(connection)
        marks = ','.join('?' * len(every))

        matrix, owners, newest_rule = {}, {}, {}
        for agency_id, doc_id, day in connection.execute(
                'SELECT agency_id, doc_id, date FROM doc_agency WHERE agency_id IN (%s)' % marks, every):
            kind = lookup.get(doc_id, default)
            slot = matrix.setdefault(agency_id, {}).setdefault((day or '')[:4], {})
            slot[kind] = slot.get(kind, 0) + 1
            held = owners.get(doc_id)
            owners[doc_id] = agency_id if held is None else ((held, agency_id) if isinstance(held, int) else held + (agency_id,))
            if kind == 'Rule':
                best = newest_rule.get(agency_id)
                if best is None or (day or '') > best[0]:
                    newest_rule[agency_id] = ((day or ''), doc_id)
        lookup = None

        cited = {}
        for doc_id, cfr_title, cfr_part in connection.execute('SELECT doc_id, cfr_title, cfr_part FROM doc_cfr'):
            held = owners.get(doc_id)
            if held is None:
                continue
            pair = (cfr_title, cfr_part)
            for agency_id in ((held,) if isinstance(held, int) else held):
                cited.setdefault(agency_id, set()).add(pair)
        owners = None

        rule_rows = {}
        wanted = sorted({doc_id for _day, doc_id in newest_rule.values()})
        if wanted:
            for row in connection.execute('SELECT id, title, date, citation, number FROM docs WHERE id IN (%s)'
                                          % ','.join('?' * len(wanted)), wanted):
                rule_rows[row['id']] = {'id': str(row['id']), 'title': row['title'] or row['number'],
                                        'date': row['date'], 'citation': row['citation'] or row['number']}

        named = {k for k, _ in TYPE_KEYS}
        for agency in AGENCIES:
            key, ids = agency['key'], ids_for[agency['key']]
            if not ids:
                continue
            years, by_type, parts, best = {}, {}, set(), None
            for agency_id in ids:
                for year, kinds in (matrix.get(agency_id) or {}).items():
                    for kind, total in kinds.items():
                        by_type[kind] = by_type.get(kind, 0) + total
                        if year:
                            slot = years.setdefault(year, {})
                            slot[kind] = slot.get(kind, 0) + total
                parts |= cited.get(agency_id) or set()
                found = newest_rule.get(agency_id)
                if found is not None and (best is None or found[0] > best[0]):
                    best = found
            out[key]['by_type'] = by_type
            out[key]['documents'] = sum(by_type.values())
            out[key]['by_year'] = [{'year': year, 'total': sum(c.values()), 'counts': c} for year, c in sorted(years.items())]
            out[key]['types'] = [k for k, _ in TYPE_KEYS if k in by_type] + sorted(k for k in by_type if k not in named)
            out[key]['cfr_parts_cited'] = len(parts)
            out[key]['latest_rule'] = rule_rows.get(best[1]) if best else None
        return out
    except Exception:
        return out
    finally:
        _close(connection)


def _fr_profile(ids):
    """The top cited CFR parts and the most recent documents, for one agency."""
    empty = {'top_cfr_parts': [], 'latest_documents': []}
    if not ids:
        return empty
    connection, _reason = _gate('federal_register_history', FR_DB)
    if connection is None:
        return empty
    marks = ','.join('?' * len(ids))
    try:
        parts = []
        for cfr_title, cfr_part, total in connection.execute(
                'SELECT c.cfr_title, c.cfr_part, count(DISTINCT c.doc_id) n FROM doc_cfr c '
                'WHERE c.doc_id IN (SELECT doc_id FROM doc_agency WHERE agency_id IN (%s)) '
                'GROUP BY 1, 2 ORDER BY n DESC, 1, 2 LIMIT 15' % marks, ids):
            parts.append({'title': str(cfr_title), 'part': str(cfr_part), 'documents': total,
                          'heading': None, 'in_slice': False,
                          'rule_history_link': '#federal-register?cfr_title=%s&cfr_part=%s' % (cfr_title, cfr_part),
                          'regulations_link': '#regulations?title=%s&part=%s:%s' % (cfr_title, cfr_title, cfr_part)})

        latest, seen = [], set()
        for row in connection.execute(
                'SELECT d.id, d.title, d.date, d.type, d.citation, d.number FROM doc_agency a JOIN docs d ON d.id=a.doc_id '
                'WHERE a.agency_id IN (%s) ORDER BY a.date DESC LIMIT 40' % marks, ids):
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            latest.append({'id': str(row['id']), 'title': row['title'] or row['number'], 'date': row['date'],
                           'type': row['type'], 'citation': row['citation'] or row['number']})
            if len(latest) >= 12:
                break
        return {'top_cfr_parts': parts, 'latest_documents': latest}
    except Exception:
        return empty
    finally:
        _close(connection)


# --------------------------------------------------------------------------- CFR part headings
def _part_headings():
    """(title, part) -> {heading, in_slice} for the four titles the regulations layer holds. Cached."""
    headings = {}
    try:
        import importlib
        regulations = importlib.import_module('federal_regulations')
        titles = regulations.titles() or {}
        for entry in titles.get('titles') or []:
            number = str(entry.get('title') or '')
            if not number:
                continue
            data = regulations.parts(number, include_all=True, page=1, limit=2000) or {}
            for part in data.get('parts') or []:
                headings[(number, str(part.get('part')))] = {'heading': part.get('heading'), 'in_slice': bool(part.get('in_slice'))}
    except Exception:
        return headings
    return headings


def _regulation_agencies():
    """slug -> eCFR agency entry (CFR titles/chapters it administers). Cached."""
    try:
        import importlib
        regulations = importlib.import_module('federal_regulations')
        data = regulations.agencies(slice_only=False) or {}
        return {a.get('slug'): a for a in (data.get('agencies') or []) if a.get('slug')}
    except Exception:
        return {}


# ------------------------------------------------------------------------- safety datasets
def _safety():
    """dataset id -> descriptor, plus the archive-wide status line. Cached."""
    try:
        import importlib
        safety = importlib.import_module('agency_safety')
        listed = safety.datasets()
        # The adapter answers with the list of datasets; the server wraps it as {'items': ..., 'status': ...}.
        listed = listed.get('items') if isinstance(listed, dict) else listed
        items = {}
        for item in listed or []:
            if isinstance(item, dict) and item.get('dataset'):
                items[item['dataset']] = item
        status = safety.status() or {}
        return {'available': bool(items), 'items': items, 'status': status if isinstance(status, dict) else {}}
    except Exception:
        return {'available': False, 'items': {}, 'status': {}}


# ------------------------------------------------------------------- downloaded documents
def _validated_at(module_name):
    try:
        import importlib
        return (importlib.import_module(module_name).summary() or {}).get('validated_at')
    except Exception:
        return None


def _documents():
    """publisher family -> saved document count. Cached."""
    connection, reason = _gate('agency_science_documents', DOC_DB)
    if connection is None:
        return {'available': False, 'reason': reason, 'by_family': {}, 'validated_at': None}
    try:
        return {'available': True, 'reason': None, 'validated_at': _validated_at('agency_science_documents'),
                'by_family': {row[0]: row[1] for row in connection.execute('SELECT family, count(*) FROM docs GROUP BY family') if row[0]}}
    except Exception:
        return {'available': False, 'reason': 'document index could not be read', 'by_family': {}, 'validated_at': None}
    finally:
        _close(connection)


def _document_sample(family, limit=8):
    connection, _ = _gate('agency_science_documents', DOC_DB)
    if connection is None or not family:
        _close(connection)
        return []
    try:
        return [{'id': str(row['id']), 'title': row['title'], 'url': row['url'],
                 'kind': (row['ext'] or '').upper() if 'ext' in row.keys() else None}
                for row in connection.execute('SELECT * FROM docs WHERE family=? ORDER BY title LIMIT ?', (family, limit))]
    except Exception:
        return []
    finally:
        _close(connection)


# ------------------------------------------------------------------------ official addresses
def _addresses():
    """agency_key -> {total, saved}. Cached."""
    connection, reason = _gate('url_directory', URL_DB)
    if connection is None:
        return {'available': False, 'reason': reason, 'by_key': {}, 'validated_at': None}
    try:
        keys = sorted({a['url_key'] for a in AGENCIES if a['url_key']})
        marks = ','.join('?' * len(keys))
        rows = connection.execute(
            "SELECT agency_key, count(*) total, sum(CASE WHEN saved_status='saved' THEN 1 ELSE 0 END) saved "
            'FROM urls WHERE is_noise=0 AND agency_key IN (%s) GROUP BY 1' % marks, keys).fetchall()
        return {'available': True, 'reason': None, 'validated_at': _validated_at('url_directory'),
                'by_key': {r['agency_key']: {'total': r['total'], 'saved': r['saved'] or 0} for r in rows}}
    except Exception:
        return {'available': False, 'reason': 'address index could not be read', 'by_key': {}, 'validated_at': None}
    finally:
        _close(connection)


def _address_profile(url_key):
    """Counts by content type, file type and host for one agency key."""
    connection, _ = _gate('url_directory', URL_DB)
    if connection is None or not url_key:
        _close(connection)
        return None
    try:
        total = connection.execute('SELECT count(*) FROM urls WHERE is_noise=0 AND agency_key=?', (url_key,)).fetchone()[0]
        if not total:
            return None
        saved = connection.execute("SELECT count(*) FROM urls WHERE is_noise=0 AND saved_status='saved' AND agency_key=?",
                                   (url_key,)).fetchone()[0]
        by_content = [{'value': value, 'count': n, 'link': _url_link(url_key, value)}
                      for value, n in connection.execute(
                          'SELECT content_type, count(*) n FROM urls WHERE is_noise=0 AND agency_key=? AND content_type IS NOT NULL '
                          "AND content_type<>'' GROUP BY 1 ORDER BY n DESC", (url_key,))]
        unlabelled = connection.execute(
            "SELECT count(*) FROM urls WHERE is_noise=0 AND agency_key=? AND (content_type IS NULL OR content_type='')",
            (url_key,)).fetchone()[0]
        by_kind = [{'value': value, 'count': n} for value, n in connection.execute(
            'SELECT doc_kind, count(*) n FROM urls WHERE is_noise=0 AND agency_key=? GROUP BY 1 ORDER BY n DESC LIMIT 8', (url_key,))]
        hosts = [{'host': host, 'count': n} for host, n in connection.execute(
            'SELECT host, count(*) n FROM urls WHERE is_noise=0 AND agency_key=? GROUP BY 1 ORDER BY n DESC LIMIT 8', (url_key,))]
        return {'total': total, 'saved': saved, 'by_content': by_content, 'content_not_labelled': unlabelled,
                'by_kind': by_kind, 'top_hosts': hosts, 'link': _url_link(url_key)}
    except Exception:
        return None
    finally:
        _close(connection)


# -------------------------------------------------------------------------------- assembly
def _as_of(safety, documents, addresses):
    status = safety.get('status') or {}
    dates = {}
    for item in (safety.get('items') or {}).values():
        if item.get('source_as_of'):
            dates[item['source_as_of']] = True
    regulations_as_of = None
    try:
        import importlib
        regulations_as_of = (importlib.import_module('federal_regulations').info() or {}).get('validated_at')
    except Exception:
        pass
    return {
        'federal_register': FR_COLLECTED,
        'federal_register_span': FR_SPAN,
        'safety': status.get('validated_at'),
        'safety_source_dates': sorted(dates) or None,
        'documents': (documents or {}).get('validated_at'),
        'addresses': (addresses or {}).get('validated_at'),
        'regulations': regulations_as_of,
    }


def _build_listing():
    index = _cached('fr_index', _fr_index)
    counts = _cached('fr_counts', lambda: _fr_counts(index))
    safety = _cached('safety', _safety)
    documents = _cached('documents', _documents)
    addresses = _cached('addresses', _addresses)
    regulations = _cached('regulation_agencies', _regulation_agencies)
    as_of = _as_of(safety, documents, addresses)

    unavailable = []
    if not index.get('available'):
        unavailable.append('Federal Register index: %s' % (index.get('reason') or 'not available'))
    if not safety.get('available'):
        unavailable.append('Safety and enforcement datasets: not available')
    if not documents.get('available'):
        unavailable.append('Downloaded documents: %s' % (documents.get('reason') or 'not available'))
    if not addresses.get('available'):
        unavailable.append('Official addresses: %s' % (addresses.get('reason') or 'not available'))

    rows = []
    for agency in AGENCIES:
        key = agency['key']
        fr = counts.get(key) or {}
        by_type = fr.get('by_type') or {}
        ids = [index['by_slug'][s]['agency_id'] for s in agency['fr_slugs'] if s in index.get('by_slug', {})]
        safety_ids = [d for d in agency['safety'] if d in (safety.get('items') or {})]
        safety_records = sum(int((safety['items'][d].get('rows') or 0)) for d in safety_ids) if safety_ids else (0 if safety.get('available') else None)
        family = agency['family']
        saved_documents = (documents.get('by_family') or {}).get(family) if family else (0 if documents.get('available') else None)
        if family and documents.get('available') and saved_documents is None:
            saved_documents = 0
        address = (addresses.get('by_key') or {}).get(agency['url_key'])
        known_addresses = address['total'] if address else (0 if addresses.get('available') else None)
        reg = (regulations or {}).get(agency['reg_slug']) if agency['reg_slug'] else None

        counted = {'federal_register_documents': fr.get('documents'), 'cfr_parts_cited': fr.get('cfr_parts_cited'),
                   'safety_records': safety_records, 'safety_datasets': len(safety_ids),
                   'saved_documents': saved_documents, 'known_addresses': known_addresses,
                   'saved_addresses': address['saved'] if address else None}
        for kind, name in TYPE_KEYS:
            counted[name] = by_type.get(kind, 0) if fr.get('documents') is not None else None
        if fr.get('documents') is not None:
            counted['other_documents'] = fr['documents'] - sum(by_type.get(k, 0) for k, _ in TYPE_KEYS)

        rows.append({
            'key': key, 'name': agency['name'], 'short_name': agency['short_name'], 'parent': agency['parent'],
            'note': agency['note'], 'covers_components': agency.get('covers'),
            'counts': counted, 'latest_rule': fr.get('latest_rule'),
            'federal_register': {'ids': ids, 'slugs': list(agency['fr_slugs']), 'collected': FR_COLLECTED, 'span': FR_SPAN},
            'regulations': {'slug': agency['reg_slug'],
                            'cfr_chapters': (reg or {}).get('cfr_chapters') or [],
                            'parts_in_referenced_chapters': (reg or {}).get('parts_in_referenced_chapters'),
                            'slice_parts': len((reg or {}).get('slice_parts') or [])},
            'url_agency_key': agency['url_key'], 'documents_family': family,
            'safety_dataset_ids': safety_ids,
            'links': {
                'profile': '#agencies?agency=%s' % key,
                'federal_register': _fr_link(ids),
                'rules': _fr_link(ids, type='Rule'),
                'proposed_rules': _fr_link(ids, type='Proposed Rule'),
                'notices': _fr_link(ids, type='Notice'),
                'addresses': _url_link(agency['url_key']),
                'documents': ('#agency-documents?agency=%s' % family) if family and saved_documents else None,
                'regulations': ('#regulations?agency=%s' % agency['reg_slug']) if agency['reg_slug'] else None,
                'datasets': ('#agencies?dataset=%s' % safety_ids[0]) if safety_ids else None,
            },
            'as_of': as_of, 'unavailable': unavailable,
        })
    return rows


def _build_detail(key):
    agency = BY_KEY.get(key)
    if agency is None:
        return None
    rows = _cached('listing', _build_listing) or []
    base = next((r for r in rows if r['key'] == key), None)
    if base is None:
        return None
    item = json.loads(json.dumps(base))

    index = _cached('fr_index', _fr_index) or {'by_slug': {}}
    counts = _cached('fr_counts', lambda: _fr_counts(index)) or {}
    ids = [index['by_slug'][s]['agency_id'] for s in agency['fr_slugs'] if s in (index.get('by_slug') or {})]
    profile = _fr_profile(ids)
    headings = _cached('part_headings', _part_headings) or {}
    for part in profile['top_cfr_parts']:
        found = headings.get((part['title'], part['part']))
        if found:
            part['heading'] = found['heading']
            part['in_slice'] = found['in_slice']
    # Each document carries its index id; the page opens it with /api/area/federal-register/item?id=<id>.

    safety = _cached('safety', _safety)
    datasets = []
    for dataset_id in base['safety_dataset_ids']:
        spec = (safety.get('items') or {}).get(dataset_id) or {}
        datasets.append({'id': dataset_id, 'label': spec.get('label') or dataset_id, 'records': spec.get('rows'),
                         'publisher': spec.get('publisher'), 'group': spec.get('group'),
                         'source_as_of': spec.get('source_as_of'), 'captured_at': spec.get('captured_at'),
                         'link': '#agencies?dataset=%s' % dataset_id})

    family = agency['family']
    saved = None
    if family and (base['counts'].get('saved_documents') or 0):
        saved = {'total': base['counts']['saved_documents'], 'family': family,
                 'link': '#agency-documents?agency=%s' % family, 'results': _document_sample(family)}

    regulations = _cached('regulation_agencies', _regulation_agencies)
    reg = (regulations or {}).get(agency['reg_slug']) if agency['reg_slug'] else None
    slice_parts = []
    for part_id in (reg or {}).get('slice_parts') or []:
        pieces = str(part_id).split(':')
        if len(pieces) == 3:
            found = headings.get((pieces[1], pieces[2])) or {}
            slice_parts.append({'title': pieces[1], 'part': pieces[2], 'heading': found.get('heading'),
                                'regulations_link': '#regulations?title=%s&part=%s:%s' % (pieces[1], pieces[1], pieces[2]),
                                'rule_history_link': '#federal-register?cfr_title=%s&cfr_part=%s' % (pieces[1], pieces[2])})

    item.update({
        'fr_by_year': (counts.get(key) or {}).get('by_year') or [], 'fr_types': (counts.get(key) or {}).get('types') or [],
        'top_cfr_parts': profile['top_cfr_parts'], 'latest_documents': profile['latest_documents'],
        'safety_datasets': datasets, 'saved_documents': saved, 'addresses': _address_profile(agency['url_key']),
        'cfr': {'slug': agency['reg_slug'], 'chapters': (reg or {}).get('cfr_chapters') or [],
                'parts_in_referenced_chapters': (reg or {}).get('parts_in_referenced_chapters'),
                'slice_parts': slice_parts,
                'basis': (reg or {}).get('basis') or 'eCFR agency list: the CFR titles and chapters the agency administers',
                'link': ('#regulations?agency=%s' % agency['reg_slug']) if agency['reg_slug'] else None},
        'qualification': (
            'Counts are records held in this archive, not a complete record of the agency\'s activity. Federal Register '
            'figures are the documents the index lists for this agency, collected %s for %s; the index names every agency the '
            'publisher printed on a document, so a department\'s figures already include the documents its component agencies '
            'file. A document that cites a CFR part may propose, amend, correct or only discuss it. Safety and enforcement '
            'records are publisher exports as of their own stated dates and are agency records about a product or firm, not '
            'findings of defect, causation or liability. Addresses are addresses found in saved discovery lists, not a '
            'statement that a page is reachable today.'
        ) % (FR_COLLECTED, FR_SPAN),
    })
    return item


# ------------------------------------------------------------------------------- public API
def listing():
    """Every curated agency with the counts this archive holds. Always a list; never raises."""
    try:
        return _cached('listing', _build_listing) or []
    except Exception:
        return []


def detail(key):
    """One agency profile, or None when the key is not one of the curated agencies. Never raises."""
    try:
        clean = str(key or '').strip().lower()
        if not re.fullmatch(r'[a-z_]{2,24}', clean) or clean not in BY_KEY:
            return None
        return _cached('detail:%s' % clean, lambda: _build_detail(clean))
    except Exception:
        return None


def keys():
    """The curated agency keys, in display order."""
    return [a['key'] for a in AGENCIES]
