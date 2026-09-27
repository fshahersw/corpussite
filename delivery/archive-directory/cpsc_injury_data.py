"""Read-only, hash-gated adapter for sources/cpsc_injury_data_20260919 (two independent CPSC datasets):

  neiss           CPSC NEISS 2025 emergency-department-visit SAMPLE (410,201 rows). Every row is a sample
                  record; the publisher's statistical Weight is shown exactly as printed and this adapter
                  never computes a rate, trend or national estimate from it. Codes are labelled only from
                  the file's own NEISS_FMT table (neiss_fmt); an unmapped code is left as a bare number.
  saferproducts   CPSC SaferProducts.gov incident reports (69,333 rows). Consumer-submitted and unverified
                  by CPSC (the publisher's own disclaimer is carried in the qualification). No submitter
                  identity or contact column exists in the export and none is stored or shown.

Generic view contract: listing(params), detail(id). A `dataset` param ('neiss' default, 'saferproducts')
selects which table listing() reads; detail(id) is routed by the id's own prefix ("neiss:" / "sp:").
Fail-closed on a missing/failed validation or a data-file hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/cpsc_injury_data_20260919'
DB_NAME = 'cpsc_injury_data.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
_LOCK = threading.Lock()
_CACHE: dict = {}

STOPWORDS = {'a', 'an', 'and', 'the', 'of', 'in', 'on', 'for', 'to', 'with', 'by', 'is', 'was', 'were',
             'this', 'that', 'it', 'as', 'at', 'or', 'be', 'are', 'from', 'not'}

NEISS_QUALIFICATION = (
    "CPSC NEISS 2025: a national probability SAMPLE of emergency-department visits (410,201 rows), not a "
    "census. Each row is the sample record and the publisher's statistical Weight, printed as-is; no rate, "
    "trend or national estimate is computed here. Codes are labelled only from the file's own NEISS_FMT "
    "table; an unmapped code is shown bare."
)
SAFERPRODUCTS_QUALIFICATION = (
    "SaferProducts.gov public incident reports (69,333 rows, 2011-2026): consumer-submitted, unverified by "
    "CPSC. “CPSC does not guarantee the accuracy, completeness, or adequacy” of this database "
    "(publisher disclaimer). No submitter identity or contact field exists in this export; none is stored. "
    "Not a confirmed defect or legal finding."
)
assert len(NEISS_QUALIFICATION) < 400 and len(SAFERPRODUCTS_QUALIFICATION) < 400


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}


def _sha256(path: Path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _state(folder=None):
    folder = Path(folder) if folder else DATA
    try:
        signature = tuple((name, (folder / name).stat().st_size, (folder / name).stat().st_mtime_ns)
                           for name in ('validation.json', DB_NAME))
    except OSError:
        return None, 'supplement files missing'
    with _LOCK:
        cached = _CACHE.get(str(folder))
        if cached and cached['signature'] == signature:
            return cached['state'], cached['reason']
        state, reason = None, None
        try:
            gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)} if isinstance(gate, dict) else {}
            if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'supplement not published (status/ready gate closed)'
            elif not expected.get(DB_NAME) or _sha256(folder / DB_NAME) != expected[DB_NAME]:
                reason = 'database file does not match its recorded SHA-256'
            else:
                state = {'folder': folder, 'counts': gate.get('counts') or {}}
        except (OSError, ValueError):
            reason = 'validation.json missing or unreadable'
        _CACHE[str(folder)] = {'signature': signature, 'state': state, 'reason': reason}
        return state, reason


def _connect(state):
    connection = sqlite3.connect(f"file:{(state['folder'] / DB_NAME).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _text(params, name, maxlen=160):
    value = (params or {}).get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:maxlen]


def _fts_query(text):
    tokens = [t for t in re.findall(r"[\w][\w.\-]*", text or '', re.UNICODE) if t.lower() not in STOPWORDS][:8]
    if not tokens:
        return None
    return ' '.join('"%s"' % t.replace('"', '') for t in tokens)


NUMERIC_FACETS = {'year', 'month', 'body_part', 'diagnosis', 'disposition', 'sex'}
AGE_BAND_ORDER = ['Under 1 year', '1-4 years', '5-9 years', '10-14 years', '15-19 years', '20-24 years',
                   '25-34 years', '35-44 years', '45-54 years', '55-64 years', '65-74 years', '75+ years', 'Unknown']


def _facet(connection, dataset, facet):
    """Filter-option counts from the precomputed facet_counts table (built once, offline, by
    build.build_facet_counts) -- NOT a live GROUP BY on neiss_cases/saferproducts_incidents, which at
    410,201/69,333 rows measured 0.15-9s per filter depending on whether SQLite could satisfy it with a
    covering index. facet_counts holds at most a few dozen rows per (dataset, facet), so this is a plain
    indexed point lookup regardless of the source table's size."""
    rows = connection.execute('SELECT value, label, n FROM facet_counts WHERE dataset=? AND facet=?', (dataset, facet)).fetchall()
    if facet in NUMERIC_FACETS:
        rows = sorted(rows, key=lambda r: int(r['value']))
    elif facet == 'age_band':
        rows = sorted(rows, key=lambda r: AGE_BAND_ORDER.index(r['value']) if r['value'] in AGE_BAND_ORDER else len(AGE_BAND_ORDER))
    else:
        rows = sorted(rows, key=lambda r: r['value'])
    return [(r['value'], r['label'], r['n']) for r in rows]


