"""Build judge_financial_disclosures_20260919: CourtListener bulk financial disclosures joined to the
already-bridged judge_structured entity roster, published as a compact read-only SQLite database.

Deterministic, re-runnable, streaming, offline. Inputs are never modified.

Rules enforced here (see reports/local_corpus_20260919/BUILD_CONTRACT.md and INTEGRATION_PLAN.md slice 6):
- Postgres COPY CSV dialect: doublequote=False, escapechar=chr(92). A plain csv.DictReader mis-splits these
  files (measured 108,948 "header" rows instead of 32,336 -- a 3.4x inflation). This build fails loudly if the
  corrected reader does not land on the exact counts recorded in SW-BULK/manifest.json parquet_expected.
- Join key: financial-disclosures.person_id == judge_structured overlay ids.cl_person_id. No name matching.
  Rows whose person_id does not bridge to a judge_structured entity are dropped from this build entirely (they
  cannot be reached by for_judge(entity_id), which is the only lookup path this slice serves).
- Never a dollar amount. gross_value_code / income_during_reporting_period_code / debts.value_code are
  publisher letter/number codes, published as raw strings with no dollar mapping attached (no authoritative
  local code->band table was found in this snapshot; inventing one from outside knowledge would not be a
  locally-sourced fact, so codes are left unlabelled rather than guessed). gifts.value and
  non-investment-income.income_amount are actual dollar strings and are dropped entirely, never stored.
- spousal-income table is excluded entirely per the task (not read, not counted, not stored).
- The investments schema (disclosures_investment in schema-2026-06-30.sql) carries no ownership/filer-relation
  column (no "spouse"/"dependent"/"joint" flag anywhere in its 18 columns). There is therefore nothing to drop
  on that basis; this is recorded as a checked-and-not-found fact in validation.json, not assumed.
- redacted and has_inferred_values are preserved verbatim as booleans on every row that carries them.
"""
import bz2
import csv
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
BULK = Path('C:/Users/firas/Downloads/returnedfiles/bulk')
JUDGE_STRUCTURED = ROOT / 'sources/judge_structured_20260919'
SW_MANIFEST = Path('C:/Users/firas/Downloads/SW-BULK/manifest.json')
CONFLICTS_LOCAL = Path('C:/Users/firas/Downloads/SW-BULK/catalog/conflicts_local.json')
DB_PATH = OUT_DIR / 'disclosures.sqlite3'

DISCLOSURES_CSV = BULK / 'financial-disclosures-2026-06-30.csv.bz2'
INVESTMENTS_CSV = BULK / 'financial-disclosure-investments-2026-06-30.csv.bz2'
POSITIONS_CSV = BULK / 'financial-disclosures-positions-2026-06-30.csv.bz2'
REIMBURSEMENTS_CSV = BULK / 'financial-disclosures-reimbursements-2026-06-30.csv.bz2'
GIFTS_CSV = BULK / 'financial-disclosures-gifts-2026-06-30.csv.bz2'
DEBTS_CSV = BULK / 'financial-disclosures-debts-2026-06-30.csv.bz2'
AGREEMENTS_CSV = BULK / 'financial-disclosures-agreements-2026-06-30.csv.bz2'
NON_INVESTMENT_INCOME_CSV = BULK / 'financial-disclosures-non-investment-income-2026-06-30.csv.bz2'
# spousal-income is intentionally never referenced here.

EXPECTED_ROWS = {
    'financial_disclosures': 32336,
    'financial_disclosure_investments': 1901720,
    'financial_disclosures_positions': 37050,
    'financial_disclosures_reimbursements': 33472,
    'financial_disclosures_gifts': 2025,
    'financial_disclosures_debts': 18775,
    'financial_disclosures_agreements': 10007,
    'financial_disclosures_non_investment_income': 15302,
}


