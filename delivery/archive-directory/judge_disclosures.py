"""Read-only, hash-gated adapter for judge financial disclosures (sources/judge_financial_disclosures_20260919).

Generic view contract: listing(params), detail(id). No original() -- disclosure PDFs are not local; every
link points at CourtListener's own hosting. Fail-closed: validation.json must carry the uniform envelope with
status == "passed", ready == true and a matching SHA-256 for the registered SQLite file; otherwise every
function returns the "not available" shape and never raises.

What the data is: federally mandated public financial disclosure filings by U.S. federal judges, from a
CourtListener bulk snapshot labelled 2026-06-30, joined to sources/judge_structured_20260919 by the native
person_id == cl_person_id bridge (no name matching). It reports what was filed, for what year, as of this
snapshot -- never a dollar figure (gross_value_code / income_code are raw publisher letter/number codes with
no dollar mapping attached here), never a total, never a spouse/dependent name, and it draws no conclusion
about recusal or conflict of interest. A bridged judge with no row in this layer means "no filing found in the
2026-06-30 CourtListener snapshot," which is a coverage gap, not a statement about the judge's holdings.
"""
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/judge_financial_disclosures_20260919'
ADAPTER = 'judge_disclosures'
DB_FILE = 'disclosures.sqlite3'
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
TOP_HOLDINGS_LIMIT = 25
DETAIL_INVESTMENTS_LIMIT = 250
_DISC_ID_RE = re.compile(r'[1-9][0-9]{0,11}')
_ENTITY_ID_RE = re.compile(r'[A-Za-z0-9_.\-]{1,120}')
_LOCK = threading.Lock()
_CACHE = {}


def _fingerprint(db_path):
    st = db_path.stat()
    return st.st_size, st.st_mtime_ns