# ------------------------------------------------------------------------------------------- NEISS

def _words(label, keep_code=False):
    """Coded labels ('4076 - BEDS OR BEDFRAMES, OTHER') shown as words ('Beds or bedframes, other (4076)'); stored values are unchanged."""
    text = str(label or '').strip()
    code, separator, rest = text.partition(' - ')
    if not separator or not code.strip().isdigit():
        code, rest = '', text
    rest = rest.strip()
    if rest.isupper():
        rest = rest.capitalize()
    return ('%s (%s)' % (rest, code.strip())) if (keep_code and code.strip()) else rest


def _neiss_result(row):
    product = _words(row['product_1_label'], keep_code=True) or (('Product code %s' % row['product_1_code']) if row['product_1_code'] is not None else 'Product not recorded')
    badges = []
    if row['disposition_code'] == 8:
        badges.append('Fatality (disposition as coded)')
    return {
        'id': 'neiss:' + row['case_number'], 'title': product,
        'subtitle': '%s · %s · Age %s' % (row['treatment_date'] or 'date not recorded', row['sex_label'] or 'sex not recorded', row['age_display'] or 'unknown'),
        'cells': {'date': row['treatment_date'] or '—', 'product': product, 'diagnosis': _words(row['diagnosis_label']) or (str(row['diagnosis_code']) if row['diagnosis_code'] is not None else '—'),
                  'body_part': _words(row['body_part_label']) or (str(row['body_part_code']) if row['body_part_code'] is not None else '—'),
                  'disposition': _words(row['disposition_label']) or (str(row['disposition_code']) if row['disposition_code'] is not None else '—')},
        'badges': badges[:2],
        'links': [{'label': 'CPSC NEISS methodology (external)', 'url': 'https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data'}],
    }


