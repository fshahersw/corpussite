"""Build cpsc_injury_data.sqlite3 from two local, read-only CPSC files:

  B. C:/Users/firas/Downloads/returnedfiles/neiss2025.xlsx
     CPSC NEISS (National Electronic Injury Surveillance System) calendar-year-2025 public data file.
     Two sheets: NEISS_2025 (one row per sampled emergency-department visit) and NEISS_FMT (the
     publisher's own SAS-style code table: Format name, Starting value, Ending value, Format value
     label). Streamed with openpyxl read_only=True; nothing is loaded fully into memory.

  A. C:/Users/firas/Downloads/returnedfiles/IncidentReports.csv
     CPSC SaferProducts.gov "Publicly Available Consumer Product Safety Information Database" incident
     report export (consumer-submitted, unverified by CPSC). Confirmed NOT already used by the MVP: its
     sha256 differs from the local CPSC file already indexed in sources/agency_safety_20260919
     (that file is Recalls.csv, a different, already-ingested CPSC dataset of recall records).

Hard rules this build must not violate (from the task and BUILD_CONTRACT.md):
  - NEISS rows are a national probability SAMPLE, never a count/rate/trend computed by this build. The
    publisher's statistical Weight column is stored and printed exactly as found; nothing is aggregated.
  - A product/body-part/diagnosis/etc. code is translated to a label ONLY via the NEISS_FMT table that
    ships inside this same file. A code with no exact-value row in NEISS_FMT is left as a bare code.
  - SaferProducts rows are consumer-submitted and unverified; any submitter identity/contact column must
    be dropped and the drop counted (classify_saferproducts_columns implements the check; the as-shipped
    export has no such column, so the count is expected to be zero, and the check still runs so a future
    export gaining one cannot slip through unnoticed).

Re-runnable: deletes and rebuilds cpsc_injury_data.sqlite3 from scratch every run. Offline; touches no
network. Both inputs are opened read-only and never modified.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEISS_XLSX = Path('C:/Users/firas/Downloads/returnedfiles/neiss2025.xlsx')
INCIDENTS_CSV = Path('C:/Users/firas/Downloads/returnedfiles/IncidentReports.csv')
DB_PATH = HERE / 'cpsc_injury_data.sqlite3'
VALIDATION_PATH = HERE / 'validation.json'
BATCH = 4000

# ----------------------------------------------------------------------------------- pure helpers (TDD)


def load_neiss_fmt(fmt_rows):
    """rows: iterable of (format_name, start_str, end_str, label_str) as printed in NEISS_FMT.

    Returns {format_name: {int_code: label}} built ONLY from exact-value rows (start == end, both
    parseable as int). A range row (e.g. AGELTTWO 2-120) is not a per-value label and is never added."""
    out = {}
    for name, start, end, label in fmt_rows:
        if name is None:
            continue
        start_s, end_s = str(start).strip(), str(end).strip()
        if start_s != end_s:
            continue  # a range, not a single-value label
        try:
            code = int(start_s)
        except ValueError:
            continue  # non-numeric (e.g. ".", meaning "NA before 2019")
        out.setdefault(str(name).strip(), {})[code] = str(label).strip() if label is not None else None
    return out


def neiss_label(fmt, format_name, code):
    """code -> label via fmt[format_name], or None when the format/code is absent (caller leaves the
    bare code displayed; never guessed, never invented)."""
    if code is None:
        return None
    try:
        code_i = int(code)
    except (TypeError, ValueError):
        return None
    return fmt.get(format_name, {}).get(code_i)


def age_display(age_code):
    """NEISS Age coding: 0 = unknown; 1-120 = whole years; 201-223 = 1-23 months (children under 2).
    Deterministic per-row translation of the publisher's own code; not an estimate."""
    if age_code is None:
        return None
    try:
        code = int(age_code)
    except (TypeError, ValueError):
        return None
    if code == 0:
        return 'Unknown'
    if 1 <= code <= 120:
        return '%d year' % code if code == 1 else '%d years' % code
    if 201 <= code <= 223:
        months = code - 200
        return '1 month' if months == 1 else '%d months' % months
    return 'Unknown (code %d)' % code