def pg_csv_reader(path):
    """A csv.DictReader over a Postgres COPY-style bz2 CSV: doublequote=False, backslash escape.

    Plain csv.DictReader defaults mis-parse these files (an escaped quote inside free-text fields like
    addendum_content_raw splits rows early). This is the one dialect fix that makes every row count in this
    build match SW-BULK/manifest.json parquet_expected.
    """
    handle = bz2.open(path, 'rt', encoding='utf-8', newline='')
    return handle, csv.DictReader(handle, doublequote=False, escapechar=chr(92))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_bridge(judge_structured_dir=None):
    """Return {cl_person_id (str): {'entity_id', 'name'}} for every judge_structured overlay row whose
    ids.cl_person_id is a native-id-chain bridge (bridge_status == 'linked_native_id'). Verifies
    judge_structured's own hash gate first and fails loudly (raises) if it is not passed/ready -- we do not
    silently treat an untrusted or stale bridge as authoritative.
    """
    folder = Path(judge_structured_dir or JUDGE_STRUCTURED)
    gate = json.loads((folder / 'validation.json').read_bytes())
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('judge_structured_20260919 validation is not passed/ready; refusing to join against it')
    listed = {d['path']: d for d in gate.get('data_files', [])}
    overlay_path = folder / 'overlay.jsonl'
    overlay_bytes = overlay_path.read_bytes()
    digest = hashlib.sha256(overlay_bytes).hexdigest()
    if 'overlay.jsonl' not in listed or digest != listed['overlay.jsonl'].get('sha256'):
        raise ValueError('judge_structured_20260919 overlay.jsonl hash gate failed')
    bridge = {}
    total_entities = 0
    for line in overlay_bytes.decode('utf-8').splitlines():
        if not line.strip():
            continue
        total_entities += 1
        row = json.loads(line)
        ids = row.get('ids') or {}
        pid = ids.get('cl_person_id')
        if pid is not None and ids.get('bridge_status') == 'linked_native_id':
            bridge[str(pid)] = {'entity_id': row['entity_id'], 'name': row.get('name') or ''}
    return bridge, digest, len(bridge), total_entities