def _listing_neiss(connection, params, state_counts):
    clauses, args = [], []
    query = _fts_query(_text(params, 'q'))
    product = _text(params, 'product')
    if product:
        if product.isdigit():
            clauses.append('(product_1_code=? OR product_2_code=? OR product_3_code=?)')
            args += [int(product)] * 3
        else:
            needle = '%' + product.replace('%', '').replace('_', '') + '%'
            clauses.append('(product_1_label LIKE ? OR product_2_label LIKE ? OR product_3_label LIKE ?)')
            args += [needle, needle, needle]
    for name, column, numeric in (('year', 'year', True), ('month', 'month', True), ('body_part', 'body_part_code', True),
                                   ('diagnosis', 'diagnosis_code', True), ('disposition', 'disposition_code', True),
                                   ('sex', 'sex_code', True), ('age_band', 'age_band', False)):
        value = _text(params, name)
        if not value:
            continue
        if numeric:
            if not re.fullmatch(r'-?\d+', value):
                continue
            clauses.append(f'{column}=?'); args.append(int(value))
        else:
            clauses.append(f'{column}=?'); args.append(value)
    where_sql = (' AND '.join(clauses)) if clauses else ''
    page, limit = _int(params.get('page'), 1, 1, 10 ** 6), _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    if query:
        base = 'FROM neiss_fts f JOIN neiss_cases c ON c.rid=f.rowid WHERE neiss_fts MATCH ?' + (' AND ' + where_sql if where_sql else '')
        sql_args = [query] + args
    else:
        base = 'FROM neiss_cases c' + (' WHERE ' + where_sql if where_sql else '')
        sql_args = args
    if not query and not clauses:
        total = state_counts.get('neiss_cases', 0)
    else:
        total = connection.execute('SELECT count(*) ' + base, sql_args).fetchone()[0]
    rows = connection.execute(f'SELECT c.* {base} ORDER BY c.treatment_date DESC, c.rid LIMIT ? OFFSET ?',
                               sql_args + [limit, (page - 1) * limit]).fetchall()
    filters = [
        {'name': 'q', 'label': 'Search narrative / product text', 'type': 'search'},
        {'name': 'product', 'label': 'Product (code or name)', 'type': 'search'},
        {'name': 'year', 'label': 'Year', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'year')]},
        {'name': 'month', 'label': 'Month', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'month')]},
        {'name': 'body_part', 'label': 'Body part', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'body_part')]},
        {'name': 'diagnosis', 'label': 'Diagnosis', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'diagnosis')]},
        {'name': 'disposition', 'label': 'Disposition', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'disposition')]},
        {'name': 'age_band', 'label': 'Age band (as coded)', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'age_band')]},
        {'name': 'sex', 'label': 'Sex', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'neiss', 'sex')]},
    ]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': NEISS_QUALIFICATION,
            'filters': filters,
            'columns': [{'key': 'product', 'label': 'Product'}, {'key': 'date', 'label': 'Treatment date'}, {'key': 'diagnosis', 'label': 'Diagnosis'},
                        {'key': 'body_part', 'label': 'Body part'}, {'key': 'disposition', 'label': 'Disposition'}],
            'results': [_neiss_result(row) for row in rows]}


def _neiss_detail(connection, case_number):
    row = connection.execute('SELECT * FROM neiss_cases WHERE case_number=?', (case_number,)).fetchone()
    if row is None:
        return None
    def fact(label, code_key, label_key):
        code, lbl = row[code_key], row[label_key]
        if code is None:
            return [label, 'not recorded']
        return [label, ('%s (code %s)' % (lbl, code)) if lbl else ('code %s (not in NEISS_FMT)' % code)]
    facts = [
        ['CPSC case number', row['case_number']], ['Treatment date', row['treatment_date'] or 'not recorded'],
        ['Age (as coded)', '%s (code %s)' % (row['age_display'], row['age_code']) if row['age_code'] is not None else 'not recorded'],
        ['Age band (derived from the code)', row['age_band']],
        fact('Sex', 'sex_code', 'sex_label'), fact('Race', 'race_code', 'race_label'),
    ]
    if row['other_race']:
        facts.append(['Race, other (as printed)', row['other_race']])
    facts.append(fact('Hispanic', 'hispanic_code', 'hispanic_label'))
    facts.append(fact('Body part', 'body_part_code', 'body_part_label'))
    facts.append(fact('Diagnosis', 'diagnosis_code', 'diagnosis_label'))
    if row['other_diagnosis']:
        facts.append(['Diagnosis, other (as printed)', row['other_diagnosis']])
    if row['body_part_2_code'] is not None:
        facts.append(fact('Body part (secondary)', 'body_part_2_code', 'body_part_2_label'))
    if row['diagnosis_2_code'] is not None:
        facts.append(fact('Diagnosis (secondary)', 'diagnosis_2_code', 'diagnosis_2_label'))
    if row['other_diagnosis_2']:
        facts.append(['Diagnosis (secondary), other (as printed)', row['other_diagnosis_2']])
    facts.append(fact('Disposition', 'disposition_code', 'disposition_label'))
    facts.append(fact('Location', 'location_code', 'location_label'))
    facts.append(fact('Fire involvement', 'fire_involvement_code', 'fire_involvement_label'))
    facts.append(fact('Product 1', 'product_1_code', 'product_1_label'))
    if row['product_2_code'] is not None:
        facts.append(fact('Product 2', 'product_2_code', 'product_2_label'))
    if row['product_3_code'] is not None:
        facts.append(fact('Product 3', 'product_3_code', 'product_3_label'))
    facts.append(fact('Alcohol involvement', 'alcohol_code', 'alcohol_label'))
    facts.append(fact('Drug involvement', 'drug_code', 'drug_label'))
    facts.append(['Sample stratum (as printed)', row['stratum'] or 'not recorded'])
    facts.append(['Primary sampling unit (as printed)', row['psu'] or 'not recorded'])
    facts.append(['Statistical weight (publisher-reported; do not compute a rate or estimate from a single row)', str(row['weight']) if row['weight'] is not None else 'not recorded'])
    sections = [{'heading': 'Narrative (as printed)', 'text': row['narrative'] or 'not recorded'}]
    return {'id': 'neiss:' + row['case_number'], 'title': row['product_1_label'] or 'NEISS sample record', 'subtitle': row['treatment_date'] or '',
            'qualification': NEISS_QUALIFICATION, 'facts': facts, 'sections': sections,
            'links': [{'label': 'CPSC NEISS methodology (external)', 'url': 'https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data'}]}