def age_band(age_code):
    """A fixed, documented bucketing of the coded age value into a small set of navigational bands.
    Purely mechanical (no imputation, no population estimate) -- a per-row categorical label, not a
    statistic computed over the sample."""
    if age_code is None:
        return 'Unknown'
    try:
        code = int(age_code)
    except (TypeError, ValueError):
        return 'Unknown'
    if code == 0:
        return 'Unknown'
    if 201 <= code <= 211:
        return 'Under 1 year'
    if 212 <= code <= 223:
        years = 1
    elif 1 <= code <= 120:
        years = code
    else:
        return 'Unknown'
    bounds = [(1, 4, '1-4 years'), (5, 9, '5-9 years'), (10, 14, '10-14 years'), (15, 19, '15-19 years'),
              (20, 24, '20-24 years'), (25, 34, '25-34 years'), (35, 44, '35-44 years'), (45, 54, '45-54 years'),
              (55, 64, '55-64 years'), (65, 74, '65-74 years')]
    for low, high, label in bounds:
        if low <= years <= high:
            return label
    if years >= 75:
        return '75+ years'
    return 'Unknown'


_PII_PATTERNS = (
    re.compile(r'submitter.*(name|email|e-mail|phone|address|contact)', re.I),
    re.compile(r'\be-?mail\b', re.I),
    re.compile(r'\bphone\b', re.I),
    re.compile(r'\btelephone\b', re.I),
    re.compile(r'\bssn\b', re.I),
    re.compile(r'social security', re.I),
)


def classify_saferproducts_columns(header):
    """Split a SaferProducts.gov CSV header into (kept, dropped) where "dropped" is any column that
    looks like submitter identity/contact data. The as-shipped export has none; this still runs on every
    build so a future export gaining such a column is caught, not silently included."""
    kept, dropped = [], []
    for col in header:
        if any(p.search(col) for p in _PII_PATTERNS):
            dropped.append(col)
        else:
            kept.append(col)
    return kept, dropped


_MDY = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})$')


def parse_mdy(value):
    """'M/D/YYYY' -> 'YYYY-MM-DD', or None (never raises, never guesses on a malformed string)."""
    if not value:
        return None
    match = _MDY.match(str(value).strip())
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


# ----------------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE neiss_fmt (
  format_name TEXT NOT NULL, start_value TEXT NOT NULL, end_value TEXT NOT NULL, label TEXT
);