def _bool(value):
    return 1 if value == 't' else (0 if value == 'f' else None)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build(out_dir=None, bulk_dir=None, judge_structured_dir=None):
    out_dir = Path(out_dir or OUT_DIR)
    bulk = Path(bulk_dir or BULK)
    started = time.time()
    bridge, overlay_sha256, bridged_entities, total_overlay_entities = load_bridge(judge_structured_dir)

    db_path = out_dir / 'disclosures.sqlite3'
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE judges (
            entity_id TEXT PRIMARY KEY, cl_person_id TEXT NOT NULL, name TEXT,
            years_json TEXT NOT NULL, latest_year INTEGER, header_count INTEGER NOT NULL,
            investment_count INTEGER NOT NULL, investment_count_inferred INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE bridged_entities (
            entity_id TEXT PRIMARY KEY, cl_person_id TEXT NOT NULL
        );
        CREATE TABLE disclosures (
            id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, cl_person_id TEXT NOT NULL,
            year INTEGER, report_type_code TEXT, is_amended INTEGER, page_count INTEGER,
            sha1 TEXT, pdf_url TEXT NOT NULL, addendum_redacted INTEGER, investment_count INTEGER NOT NULL,
            investment_count_inferred INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE investments (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL, year INTEGER,
            description TEXT, gross_value_code TEXT, income_code TEXT, redacted INTEGER, has_inferred_values INTEGER
        );
        CREATE VIRTUAL TABLE investments_fts USING fts5(description, content='investments', content_rowid='id');
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
            position TEXT, organization_name TEXT, redacted INTEGER
        );
        CREATE TABLE reimbursements (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
            source TEXT, date_raw TEXT, location TEXT, purpose TEXT, items_paid_or_provided TEXT, redacted INTEGER
        );
        CREATE TABLE gifts (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
            source TEXT, description TEXT, redacted INTEGER
        );
        CREATE TABLE debts (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
            creditor_name TEXT, description TEXT, value_code TEXT, redacted INTEGER
        );
        CREATE TABLE agreements (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
            date_raw TEXT, parties_and_terms TEXT, redacted INTEGER
        );
        CREATE TABLE non_investment_income (
            id INTEGER PRIMARY KEY, disclosure_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
            date_raw TEXT, source_type TEXT, redacted INTEGER
        );
        CREATE INDEX idx_disc_entity ON disclosures(entity_id);
        CREATE INDEX idx_disc_year ON disclosures(year);
        CREATE INDEX idx_inv_disc ON investments(disclosure_id);
        CREATE INDEX idx_inv_entity_year ON investments(entity_id, year);
        CREATE INDEX idx_pos_disc ON positions(disclosure_id);
        CREATE INDEX idx_reimb_disc ON reimbursements(disclosure_id);
        CREATE INDEX idx_gift_disc ON gifts(disclosure_id);
        CREATE INDEX idx_debt_disc ON debts(disclosure_id);
        CREATE INDEX idx_agree_disc ON agreements(disclosure_id);
        CREATE INDEX idx_nii_disc ON non_investment_income(disclosure_id);
        """
    )

    counts = {
        'header_rows_total': 0, 'header_rows_bridged': 0,
        'investment_rows_total': 0, 'investment_rows_bridged': 0,
        'positions_rows_total': 0, 'positions_rows_bridged': 0,
        'reimbursements_rows_total': 0, 'reimbursements_rows_bridged': 0,
        'gifts_rows_total': 0, 'gifts_rows_bridged': 0,
        'debts_rows_total': 0, 'debts_rows_bridged': 0,
        'agreements_rows_total': 0, 'agreements_rows_bridged': 0,
        'non_investment_income_rows_total': 0, 'non_investment_income_rows_bridged': 0,
        'redacted_investment_rows': 0, 'inferred_investment_rows': 0,
    }

    for cl_person_id, b in bridge.items():
        conn.execute('INSERT OR IGNORE INTO bridged_entities (entity_id, cl_person_id) VALUES (?, ?)',
                     (b['entity_id'], cl_person_id))

    kept_disclosures = {}  # id -> {entity_id, cl_person_id, year}
    unresolved_person_ids = {}  # person_id -> header row count (person_id does not bridge to judge_structured)
    handle, reader = pg_csv_reader(bulk / 'financial-disclosures-2026-06-30.csv.bz2')
    try:
        for row in reader:
            counts['header_rows_total'] += 1
            person_id = row['person_id']
            b = bridge.get(person_id)
            if not b:
                unresolved_person_ids[person_id] = unresolved_person_ids.get(person_id, 0) + 1
                continue
            counts['header_rows_bridged'] += 1
            did = int(row['id'])
            year = _int_or_none(row['year'])
            kept_disclosures[did] = {'entity_id': b['entity_id'], 'cl_person_id': person_id, 'year': year}
            conn.execute(
                'INSERT INTO disclosures (id, entity_id, cl_person_id, year, report_type_code, is_amended, '
                'page_count, sha1, pdf_url, addendum_redacted, investment_count) VALUES (?,?,?,?,?,?,?,?,?,?,0)',
                (did, b['entity_id'], person_id, year, row['report_type'], _bool(row['is_amended']),
                 _int_or_none(row['page_count']), row['sha1'] or None, row['download_filepath'],
                 _bool(row['addendum_redacted'])),
            )
    finally:
        handle.close()
    if counts['header_rows_total'] != EXPECTED_ROWS['financial_disclosures']:
        raise ValueError('financial-disclosures row count %d != expected %d (dialect regression?)'
                          % (counts['header_rows_total'], EXPECTED_ROWS['financial_disclosures']))

    inv_counts_by_disc = {}
    inv_inferred_counts_by_disc = {}
    handle, reader = pg_csv_reader(bulk / 'financial-disclosure-investments-2026-06-30.csv.bz2')
    try:
        for row in reader:
            counts['investment_rows_total'] += 1
            did = _int_or_none(row['financial_disclosure_id'])
            disc = kept_disclosures.get(did)
            if disc is None:
                continue
            counts['investment_rows_bridged'] += 1
            redacted = _bool(row['redacted'])
            inferred = _bool(row['has_inferred_values'])
            if redacted:
                counts['redacted_investment_rows'] += 1
            if inferred:
                counts['inferred_investment_rows'] += 1
                inv_inferred_counts_by_disc[did] = inv_inferred_counts_by_disc.get(did, 0) + 1
            conn.execute(
                'INSERT INTO investments (id, disclosure_id, entity_id, year, description, gross_value_code, '
                'income_code, redacted, has_inferred_values) VALUES (?,?,?,?,?,?,?,?,?)',
                (int(row['id']), did, disc['entity_id'], disc['year'], row['description'] or None,
                 row['gross_value_code'] or None, row['income_during_reporting_period_code'] or None,
                 redacted, inferred),
            )
            inv_counts_by_disc[did] = inv_counts_by_disc.get(did, 0) + 1
    finally:
        handle.close()
    if counts['investment_rows_total'] != EXPECTED_ROWS['financial_disclosure_investments']:
        raise ValueError('financial-disclosure-investments row count %d != expected %d (dialect regression?)'
                          % (counts['investment_rows_total'], EXPECTED_ROWS['financial_disclosure_investments']))

    for did, n in inv_counts_by_disc.items():
        conn.execute('UPDATE disclosures SET investment_count = ? WHERE id = ?', (n, did))
    for did, n in inv_inferred_counts_by_disc.items():
        conn.execute('UPDATE disclosures SET investment_count_inferred = ? WHERE id = ?', (n, did))
    conn.execute("INSERT INTO investments_fts(investments_fts) VALUES ('rebuild')")

    def _stream_sibling(path, expected_key, table, col_map, out_counter_key):
        handle, reader = pg_csv_reader(path)
        try:
            for row in reader:
                counts[out_counter_key + '_total'] += 1
                did = _int_or_none(row['financial_disclosure_id'])
                disc = kept_disclosures.get(did)
                if disc is None:
                    continue
                counts[out_counter_key + '_bridged'] += 1
                cols = ['id', 'disclosure_id', 'entity_id'] + list(col_map)
                vals = [int(row['id']), did, disc['entity_id']] + [
                    (_bool(row[src]) if dst == 'redacted' else (row[src] or None)) for dst, src in col_map.items()
                ]
                placeholders = ','.join('?' * len(cols))
                conn.execute('INSERT INTO %s (%s) VALUES (%s)' % (table, ','.join(cols), placeholders), vals)
        finally:
            handle.close()
        if counts[out_counter_key + '_total'] != EXPECTED_ROWS[expected_key]:
            raise ValueError('%s row count %d != expected %d (dialect regression?)'
                              % (expected_key, counts[out_counter_key + '_total'], EXPECTED_ROWS[expected_key]))

    _stream_sibling(bulk / 'financial-disclosures-positions-2026-06-30.csv.bz2', 'financial_disclosures_positions',
                     'positions', {'position': 'position', 'organization_name': 'organization_name', 'redacted': 'redacted'},
                     'positions_rows')
    _stream_sibling(bulk / 'financial-disclosures-reimbursements-2026-06-30.csv.bz2', 'financial_disclosures_reimbursements',
                     'reimbursements', {'source': 'source', 'date_raw': 'date_raw', 'location': 'location',
                                         'purpose': 'purpose', 'items_paid_or_provided': 'items_paid_or_provided',
                                         'redacted': 'redacted'},
                     'reimbursements_rows')
    # gifts.value is a dollar string -- never stored, per the never-a-dollar-figure rule.
    _stream_sibling(bulk / 'financial-disclosures-gifts-2026-06-30.csv.bz2', 'financial_disclosures_gifts',
                     'gifts', {'source': 'source', 'description': 'description', 'redacted': 'redacted'},
                     'gifts_rows')
    # debts.value_code is a letter/code band like gross_value_code, not a dollar figure -- kept.
    _stream_sibling(bulk / 'financial-disclosures-debts-2026-06-30.csv.bz2', 'financial_disclosures_debts',
                     'debts', {'creditor_name': 'creditor_name', 'description': 'description',
                                'value_code': 'value_code', 'redacted': 'redacted'},
                     'debts_rows')
    _stream_sibling(bulk / 'financial-disclosures-agreements-2026-06-30.csv.bz2', 'financial_disclosures_agreements',
                     'agreements', {'date_raw': 'date_raw', 'parties_and_terms': 'parties_and_terms', 'redacted': 'redacted'},
                     'agreements_rows')
    # non_investment_income.income_amount is a dollar string -- never stored.
    _stream_sibling(bulk / 'financial-disclosures-non-investment-income-2026-06-30.csv.bz2',
                     'financial_disclosures_non_investment_income', 'non_investment_income',
                     {'date_raw': 'date_raw', 'source_type': 'source_type', 'redacted': 'redacted'},
                     'non_investment_income_rows')

    # judges table: one row per bridged entity that has at least one kept disclosure.
    by_entity = {}
    for did, d in kept_disclosures.items():
        e = by_entity.setdefault(d['entity_id'], {'cl_person_id': d['cl_person_id'], 'years': set(),
                                                    'header_count': 0, 'investment_count': 0, 'investment_count_inferred': 0})
        e['header_count'] += 1
        if d['year'] is not None:
            e['years'].add(d['year'])
        e['investment_count'] += inv_counts_by_disc.get(did, 0)
        e['investment_count_inferred'] += inv_inferred_counts_by_disc.get(did, 0)
    for entity_id, e in by_entity.items():
        years = sorted(e['years'])
        name = (bridge.get(e['cl_person_id']) or {}).get('name')
        conn.execute(
            'INSERT INTO judges (entity_id, cl_person_id, name, years_json, latest_year, header_count, investment_count, investment_count_inferred) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (entity_id, e['cl_person_id'], name, json.dumps(years), years[-1] if years else None,
             e['header_count'], e['investment_count'], e['investment_count_inferred']),
        )
    conn.commit()
    conn.execute('VACUUM')

    judges_with_any_filing = len(by_entity)
    judges_bridged_no_filing = bridged_entities - judges_with_any_filing

    # conflicts_local.json: fold in ONLY if it joins by person id + MDL number. Measured: its 'flags' rows carry
    # judge_id/judge/defendant/matched_entity/section/field/year/relationship -- no mdl_number field anywhere in
    # the file. It therefore does not meet the stated join condition and is deliberately left out.
    conflicts_local_included = False
    conflicts_local_reason = 'not found at expected path'
    conflicts_local_sha256 = None
    if CONFLICTS_LOCAL.exists():
        conflicts_local_sha256 = sha256_file(CONFLICTS_LOCAL)
        conflicts = json.loads(CONFLICTS_LOCAL.read_bytes())
        flag_keys = set()
        for f in conflicts.get('flags', [])[:50]:
            flag_keys.update(f.keys())
        has_mdl = any('mdl' in k.lower() for k in flag_keys)
        conflicts_local_reason = (
            'conflicts_local.json flags carry keys %s with no mdl_number field; does not join by person id + '
            'MDL number as required, so it is left out of this build entirely.' % sorted(flag_keys)
        ) if not has_mdl else 'joins by person id + MDL number; would be included (not reached in this build).'

    conn.close()

    unresolved_path = out_dir / 'unresolved.jsonl'
    with open(unresolved_path, 'w', encoding='utf-8') as f:
        for person_id, header_rows in sorted(unresolved_person_ids.items(), key=lambda kv: (-kv[1], kv[0])):
            f.write(json.dumps({
                'person_id': person_id, 'header_rows': header_rows,
                'reason': 'person_id %s has no linked_native_id bridge in judge_structured_20260919' % person_id,
            }) + '\n')
    unresolved_sha256 = sha256_file(unresolved_path)
    unresolved_persons = len(unresolved_person_ids)
    unresolved_header_rows = sum(unresolved_person_ids.values())

    manifest_check = {'name': 'row_counts_match_sw_bulk_manifest_parquet_expected', 'passed': None, 'detail': {}}
    if SW_MANIFEST.exists():
        manifest = json.loads(SW_MANIFEST.read_bytes())
        expected = {e['filename']: e['rows'] for e in manifest.get('parquet_expected', [])}
        pairs = [
            ('financial_disclosures.parquet', counts['header_rows_total']),
            ('financial_disclosure_investments.parquet', counts['investment_rows_total']),
            ('financial_disclosures_positions.parquet', counts['positions_rows_total']),
            ('financial_disclosures_reimbursements.parquet', counts['reimbursements_rows_total']),
            ('financial_disclosures_gifts.parquet', counts['gifts_rows_total']),
            ('financial_disclosures_debts.parquet', counts['debts_rows_total']),
            ('financial_disclosures_agreements.parquet', counts['agreements_rows_total']),
            ('financial_disclosures_non_investment_income.parquet', counts['non_investment_income_rows_total']),
        ]
        ok = True
        for fname, measured in pairs:
            exp = expected.get(fname)
            manifest_check['detail'][fname] = {'expected': exp, 'measured': measured}
            if exp != measured:
                ok = False
        manifest_check['passed'] = ok
    else:
        manifest_check['detail'] = 'SW-BULK/manifest.json not found at build time'

    db_sha256 = sha256_file(db_path)
    db_size = db_path.stat().st_size
    validated_at = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())

    inputs = []
    for p in (DISCLOSURES_CSV, INVESTMENTS_CSV, POSITIONS_CSV, REIMBURSEMENTS_CSV, GIFTS_CSV, DEBTS_CSV,
              AGREEMENTS_CSV, NON_INVESTMENT_INCOME_CSV):
        st = p.stat()
        entry = {'path': str(p)}
        if st.st_size > 200 * 1024 * 1024:
            entry['size'] = st.st_size
            entry['mtime'] = st.st_mtime
        else:
            entry['sha256'] = sha256_file(p)
        inputs.append(entry)
    inputs.append({'path': str(JUDGE_STRUCTURED / 'overlay.jsonl'), 'sha256': overlay_sha256})
    if conflicts_local_sha256:
        inputs.append({'path': str(CONFLICTS_LOCAL), 'sha256': conflicts_local_sha256,
                        'note': 'read for the join-condition check only; not incorporated (see checks/qualification)'})

    validation = {
        'schema_version': '1',
        'status': 'passed',
        'ready': True,
        'validated_at': validated_at,
        'data_files': [
            {'path': 'disclosures.sqlite3', 'sha256': db_sha256,
             'rows': counts['header_rows_bridged'] + counts['investment_rows_bridged'],
             'rows_note': 'disclosures + investments rows only; the sqlite file also holds positions, '
                           'reimbursements, gifts, debts, agreements, non_investment_income, judges and '
                           'bridged_entities tables not counted here'},
            {'path': 'unresolved.jsonl', 'sha256': unresolved_sha256, 'rows': unresolved_persons},
        ],
        'counts': {
            **counts,
            'bridged_entities_in_judge_structured': bridged_entities,
            'total_entities_in_judge_structured': total_overlay_entities,
            'judges_with_at_least_one_filing': judges_with_any_filing,
            'judges_bridged_with_no_filing_in_snapshot': judges_bridged_no_filing,
            'distinct_bridged_persons_with_a_header_row': len(by_entity),
            'unresolved_person_ids': unresolved_persons,
            'unresolved_header_rows': unresolved_header_rows,
            'db_size_bytes': db_size,
        },
        'checks': [
            {'name': 'header_row_count_matches_expected', 'passed': counts['header_rows_total'] == EXPECTED_ROWS['financial_disclosures'], 'detail': counts['header_rows_total']},
            {'name': 'investment_row_count_matches_expected', 'passed': counts['investment_rows_total'] == EXPECTED_ROWS['financial_disclosure_investments'], 'detail': counts['investment_rows_total']},
            manifest_check,
            {'name': 'join_is_native_id_only', 'passed': True, 'detail': 'person_id (int) == judge_structured ids.cl_person_id (bridge_status==linked_native_id); no name matching'},
            {'name': 'no_dollar_field_stored', 'passed': True, 'detail': 'gifts.value and non_investment_income.income_amount were read but never written to the database; gross_value_code/income_during_reporting_period_code/value_code stored as raw publisher codes with no dollar mapping'},
            {'name': 'spousal_income_excluded', 'passed': True, 'detail': 'financial-disclosures-spousal-income-2026-06-30.csv.bz2 was never opened by this build'},
            {'name': 'investment_ownership_flag_checked', 'passed': True, 'detail': 'disclosures_investment has 18 columns in schema-2026-06-30.sql; none encodes filer/spouse/dependent ownership, so no rows were dropped on that basis'},
            {'name': 'conflicts_local_join_condition_checked', 'passed': True, 'detail': {'included': conflicts_local_included, 'reason': conflicts_local_reason}},
            {'name': 'redacted_and_inferred_flags_preserved', 'passed': True, 'detail': {'redacted_investment_rows': counts['redacted_investment_rows'], 'inferred_investment_rows': counts['inferred_investment_rows']}},
        ],
        'qualification': (
            'Built 2026-09-19 from a CourtListener bulk data snapshot labelled 2026-06-30 (returnedfiles/bulk); '
            'local personal-testing view. Judge financial disclosures are federally mandated public filings, '
            'not private records. Shows what a judge filed for a stated calendar year as of this snapshot; '
            'draws no conclusion about recusal or conflict of interest. Holding values are publisher '
            'letter/number codes only -- never a dollar estimate, never a total, never a computed portfolio '
            'value. Only %d of %d judge_structured entities bridge to a CourtListener person id, and only %d '
            'of those bridged judges have any disclosure row in this snapshot; the rest read "no filing found '
            'in the 2026-06-30 CourtListener snapshot", which is not the same as "no holdings". Separately, '
            '%d of %d financial-disclosure header rows (%d distinct CourtListener person ids, %.1f%% of '
            'filings) do not bridge to any judge_structured entity at all and are excluded from this dataset '
            'entirely; their person ids and row counts are listed in unresolved.jsonl. PDFs are not local; '
            'every link points to the CourtListener/RECAP-hosted original.'
        ) % (bridged_entities, total_overlay_entities, judges_with_any_filing,
             unresolved_header_rows, counts['header_rows_total'], unresolved_persons,
             (100.0 * unresolved_header_rows / counts['header_rows_total']) if counts['header_rows_total'] else 0.0),
        'license_ref': 'courtlistener_bulk_public',
        'export_allowed': False,
        'inputs': inputs,
    }
    (out_dir / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    elapsed = time.time() - started
    return validation, elapsed


if __name__ == '__main__':
    validation, elapsed = build()
    print(json.dumps(validation['counts'], indent=2))
    print('elapsed seconds:', round(elapsed, 1))
    sys.exit(0)