# ------------------------------------------------------------------------------------------- SaferProducts

def _sp_result(row):
    title = row['product_description'] or row['product_type'] or row['product_category'] or 'Consumer product'
    return {
        'id': 'sp:' + row['report_no'], 'title': title[:140],
        'subtitle': '%s · %s · %s' % (row['report_date'] or 'date not recorded', row['category_of_submitter'] or 'submitter category not recorded', row['state'] or ''),
        'cells': {'date': row['report_date'] or '—', 'category': row['product_category'] or '—',
                  'manufacturer': row['manufacturer_name'] or row['brand'] or '—', 'state': row['state'] or '—',
                  'submitter': row['category_of_submitter'] or '—'},
        'badges': ['Unverified consumer-submitted report'],
        'links': [{'label': 'SaferProducts.gov (external)', 'url': 'https://www.saferproducts.gov/PublicSearch'}],
    }


def _listing_saferproducts(connection, params, state_counts):
    clauses, args = [], []
    query = _fts_query(_text(params, 'q'))
    for name, column in (('product_category', 'product_category'), ('state', 'state'), ('category_of_submitter', 'category_of_submitter')):
        value = _text(params, name)
        if value:
            clauses.append(f'{column}=?'); args.append(value)
    year = _text(params, 'year')
    if re.fullmatch(r'\d{4}', year):
        clauses.append('year=?'); args.append(int(year))
    where_sql = ' AND '.join(clauses)
    page, limit = _int(params.get('page'), 1, 1, 10 ** 6), _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    if query:
        base = 'FROM saferproducts_fts f JOIN saferproducts_incidents c ON c.rid=f.rowid WHERE saferproducts_fts MATCH ?' + (' AND ' + where_sql if where_sql else '')
        sql_args = [query] + args
    else:
        base = 'FROM saferproducts_incidents c' + (' WHERE ' + where_sql if where_sql else '')
        sql_args = args
    if not query and not clauses:
        total = state_counts.get('saferproducts_incidents', 0)
    else:
        total = connection.execute('SELECT count(*) ' + base, sql_args).fetchone()[0]
    rows = connection.execute(f'SELECT c.* {base} ORDER BY c.report_date_iso DESC, c.rid LIMIT ? OFFSET ?',
                               sql_args + [limit, (page - 1) * limit]).fetchall()
    filters = [
        {'name': 'q', 'label': 'Search incident / product text', 'type': 'search'},
        {'name': 'product_category', 'label': 'Product category', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'saferproducts', 'product_category')]},
        {'name': 'state', 'label': 'State (incident, as reported)', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'saferproducts', 'state')]},
        {'name': 'year', 'label': 'Report year', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'saferproducts', 'year')]},
        {'name': 'category_of_submitter', 'label': 'Category of submitter', 'type': 'select', 'options': [{'value': v, 'label': lbl, 'count': n} for v, lbl, n in _facet(connection, 'saferproducts', 'category_of_submitter')]},
    ]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': SAFERPRODUCTS_QUALIFICATION,
            'filters': filters,
            'columns': [{'key': 'date', 'label': 'Report date'}, {'key': 'category', 'label': 'Product category'}, {'key': 'manufacturer', 'label': 'Manufacturer / brand'},
                        {'key': 'state', 'label': 'State'}, {'key': 'submitter', 'label': 'Submitter category'}],
            'results': [_sp_result(row) for row in rows]}