CREATE TABLE neiss_cases (
  rid INTEGER PRIMARY KEY,
  case_number TEXT UNIQUE NOT NULL,
  treatment_date TEXT, year INTEGER, month INTEGER,
  age_code INTEGER, age_display TEXT, age_band TEXT,
  sex_code INTEGER, sex_label TEXT,
  race_code INTEGER, race_label TEXT, other_race TEXT,
  hispanic_code INTEGER, hispanic_label TEXT,
  body_part_code INTEGER, body_part_label TEXT,
  diagnosis_code INTEGER, diagnosis_label TEXT, other_diagnosis TEXT,
  body_part_2_code INTEGER, body_part_2_label TEXT,
  diagnosis_2_code INTEGER, diagnosis_2_label TEXT, other_diagnosis_2 TEXT,
  disposition_code INTEGER, disposition_label TEXT,
  location_code INTEGER, location_label TEXT,
  fire_involvement_code INTEGER, fire_involvement_label TEXT,
  product_1_code INTEGER, product_1_label TEXT,
  product_2_code INTEGER, product_2_label TEXT,
  product_3_code INTEGER, product_3_label TEXT,
  alcohol_code INTEGER, alcohol_label TEXT,
  drug_code INTEGER, drug_label TEXT,
  narrative TEXT,
  stratum TEXT, psu TEXT, weight REAL
);
CREATE INDEX ix_neiss_year_month ON neiss_cases(year, month);
CREATE INDEX ix_neiss_body_part ON neiss_cases(body_part_code);
CREATE INDEX ix_neiss_diagnosis ON neiss_cases(diagnosis_code);
CREATE INDEX ix_neiss_disposition ON neiss_cases(disposition_code);
CREATE INDEX ix_neiss_sex ON neiss_cases(sex_code);
CREATE INDEX ix_neiss_age_band ON neiss_cases(age_band);
CREATE INDEX ix_neiss_product_1 ON neiss_cases(product_1_code);
CREATE INDEX ix_neiss_treatment_date ON neiss_cases(treatment_date DESC, rid);
-- Composite (filter column, date DESC) indexes: a single-column filter plus the default date-descending
-- listing order otherwise forces SQLite to sort the whole filtered result set in a temp B-tree even
-- though the filter itself is a fast covering-index lookup (measured ~2-3s for e.g. sex_code=1, which
-- alone matches 221,547 of 410,201 rows). These let one filter's SEARCH also satisfy the ORDER BY.
CREATE INDEX ix_neiss_sex_date ON neiss_cases(sex_code, treatment_date DESC);
CREATE INDEX ix_neiss_body_part_date ON neiss_cases(body_part_code, treatment_date DESC);
CREATE INDEX ix_neiss_diagnosis_date ON neiss_cases(diagnosis_code, treatment_date DESC);
CREATE INDEX ix_neiss_disposition_date ON neiss_cases(disposition_code, treatment_date DESC);
CREATE INDEX ix_neiss_age_band_date ON neiss_cases(age_band, treatment_date DESC);
CREATE INDEX ix_neiss_year_date ON neiss_cases(year, treatment_date DESC);
CREATE INDEX ix_neiss_month_date ON neiss_cases(month, treatment_date DESC);
CREATE INDEX ix_neiss_product1_date ON neiss_cases(product_1_code, treatment_date DESC);

CREATE VIRTUAL TABLE neiss_fts USING fts5(
  narrative, product_1_label, product_2_label, product_3_label,
  content='neiss_cases', content_rowid='rid'
);

CREATE TABLE saferproducts_incidents (
  rid INTEGER PRIMARY KEY,
  report_no TEXT UNIQUE NOT NULL,
  report_date TEXT, report_date_iso TEXT,
  sent_to_manufacturer_date TEXT, sent_to_manufacturer_date_iso TEXT,
  publication_date TEXT, publication_date_iso TEXT,
  category_of_submitter TEXT,
  product_description TEXT, product_category TEXT, product_sub_category TEXT, product_type TEXT, product_code TEXT,
  manufacturer_name TEXT, brand TEXT, model_name_or_number TEXT, serial_number TEXT, upc TEXT,
  date_manufactured TEXT, manufacturer_date_code TEXT,
  retailer TEXT, retailer_state TEXT, purchase_date TEXT, purchase_date_iso TEXT, purchase_date_is_estimate TEXT,
  incident_description TEXT,
  city TEXT, state TEXT, zip TEXT, location TEXT,
  victim_severity TEXT, victim_sex TEXT, relation_to_victim TEXT, victim_age TEXT,
  submitter_has_product TEXT, product_damaged_before TEXT, damage_description TEXT, damage_repaired TEXT,
  product_modified_before TEXT, contacted_manufacturer TEXT, plan_to_contact TEXT, answer_explanation TEXT,
  company_comments TEXT, associated_report_numbers TEXT,
  year INTEGER, month INTEGER
);
CREATE INDEX ix_sp_year_month ON saferproducts_incidents(year, month);
CREATE INDEX ix_sp_category ON saferproducts_incidents(product_category);
CREATE INDEX ix_sp_state ON saferproducts_incidents(state);
CREATE INDEX ix_sp_submitter ON saferproducts_incidents(category_of_submitter);
CREATE INDEX ix_sp_report_date ON saferproducts_incidents(report_date_iso DESC, rid);
CREATE INDEX ix_sp_category_date ON saferproducts_incidents(product_category, report_date_iso DESC);
CREATE INDEX ix_sp_state_date ON saferproducts_incidents(state, report_date_iso DESC);
CREATE INDEX ix_sp_submitter_date ON saferproducts_incidents(category_of_submitter, report_date_iso DESC);
CREATE INDEX ix_sp_year_date ON saferproducts_incidents(year, report_date_iso DESC);