def _load(folder=None):
    """Return the cached, hash-verified state or raise. The SQLite file is re-hashed whenever its size or
    mtime differs from the last verified fingerprint (any tamper that does not touch either is not a case a
    filesystem-level threat model needs to defend against); a brand-new fingerprint always gets a fresh
    cryptographic hash check before the connection is trusted.
    """
    folder = Path(folder or DATA)
    gate_bytes = (folder / 'validation.json').read_bytes()
    gate = json.loads(gate_bytes)
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('judge disclosures validation is not passed/ready')
    listed = {d.get('path'): d for d in gate.get('data_files', [])}
    if DB_FILE not in listed:
        raise ValueError('judge disclosures data file list mismatch')
    db_path = folder / DB_FILE
    fingerprint = _fingerprint(db_path)
    cache_key = str(db_path)
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached['fingerprint'] == fingerprint:
            return cached
        digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
        if digest != listed[DB_FILE].get('sha256'):
            _CACHE.pop(cache_key, None)
            raise ValueError('judge disclosures hash gate failed for %s' % DB_FILE)
        if cached:
            try:
                cached['conn'].close()
            except sqlite3.Error:
                pass
        conn = sqlite3.connect('file:%s?mode=ro' % db_path.as_posix(), uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        state = {'conn': conn, 'fingerprint': fingerprint, 'digest': digest,
                  'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}}
        _CACHE[cache_key] = state
        return state


def _unavailable(error, extra=None):
    out = {'available': False, 'reason': 'Judge disclosures supplement is unavailable or failed its hash gate (%s).' % type(error).__name__}
    out.update(extra or {})
    return out


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


REPORT_TYPE_NOTE = 'raw CourtListener report_type code; no label table exists in this snapshot'


def _disc_result(row, conn):
    holdings = row['investment_count'] or 0
    inferred = row['investment_count_inferred'] or 0
    holdings_label = ('%d holding row%s' % (holdings, '' if holdings == 1 else 's')) if holdings else 'no reportable holdings on file'
    if holdings and inferred:
        holdings_label += ' (%d extraction-inferred)' % inferred
    subtitle_bits = ['Filing year %s' % (row['year'] if row['year'] is not None else 'not recorded'), holdings_label]
    if row['addendum_redacted']:
        subtitle_bits.append('addendum redacted')
    badges = []
    if row['is_amended']:
        badges.append('Amended')
    if not holdings:
        badges.append('No reportable holdings')
    return {
        'id': str(row['id']),
        'title': row['name'] or ('Judge (CourtListener person id %s)' % row['cl_person_id']),
        'subtitle': ' · '.join(subtitle_bits),
        'cells': {
            'year': str(row['year']) if row['year'] is not None else 'not recorded',
            'holdings': ('%d (%d extraction-inferred)' % (holdings, inferred)) if inferred else str(holdings),
            'report_type': row['report_type_code'] or 'not recorded',
        },
        'badges': badges,
        'links': [{'label': 'Open original PDF (hosted by CourtListener)', 'url': row['pdf_url']}],
    }


def listing(params=None):
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        return _unavailable(error, {'total': 0, 'page': 1, 'limit': 0, 'results': []})
    params = params if isinstance(params, dict) else {}
    conn = data['conn']
    q = _one(params, 'q')
    year = _one(params, 'year')
    entity_id = _one(params, 'entity_id')
    has_holdings = _one(params, 'has_holdings').casefold()

    where, args = ['1=1'], []
    if year:
        try:
            year_int = int(year)
        except ValueError:
            pass
        else:
            where.append('d.year = ?')
            args.append(year_int)
    if entity_id and _ENTITY_ID_RE.fullmatch(entity_id):
        where.append('d.entity_id = ?')
        args.append(entity_id)
    if has_holdings in ('yes', 'no'):
        where.append('d.investment_count %s 0' % ('>' if has_holdings == 'yes' else '='))
    if q:
        where.append('d.id IN (SELECT i.disclosure_id FROM investments i WHERE i.id IN '
                      '(SELECT rowid FROM investments_fts WHERE investments_fts MATCH ?))')
        # FTS5 query syntax reserves punctuation; quote the raw phrase so a search like "Johnson & Johnson"
        # or a name with a hyphen never raises a syntax error.
        args.append('"%s"' % q.replace('"', '""'))

    sql_where = ' AND '.join(where)
    try:
        total = conn.execute('SELECT COUNT(*) FROM disclosures d WHERE %s' % sql_where, args).fetchone()[0]
        limit, page = _int(params, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT), _int(params, 'page', 1, 1, 10 ** 6)
        offset = (page - 1) * limit
        rows = conn.execute(
            'SELECT d.*, j.name FROM disclosures d LEFT JOIN judges j ON j.entity_id = d.entity_id '
            'WHERE %s ORDER BY d.year DESC, d.id LIMIT ? OFFSET ?' % sql_where,
            args + [limit, offset],
        ).fetchall()
        years = [r[0] for r in conn.execute('SELECT DISTINCT year FROM disclosures WHERE year IS NOT NULL ORDER BY year DESC').fetchall()]
        with_holdings = conn.execute('SELECT COUNT(*) FROM disclosures WHERE investment_count > 0').fetchone()[0]
        without_holdings = conn.execute('SELECT COUNT(*) FROM disclosures WHERE investment_count = 0').fetchone()[0]
    except sqlite3.Error as error:
        return _unavailable(error, {'total': 0, 'page': 1, 'limit': 0, 'results': []})

    filters = [
        {'name': 'q', 'label': 'Search holding description', 'type': 'search'},
        {'name': 'entity_id', 'label': 'Judge (entity id)', 'type': 'text'},
        {'name': 'year', 'label': 'Filing year', 'type': 'select',
         'options': [{'value': str(y), 'label': str(y), 'count': None} for y in years]},
        {'name': 'has_holdings', 'label': 'Reportable holdings', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Has reportable holdings', 'count': with_holdings},
                     {'value': 'no', 'label': 'No reportable holdings on file', 'count': without_holdings}]},
    ]
    return {
        'available': True, 'total': total, 'page': page, 'limit': limit,
        'qualification': data['qualification'], 'filters': filters,
        'columns': [{'key': 'year', 'label': 'Year'}, {'key': 'holdings', 'label': 'Holdings'}, {'key': 'report_type', 'label': 'Report type code'}],
        'results': [_disc_result(r, conn) for r in rows],
    }


def detail(disclosure_id):
    if not isinstance(disclosure_id, str) or not _DISC_ID_RE.fullmatch(disclosure_id):
        return None
    try:
        data = _load()
        conn = data['conn']
        row = conn.execute(
            'SELECT d.*, j.name FROM disclosures d LEFT JOIN judges j ON j.entity_id = d.entity_id WHERE d.id = ?',
            (int(disclosure_id),),
        ).fetchone()
        if row is None:
            return None
        investments = conn.execute(
            'SELECT description, gross_value_code, income_code, redacted, has_inferred_values FROM investments '
            'WHERE disclosure_id = ? ORDER BY description COLLATE NOCASE', (row['id'],),
        ).fetchall()
        positions = conn.execute('SELECT position, organization_name, redacted FROM positions WHERE disclosure_id = ?', (row['id'],)).fetchall()
        reimbursements = conn.execute('SELECT source, location, purpose, redacted FROM reimbursements WHERE disclosure_id = ?', (row['id'],)).fetchall()
        gifts = conn.execute('SELECT source, description, redacted FROM gifts WHERE disclosure_id = ?', (row['id'],)).fetchall()
        debts = conn.execute('SELECT creditor_name, value_code, redacted FROM debts WHERE disclosure_id = ?', (row['id'],)).fetchall()
        agreements = conn.execute('SELECT date_raw, parties_and_terms, redacted FROM agreements WHERE disclosure_id = ?', (row['id'],)).fetchall()
        nii = conn.execute('SELECT date_raw, source_type, redacted FROM non_investment_income WHERE disclosure_id = ?', (row['id'],)).fetchall()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None

    facts = [
        ['Judge (judge_structured entity)', row['name'] or 'name not recorded'],
        ['CourtListener person id', row['cl_person_id']],
        ['Filing year', str(row['year']) if row['year'] is not None else 'not recorded'],
        ['Report type code (%s)' % REPORT_TYPE_NOTE, row['report_type_code'] or 'not recorded'],
        ['Amended', 'yes' if row['is_amended'] else 'no'],
        ['Page count (as recorded by CourtListener)', str(row['page_count']) if row['page_count'] is not None else 'not recorded'],
        ['Addendum redacted', 'yes' if row['addendum_redacted'] else 'no'],
        ['Snapshot', 'CourtListener bulk data, snapshot label 2026-06-30'],
    ]

    def _rows(cur, cols):
        return [[('yes' if v == 1 else 'no') if c == 'redacted' else ('' if v is None else str(v)) for c, v in zip(cols, r)] for r in cur]

    sections = []
    if investments:
        shown = investments[:DETAIL_INVESTMENTS_LIMIT]
        heading = 'Investment holdings (%d row%s) — description, gross value code and income code only; never a dollar figure' % (len(investments), '' if len(investments) == 1 else 's')
        if len(investments) > DETAIL_INVESTMENTS_LIMIT:
            heading += ' — showing %d of %d; full list in the listing search filtered to this judge' % (DETAIL_INVESTMENTS_LIMIT, len(investments))
        sections.append({
            'heading': heading,
            'header': ['Description', 'Gross value code', 'Income code', 'Redacted', 'Has inferred values'],
            'rows': [[i['description'] or '(redacted or not recorded)', i['gross_value_code'] or 'not reported',
                      i['income_code'] or 'not reported', 'yes' if i['redacted'] else 'no',
                      'yes' if i['has_inferred_values'] else 'no'] for i in shown],
        })
    else:
        sections.append({'heading': 'Investment holdings', 'text': 'Filing with no reportable holdings on file for this year — not the same as "no filing."'})
    if positions:
        sections.append({'heading': 'Outside positions (%d)' % len(positions), 'header': ['Position', 'Organization', 'Redacted'], 'rows': _rows(positions, ['position', 'organization_name', 'redacted'])})
    if reimbursements:
        sections.append({'heading': 'Reimbursements (%d) — no dollar amounts published' % len(reimbursements), 'header': ['Source', 'Location', 'Purpose', 'Redacted'], 'rows': _rows(reimbursements, ['source', 'location', 'purpose', 'redacted'])})
    if gifts:
        sections.append({'heading': 'Gifts (%d) — description only, dollar value never published' % len(gifts), 'header': ['Source', 'Description', 'Redacted'], 'rows': _rows(gifts, ['source', 'description', 'redacted'])})
    if debts:
        sections.append({'heading': 'Debts (%d) — value code only, never a dollar figure' % len(debts), 'header': ['Creditor', 'Value code', 'Redacted'], 'rows': _rows(debts, ['creditor_name', 'value_code', 'redacted'])})
    if agreements:
        sections.append({'heading': 'Agreements (%d)' % len(agreements), 'header': ['Date', 'Parties and terms', 'Redacted'], 'rows': _rows(agreements, ['date_raw', 'parties_and_terms', 'redacted'])})
    if nii:
        sections.append({'heading': 'Non-investment income sources (%d) — source only, dollar amount never published' % len(nii), 'header': ['Date', 'Source type', 'Redacted'], 'rows': _rows(nii, ['date_raw', 'source_type', 'redacted'])})

    qualification = (
        'Federally mandated public financial disclosure filed for calendar year %s, from a CourtListener bulk '
        'snapshot labelled 2026-06-30. This reports what was filed, not current holdings, and is not a '
        'conflict-of-interest finding. Value fields are publisher letter/number codes only, never a dollar '
        'estimate or total. The original PDF is hosted by CourtListener, not this application.'
    ) % (row['year'] if row['year'] is not None else 'unknown')

    return {
        'id': str(row['id']),
        'title': row['name'] or ('Judge (CourtListener person id %s)' % row['cl_person_id']),
        'subtitle': 'Financial disclosure, filing year %s' % (row['year'] if row['year'] is not None else 'not recorded'),
        'qualification': qualification,
        'facts': facts,
        'sections': sections,
        'links': [{'label': 'Open original PDF (hosted by CourtListener)', 'url': row['pdf_url']}],
    }


def for_judge(entity_id, folder=None):
    """Compact embedding hook for a judge profile page. Returns None only when the supplement itself is
    unavailable (fails the hash gate); a bridged judge with zero filings still returns a normal dict whose
    `state` says so explicitly -- that is a real, reportable fact about coverage, not an absence of data.
    """
    if not isinstance(entity_id, str) or not entity_id or not _ENTITY_ID_RE.fullmatch(entity_id):
        return None
    try:
        data = _load(folder)
        conn = data['conn']
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None
    try:
        judge = conn.execute('SELECT * FROM judges WHERE entity_id = ?', (entity_id,)).fetchone()
        if judge is None:
            bridged = conn.execute('SELECT 1 FROM bridged_entities WHERE entity_id = ?', (entity_id,)).fetchone()
            if bridged is None:
                return {
                    'available': True, 'entity_id': entity_id, 'state': 'not_bridged_to_courtlistener_person_id',
                    'qualification': 'This entity_id has no linked CourtListener person id bridge in '
                                      'judge_structured_20260919, so it cannot be looked up in this snapshot at all. '
                                      'This is different from a bridged judge with no filing on record.',
                    'years_filed': [], 'latest_year': None, 'holdings_count_by_year': {}, 'top_holdings_latest_year': [],
                    'total_holdings': 0, 'total_holdings_inferred': 0, 'link': '#judge-disclosures?entity_id=%s' % entity_id,
                }
            return {
                'available': True, 'entity_id': entity_id, 'state': 'no_filing_in_snapshot',
                'qualification': 'No filing found in the 2026-06-30 CourtListener financial disclosures snapshot for this judge. '
                                  'This is a coverage gap in the snapshot, not a statement that the judge holds no reportable assets.',
                'years_filed': [], 'latest_year': None, 'holdings_count_by_year': {}, 'top_holdings_latest_year': [],
                'total_holdings': 0, 'total_holdings_inferred': 0, 'link': '#judge-disclosures?entity_id=%s' % entity_id,
            }
        years = json.loads(judge['years_json'] or '[]')
        latest_year = judge['latest_year']
        by_year_rows = conn.execute(
            'SELECT d.year, COUNT(i.id) AS n FROM disclosures d LEFT JOIN investments i ON i.disclosure_id = d.id '
            'WHERE d.entity_id = ? GROUP BY d.year ORDER BY d.year', (entity_id,),
        ).fetchall()
        holdings_by_year = {str(r['year']): r['n'] for r in by_year_rows}
        total_holdings = judge['investment_count']
        # state reflects whether this judge has ANY reportable holdings on file across all years, not just the
        # most recent filing: a judge can file a holdings-free report in his latest year while earlier filings
        # list holdings, and labelling that judge "no reportable holdings" would misstate the record.
        state = 'filing_with_reportable_holdings' if total_holdings else 'filing_with_no_reportable_holdings'
        top_holdings = []
        top_holdings_note = None
        if latest_year is not None:
            top_rows = conn.execute(
                'SELECT i.description, i.has_inferred_values, i.redacted FROM investments i JOIN disclosures d ON d.id = i.disclosure_id '
                'WHERE d.entity_id = ? AND d.year = ? AND i.description IS NOT NULL AND i.description != "" '
                'ORDER BY i.description COLLATE NOCASE LIMIT ?',
                (entity_id, latest_year, TOP_HOLDINGS_LIMIT),
            ).fetchall()
            top_holdings = [{'description': r['description'], 'inferred': bool(r['has_inferred_values']),
                              'redacted': bool(r['redacted'])} for r in top_rows]
            if not top_holdings and total_holdings:
                top_holdings_note = ('no holdings reported in %s; see holdings_count_by_year for years that do '
                                      'have reportable holdings' % latest_year)
        return {
            'available': True, 'entity_id': entity_id, 'state': state,
            'qualification': (
                'Financial disclosures filed by this judge in a CourtListener bulk snapshot labelled 2026-06-30; '
                'reports what was filed for each stated year, not current holdings, and draws no conclusion about '
                'recusal or conflict of interest. Values are publisher codes, never a dollar figure.'
            ),
            'years_filed': years, 'latest_year': latest_year, 'holdings_count_by_year': holdings_by_year,
            'top_holdings_latest_year': top_holdings, 'top_holdings_latest_year_limit': TOP_HOLDINGS_LIMIT,
            'top_holdings_latest_year_note': top_holdings_note,
            'total_holdings': total_holdings, 'total_holdings_inferred': judge['investment_count_inferred'],
            'link': '#judge-disclosures?entity_id=%s' % entity_id,
        }
    except sqlite3.Error:
        return None