def _sp_detail(connection, report_no):
    row = connection.execute('SELECT * FROM saferproducts_incidents WHERE report_no=?', (report_no,)).fetchone()
    if row is None:
        return None
    facts = [
        ['Report number', row['report_no']], ['Report date', row['report_date'] or 'not recorded'],
        ['Sent to manufacturer/importer/private labeler', row['sent_to_manufacturer_date'] or 'not recorded'],
        ['Publication date', row['publication_date'] or 'not recorded'],
        ['Category of submitter', row['category_of_submitter'] or 'not recorded'],
        ['Product category', row['product_category'] or 'not recorded'],
        ['Product sub-category', row['product_sub_category'] or 'not recorded'],
        ['Product type', row['product_type'] or 'not recorded'],
        ['Product code (as printed)', row['product_code'] or 'not recorded'],
        ['Manufacturer / importer / private labeler', row['manufacturer_name'] or 'not recorded'],
        ['Brand', row['brand'] or 'not recorded'], ['Model name or number', row['model_name_or_number'] or 'not recorded'],
        ['Retailer', row['retailer'] or 'not recorded'], ['Retailer state', row['retailer_state'] or 'not recorded'],
        ['Purchase date', row['purchase_date'] or 'not recorded'], ['Purchase date is an estimate (as answered)', row['purchase_date_is_estimate'] or 'not recorded'],
        ['City / state / ZIP (as submitted)', ', '.join(x for x in (row['city'], row['state'], row['zip']) if x) or 'not recorded'],
        ['Incident location', row['location'] or 'not recorded'],
        ['Victim severity (as coded by the submitter)', row['victim_severity'] or 'not recorded'],
        ["Victim's sex", row['victim_sex'] or 'not recorded'], ['Relation to victim', row['relation_to_victim'] or 'not recorded'],
        ["Victim's age (as submitted)", row['victim_age'] or 'not recorded'],
        ['Submitter has the product (as answered)', row['submitter_has_product'] or 'not recorded'],
        ['Product damaged before incident (as answered)', row['product_damaged_before'] or 'not recorded'],
        ['Product modified before incident (as answered)', row['product_modified_before'] or 'not recorded'],
        ['Contacted the manufacturer (as answered)', row['contacted_manufacturer'] or 'not recorded'],
        ['Associated report numbers (as printed)', row['associated_report_numbers'] or 'none printed'],
    ]
    sections = [{'heading': 'Incident description (as submitted, unverified)', 'text': row['incident_description'] or 'not recorded'}]
    if row['damage_description']:
        sections.append({'heading': 'Damage description (as submitted)', 'text': row['damage_description']})
    if row['answer_explanation']:
        sections.append({'heading': 'Answer explanation (as submitted)', 'text': row['answer_explanation']})
    if row['company_comments']:
        sections.append({'heading': 'Company comments', 'text': row['company_comments']})
    title = row['product_description'] or row['product_type'] or 'Consumer product incident report'
    return {'id': 'sp:' + row['report_no'], 'title': title[:200], 'subtitle': row['report_date'] or '',
            'qualification': SAFERPRODUCTS_QUALIFICATION, 'facts': facts, 'sections': sections,
            'links': [{'label': 'SaferProducts.gov (external)', 'url': 'https://www.saferproducts.gov/PublicSearch'}]}


# ------------------------------------------------------------------------------------------- public API

def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    dataset = _text(params, 'dataset') or 'neiss'
    try:
        connection = _connect(state)
        try:
            if dataset == 'saferproducts':
                return _listing_saferproducts(connection, params, state['counts'])
            return _listing_neiss(connection, params, state['counts'])
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('database could not be read: %s' % type(error).__name__)


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not isinstance(item_id, str):
        return None
    try:
        connection = _connect(state)
        try:
            if item_id.startswith('neiss:'):
                return _neiss_detail(connection, item_id[len('neiss:'):])
            if item_id.startswith('sp:'):
                return _sp_detail(connection, item_id[len('sp:'):])
            return None
        finally:
            connection.close()
    except sqlite3.Error:
        return None