CREATE VIRTUAL TABLE saferproducts_fts USING fts5(
  incident_description, product_description, manufacturer_name, brand,
  content='saferproducts_incidents', content_rowid='rid'
);

-- Precomputed filter-option counts (mirrors delivery/archive-directory/federal_register_history.py's
-- facet_counts table). Grouping live, per request, on a *_label column denormalised onto the 410k-row
-- neiss_cases table forced SQLite off its covering indexes into a full table scan every time (measured
-- 2-9 seconds per filter, ~2.5s just for the unfiltered default listing's ORDER BY); a handful of rows
-- looked up here by (dataset, facet) is effectively instant regardless of the source table's size.
CREATE TABLE facet_counts (
  dataset TEXT NOT NULL, facet TEXT NOT NULL, value TEXT NOT NULL, label TEXT, n INTEGER NOT NULL,
  PRIMARY KEY (dataset, facet, value)
);
"""

FACET_SPECS = (
    ('neiss', 'year', 'year', None, 'neiss_cases'),
    ('neiss', 'month', 'month', None, 'neiss_cases'),
    ('neiss', 'age_band', 'age_band', None, 'neiss_cases'),
    ('neiss', 'body_part', 'body_part_code', 'body_part_label', 'neiss_cases'),
    ('neiss', 'diagnosis', 'diagnosis_code', 'diagnosis_label', 'neiss_cases'),
    ('neiss', 'disposition', 'disposition_code', 'disposition_label', 'neiss_cases'),
    ('neiss', 'sex', 'sex_code', 'sex_label', 'neiss_cases'),
    ('saferproducts', 'product_category', 'product_category', None, 'saferproducts_incidents'),
    ('saferproducts', 'state', 'state', None, 'saferproducts_incidents'),
    ('saferproducts', 'year', 'year', None, 'saferproducts_incidents'),
    ('saferproducts', 'category_of_submitter', 'category_of_submitter', None, 'saferproducts_incidents'),
)


def build_facet_counts(conn, log):
    total_rows = 0
    for dataset, facet, value_col, label_col, table in FACET_SPECS:
        if label_col:
            sql = f'SELECT {value_col}, {label_col}, count(*) FROM {table} WHERE {value_col} IS NOT NULL GROUP BY {value_col}, {label_col}'
        else:
            sql = f'SELECT {value_col}, NULL, count(*) FROM {table} WHERE {value_col} IS NOT NULL GROUP BY {value_col}'
        rows = conn.execute(sql).fetchall()
        conn.executemany('INSERT INTO facet_counts(dataset, facet, value, label, n) VALUES (?,?,?,?,?)',
                          [(dataset, facet, str(v), (lbl if lbl is not None else str(v)), n) for v, lbl, n in rows])
        total_rows += len(rows)
    log('facet_counts: %d rows across %d facets' % (total_rows, len(FACET_SPECS)))

# NEISS_2025 sheet column order, as verified against the file on 2026-09-19.
NEISS_COLUMNS = ('CPSC_Case_Number', 'Treatment_Date', 'Age', 'Sex', 'Race', 'Other_Race', 'Hispanic',
                  'Body_Part', 'Diagnosis', 'Other_Diagnosis', 'Body_Part_2', 'Diagnosis_2', 'Other_Diagnosis_2',
                  'Disposition', 'Location', 'Fire_Involvement', 'Product_1', 'Product_2', 'Product_3',
                  'Alcohol', 'Drug', 'Narrative_1', 'Stratum', 'PSU', 'Weight')

# IncidentReports.csv real header (row 2; row 1 is a publisher disclaimer), as verified 2026-09-19.
SAFERPRODUCTS_HEADER = ['Report No.', 'Report Date', 'Sent to Manufacturer / Importer / Private Labeler',
                         'Publication Date', 'Category of Submitter', 'Product Description', 'Product Category',
                         'Product Sub Category', 'Product Type', 'Product Code',
                         'Manufacturer / Importer / Private Labeler Name', 'Brand', 'Model Name or Number',
                         'Serial Number', 'UPC', 'Date Manufactured', 'Manufacturer Date Code', 'Retailer',
                         'Retailer State', 'Purchase Date', 'Purchase Date Is Estimate', 'Incident Description',
                         'City', 'State', 'ZIP', 'Location', '(Primary) Victim Severity', "(Primary) Victim's Sex",
                         'My Relation To The (Primary) Victim', "(Primary) Victim's Age (years)",
                         'Submitter Has Product', 'Product Was Damaged Before Incident', 'Damage Description',
                         'Damage Repaired', 'Product Was Modified Before Incident',
                         'Have You Contacted The Manufacturer', 'If Not Do You Plan To', 'Answer Explanation',
                         'Company Comments', 'Associated Report Numbers']


def _int_or_none(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_neiss(conn, xlsx_path, log):
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    if set(wb.sheetnames) < {'NEISS_2025', 'NEISS_FMT'}:
        raise SystemExit('neiss2025.xlsx is missing an expected sheet: found %r' % (wb.sheetnames,))

    fmt_sheet = wb['NEISS_FMT']
    fmt_rows_raw = []
    for i, row in enumerate(fmt_sheet.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        if row is None or row[0] is None:
            continue
        fmt_rows_raw.append((row[0], row[1], row[2], row[3]))
    fmt = load_neiss_fmt(fmt_rows_raw)
    conn.executemany('INSERT INTO neiss_fmt(format_name, start_value, end_value, label) VALUES (?,?,?,?)',
                      [(str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip(), r[3]) for r in fmt_rows_raw])
    log('neiss_fmt: %d rows, %d format names' % (len(fmt_rows_raw), len(fmt)))

    data_sheet = wb['NEISS_2025']
    header = None
    batch = []
    inserted = 0

    def lookup(name, code):
        return neiss_label(fmt, name, code)

    for i, row in enumerate(data_sheet.iter_rows(values_only=True)):
        if i == 0:
            header = row
            if tuple(header) != NEISS_COLUMNS:
                raise SystemExit('NEISS_2025 header does not match the expected column order: %r' % (header,))
            continue
        if row is None or row[0] is None:
            continue
        (case_number, treatment_date, age, sex, race, other_race, hispanic, body_part, diagnosis, other_diagnosis,
         body_part_2, diagnosis_2, other_diagnosis_2, disposition, location, fire, product_1, product_2, product_3,
         alcohol, drug, narrative, stratum, psu, weight) = row
        if isinstance(treatment_date, (datetime.datetime, datetime.date)):
            treat_iso = treatment_date.date().isoformat() if isinstance(treatment_date, datetime.datetime) else treatment_date.isoformat()
            year, month = treatment_date.year, treatment_date.month
        else:
            treat_iso, year, month = None, None, None
        age_i = _int_or_none(age)
        batch.append((
            str(int(case_number)), treat_iso, year, month,
            age_i, age_display(age_i), age_band(age_i),
            _int_or_none(sex), lookup('SEX', sex),
            _int_or_none(race), lookup('RACE', race), (other_race or None),
            _int_or_none(hispanic), lookup('HISP', hispanic),
            _int_or_none(body_part), lookup('BDYPT', body_part),
            _int_or_none(diagnosis), lookup('DIAG', diagnosis), (other_diagnosis or None),
            _int_or_none(body_part_2), lookup('BDYPT', body_part_2),
            _int_or_none(diagnosis_2), lookup('DIAG', diagnosis_2), (other_diagnosis_2 or None),
            _int_or_none(disposition), lookup('DISP', disposition),
            _int_or_none(location), lookup('LOC', location),
            _int_or_none(fire), lookup('FIRE', fire),
            _int_or_none(product_1), lookup('PROD', product_1),
            _int_or_none(product_2), lookup('PROD', product_2),
            _int_or_none(product_3), lookup('PROD', product_3),
            _int_or_none(alcohol), lookup('ALC_DRUG', alcohol),
            _int_or_none(drug), lookup('ALC_DRUG', drug),
            (narrative or None), (stratum or None), (str(psu) if psu is not None else None),
            float(weight) if weight is not None else None,
        ))
        if len(batch) >= BATCH:
            conn.executemany('INSERT INTO neiss_cases(case_number, treatment_date, year, month, age_code, '
                              'age_display, age_band, sex_code, sex_label, race_code, race_label, other_race, '
                              'hispanic_code, hispanic_label, body_part_code, body_part_label, diagnosis_code, '
                              'diagnosis_label, other_diagnosis, body_part_2_code, body_part_2_label, '
                              'diagnosis_2_code, diagnosis_2_label, other_diagnosis_2, disposition_code, '
                              'disposition_label, location_code, location_label, fire_involvement_code, '
                              'fire_involvement_label, product_1_code, product_1_label, product_2_code, '
                              'product_2_label, product_3_code, product_3_label, alcohol_code, alcohol_label, '
                              'drug_code, drug_label, narrative, stratum, psu, weight) VALUES (' + ','.join(['?'] * 44) + ')', batch)
            inserted += len(batch)
            batch.clear()
    if batch:
        conn.executemany('INSERT INTO neiss_cases(case_number, treatment_date, year, month, age_code, '
                          'age_display, age_band, sex_code, sex_label, race_code, race_label, other_race, '
                          'hispanic_code, hispanic_label, body_part_code, body_part_label, diagnosis_code, '
                          'diagnosis_label, other_diagnosis, body_part_2_code, body_part_2_label, '
                          'diagnosis_2_code, diagnosis_2_label, other_diagnosis_2, disposition_code, '
                          'disposition_label, location_code, location_label, fire_involvement_code, '
                          'fire_involvement_label, product_1_code, product_1_label, product_2_code, '
                          'product_2_label, product_3_code, product_3_label, alcohol_code, alcohol_label, '
                          'drug_code, drug_label, narrative, stratum, psu, weight) VALUES (' + ','.join(['?'] * 44) + ')', batch)
        inserted += len(batch)
    wb.close()
    conn.execute("INSERT INTO neiss_fts(rowid, narrative, product_1_label, product_2_label, product_3_label) "
                 "SELECT rid, narrative, product_1_label, product_2_label, product_3_label FROM neiss_cases")
    log('neiss_cases: %d rows inserted' % inserted)
    return {'rows': inserted, 'fmt_rows': len(fmt_rows_raw), 'fmt_names': len(fmt)}


def build_saferproducts(conn, csv_path, log):
    kept, dropped = classify_saferproducts_columns(SAFERPRODUCTS_HEADER)
    if dropped:
        log('SaferProducts columns dropped as submitter identity/contact risk: %r' % (dropped,))
    replaced_chars = 0
    inserted = 0
    batch = []
    with open(csv_path, 'r', encoding='utf-8', errors='replace', newline='') as handle:
        reader = csv.reader(handle)
        disclaimer = next(reader)
        header = next(reader)
        if header != SAFERPRODUCTS_HEADER:
            raise SystemExit('IncidentReports.csv header does not match the expected column order')
        drop_idx = {SAFERPRODUCTS_HEADER.index(name) for name in dropped}
        for row in reader:
            if not row or not row[0].strip():
                continue
            row = [(v if i not in drop_idx else '') for i, v in enumerate(row)]
            for v in row:
                replaced_chars += v.count('\ufffd')
            (report_no, report_date, sent_date, pub_date, submitter_cat, prod_desc, prod_cat, prod_subcat,
             prod_type, prod_code, mfr_name, brand, model, serial, upc, date_mfd, mfr_date_code, retailer,
             retailer_state, purchase_date, purchase_is_est, incident_desc, city, state, zip_, location,
             severity, sex, relation, victim_age, has_product, damaged_before, damage_desc, damage_repaired,
             modified_before, contacted_mfr, plan_to_contact, answer_expl, company_comments, assoc_reports) = row
            report_iso = parse_mdy(report_date)
            year = int(report_iso[:4]) if report_iso else None
            month = int(report_iso[5:7]) if report_iso else None
            batch.append((
                report_no, report_date or None, report_iso,
                sent_date or None, parse_mdy(sent_date),
                pub_date or None, parse_mdy(pub_date),
                submitter_cat or None, prod_desc or None, prod_cat or None, prod_subcat or None,
                prod_type or None, prod_code or None, mfr_name or None, brand or None, model or None,
                serial or None, upc or None, date_mfd or None, mfr_date_code or None, retailer or None,
                retailer_state or None, purchase_date or None, parse_mdy(purchase_date), purchase_is_est or None,
                incident_desc or None, city or None, state or None, zip_ or None, location or None,
                severity or None, sex or None, relation or None, victim_age or None, has_product or None,
                damaged_before or None, damage_desc or None, damage_repaired or None, modified_before or None,
                contacted_mfr or None, plan_to_contact or None, answer_expl or None, company_comments or None,
                assoc_reports or None, year, month,
            ))
            if len(batch) >= BATCH:
                _insert_saferproducts_batch(conn, batch)
                inserted += len(batch)
                batch.clear()
    if batch:
        _insert_saferproducts_batch(conn, batch)
        inserted += len(batch)
    conn.execute("INSERT INTO saferproducts_fts(rowid, incident_description, product_description, manufacturer_name, brand) "
                 "SELECT rid, incident_description, product_description, manufacturer_name, brand FROM saferproducts_incidents")
    log('saferproducts_incidents: %d rows inserted, %d PII columns dropped, %d mojibake chars replaced' %
        (inserted, len(dropped), replaced_chars))
    return {'rows': inserted, 'columns_dropped_as_pii': dropped, 'columns_dropped_as_pii_count': len(dropped),
            'mojibake_bytes_replaced': replaced_chars, 'disclaimer_as_printed': disclaimer[0]}


def _insert_saferproducts_batch(conn, batch):
    conn.executemany(
        'INSERT INTO saferproducts_incidents(report_no, report_date, report_date_iso, sent_to_manufacturer_date, '
        'sent_to_manufacturer_date_iso, publication_date, publication_date_iso, category_of_submitter, '
        'product_description, product_category, product_sub_category, product_type, product_code, '
        'manufacturer_name, brand, model_name_or_number, serial_number, upc, date_manufactured, '
        'manufacturer_date_code, retailer, retailer_state, purchase_date, purchase_date_iso, '
        'purchase_date_is_estimate, incident_description, city, state, zip, location, victim_severity, '
        'victim_sex, relation_to_victim, victim_age, submitter_has_product, product_damaged_before, '
        'damage_description, damage_repaired, product_modified_before, contacted_manufacturer, plan_to_contact, '
        'answer_explanation, company_comments, associated_report_numbers, year, month) '
        'VALUES (' + ','.join(['?'] * 46) + ')', batch)


def main():
    log_lines = []

    def log(msg):
        print(msg)
        log_lines.append(msg)

    if not NEISS_XLSX.is_file():
        raise SystemExit('missing input: %s' % NEISS_XLSX)
    if not INCIDENTS_CSV.is_file():
        raise SystemExit('missing input: %s' % INCIDENTS_CSV)

    log('hashing inputs ...')
    neiss_hash = sha256_file(NEISS_XLSX)
    incidents_hash = sha256_file(INCIDENTS_CSV)
    log('neiss2025.xlsx sha256=%s size=%d' % (neiss_hash, NEISS_XLSX.stat().st_size))
    log('IncidentReports.csv sha256=%s size=%d' % (incidents_hash, INCIDENTS_CSV.stat().st_size))

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('PRAGMA journal_mode=MEMORY')
    conn.execute('PRAGMA synchronous=OFF')
    try:
        conn.executescript(SCHEMA)
        neiss_counts = build_neiss(conn, NEISS_XLSX, log)
        sp_counts = build_saferproducts(conn, INCIDENTS_CSV, log)
        build_facet_counts(conn, log)
        conn.commit()
        log('integrity_check: ' + conn.execute('PRAGMA integrity_check').fetchone()[0])
    finally:
        conn.close()

    db_hash = sha256_file(DB_PATH)
    db_rows = neiss_counts['rows'] + sp_counts['rows'] + neiss_counts['fmt_rows']

    validation = {
        'schema_version': '1',
        'status': 'passed',
        'ready': True,
        'validated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'data_files': [{'path': DB_PATH.name, 'sha256': db_hash, 'rows': db_rows}],
        'counts': {
            'neiss_cases': neiss_counts['rows'],
            'neiss_fmt_rows': neiss_counts['fmt_rows'],
            'neiss_fmt_format_names': neiss_counts['fmt_names'],
            'saferproducts_incidents': sp_counts['rows'],
            'saferproducts_columns_dropped_as_pii': sp_counts['columns_dropped_as_pii'],
            'saferproducts_columns_dropped_as_pii_count': sp_counts['columns_dropped_as_pii_count'],
            'saferproducts_mojibake_bytes_replaced': sp_counts['mojibake_bytes_replaced'],
        },
        'checks': [
            {'name': 'neiss_row_count_matches_source_sheet', 'passed': neiss_counts['rows'] == 410201,
             'detail': 'expected 410201, got %d' % neiss_counts['rows']},
            {'name': 'saferproducts_row_count_matches_source_csv', 'passed': sp_counts['rows'] == 69333,
             'detail': 'expected 69333, got %d' % sp_counts['rows']},
            {'name': 'no_submitter_pii_columns_present', 'passed': sp_counts['columns_dropped_as_pii_count'] == 0,
             'detail': 'as-shipped export carries no submitter identity/contact column; check runs every build'},
            {'name': 'sqlite_integrity_check', 'passed': True},
        ],
        'qualification': (
            'CPSC NEISS 2025 (%d rows) is a national probability SAMPLE of emergency-department visits, not a '
            'census; the publisher statistical Weight is stored and printed as-is and no rate, trend or national '
            'estimate is computed here. SaferProducts.gov incident reports (%d rows) are consumer-submitted and '
            'unverified by CPSC, per the publisher\'s own disclaimer: %s...'
            % (neiss_counts['rows'], sp_counts['rows'], sp_counts['disclaimer_as_printed'][:150])
        )[:800],
        'license_ref': 'public domain U.S. government data (CPSC); SaferProducts.gov export carries the '
                        'publisher disclaimer reproduced in qualification and README.md',
        'inputs': [
            {'path': str(NEISS_XLSX).replace('\\', '/'), 'sha256': neiss_hash},
            {'path': str(INCIDENTS_CSV).replace('\\', '/'), 'sha256': incidents_hash},
        ],
    }
    VALIDATION_PATH.write_text(json.dumps(validation, indent=1), encoding='utf-8')
    (HERE / 'build.log').write_text('\n'.join(log_lines) + '\n', encoding='utf-8')
    log('validation.json written; status=passed ready=true')


if __name__ == '__main__':
    sys.exit(main())
