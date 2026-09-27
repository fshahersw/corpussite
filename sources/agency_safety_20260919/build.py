"""Offline build of the agency safety / enforcement supplement.

Reads only files already on disk (raw/ + receipts.jsonl written by fetch.py, the local CPSC CSV)
and writes agency_safety.sqlite3, firm_names.jsonl, edges.jsonl, unresolved.jsonl, files.json and
validation.json into the same folder. Re-runnable; never touches the network.

    python build.py                       # full build
    python build.py --without-openfda-bulk  # build only the FDA XLSX + local CPSC datasets
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import time
import unicodedata
import zipfile
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_CPSC = Path('C:/Users/firas/Downloads/returnedfiles/Recalls.csv')
ECFR_T21_STRUCTURE_GLOB = 'reports/corpus_upgrade_20260919/understand/packets/regulation_probe'
DB_NAME = 'agency_safety.sqlite3'

OPENFDA_DISCLAIMER_FALLBACK = ('Do not rely on openFDA to make decisions regarding medical care. While we make every effort to ensure that '
                               'data is accurate, you should assume all results are unvalidated.')
OPENFDA_TERMS = ('openFDA data: public domain (CC0) per https://open.fda.gov/license/ with the publisher disclaimer: "{disclaimer}" '
                 'Records are the publisher\'s bulk export as of the stated export date, not a live query.')


# ----------------------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def norm_firm(name):
    """Light, reversible-in-spirit normalisation. No suffix stripping, no alias tables, no merging."""
    if not isinstance(name, str):
        return ''
    text = unicodedata.normalize('NFKC', name).upper().replace('&', ' AND ')
    text = re.sub(r"['\u2019`]", '', text)
    text = re.sub(r'[^\w\s]|_', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def firm_id(firm_norm):
    return 'firm:' + firm_norm.lower().replace(' ', '-')


def iso_date(value):
    """YYYYMMDD, YYYY-MM-DD or M/D/YYYY -> ISO date. Anything else stays unknown (None)."""
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', text) or re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
        if not match:
            return None
        month, day, year = map(int, match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False) if value else None
    return str(value)


def join_text(*parts):
    out = []
    for part in parts:
        if isinstance(part, (list, tuple)):
            out.extend(str(p) for p in part if p)
        elif part:
            out.append(str(part))
    return ' \n'.join(out)


RESULTS_START = re.compile(r'"results"\s*:\s*\[')
SKIP = re.compile(r'[\s,]*')


def iter_openfda(zip_path):
    """Stream an openFDA bulk zip: yields ('meta', dict) once, then ('row', dict) per result.
    The largest file is > 400 MB of JSON, so it is never loaded whole."""
    with zipfile.ZipFile(zip_path) as archive:
        name = [n for n in archive.namelist() if n.endswith('.json')][0]
        with archive.open(name) as stream:
            reader = codecs.getreader('utf-8')(stream)
            decoder, buffer, position = json.JSONDecoder(), reader.read(1 << 20), 0
            while True:
                found = RESULTS_START.search(buffer)
                if found:
                    break
                more = reader.read(1 << 20)
                if not more:
                    raise ValueError('no top-level results array in ' + str(zip_path))
                buffer += more
            head = buffer[:found.start()].rstrip().rstrip(',') + '}'
            yield 'meta', json.loads(head).get('meta', {})
            position = found.end()
            while True:
                position = SKIP.match(buffer, position).end()
                if position >= len(buffer):
                    more = reader.read(8 << 20)
                    if not more:
                        raise ValueError('unterminated results array in ' + str(zip_path))
                    buffer, position = more, 0
                    continue
                if buffer[position] == ']':
                    return
                try:
                    row, position = decoder.raw_decode(buffer, position)
                except json.JSONDecodeError:
                    more = reader.read(8 << 20)
                    if not more:
                        raise
                    buffer, position = buffer[position:] + more, 0
                    continue
                yield 'row', row


# ----------------------------------------------------------------------------- dataset handlers
ENFORCEMENT_DATES = ('recall_initiation_date', 'center_classification_date', 'report_date', 'termination_date')


def h_enforcement(row):
    dates = {key: iso_date(row.get(key)) for key in ENFORCEMENT_DATES}
    table = {key: clean(row.get(key)) for key in (
        'recall_number', 'event_id', 'classification', 'status', 'product_type', 'product_description', 'reason_for_recall',
        'recalling_firm', 'city', 'state', 'country', 'postal_code', 'voluntary_mandated', 'initial_firm_notification',
        'distribution_pattern', 'product_quantity')}
    for key in ENFORCEMENT_DATES:
        table[key] = dates[key]
        table[key + '_raw'] = clean(row.get(key))
    return {'native_id': clean(row.get('recall_number')), 'title': clean(row.get('product_description')), 'firm': clean(row.get('recalling_firm')),
            'classification': clean(row.get('classification')), 'status': clean(row.get('status')), 'state': clean(row.get('state')),
            'country': clean(row.get('country')), 'dates': dates, 'published_at': dates['report_date'], 'sort_date': dates['report_date'],
            'snippet': clean(row.get('reason_for_recall')),
            'body': join_text(row.get('product_description'), row.get('reason_for_recall'), row.get('recall_number'), row.get('city')),
            'table': table}


def h_drugsfda(row):
    submissions, products = row.get('submissions') or [], row.get('products') or []
    number = clean(row.get('application_number')) or ''
    originals = sorted(d for d in (iso_date(s.get('submission_status_date')) for s in submissions
                                   if s.get('submission_type') == 'ORIG' and s.get('submission_status') == 'AP') if d)
    every = sorted(d for d in (iso_date(s.get('submission_status_date')) for s in submissions) if d)
    brands = sorted({p.get('brand_name') for p in products if p.get('brand_name')})
    ingredients = sorted({i.get('name') for p in products for i in (p.get('active_ingredients') or []) if i.get('name')})
    kind = re.match(r'[A-Z]+', number)
    table = {'application_number': number or None, 'application_type': kind.group(0) if kind else None, 'sponsor_name': clean(row.get('sponsor_name')),
             'product_count': len(products), 'submission_count': len(submissions), 'brand_names': clean(brands), 'active_ingredients': clean(ingredients),
             'original_approval_date': originals[0] if originals else None,
             'original_approval_basis': 'earliest submission_status_date among submissions with submission_type ORIG and submission_status AP' if originals else None,
             'latest_submission_status_date': every[-1] if every else None}
    children = {
        'drugsfda_products': [{'application_number': number, 'product_number': clean(p.get('product_number')), 'brand_name': clean(p.get('brand_name')),
                               'active_ingredients': clean(p.get('active_ingredients')), 'dosage_form': clean(p.get('dosage_form')),
                               'route': clean(p.get('route')), 'marketing_status': clean(p.get('marketing_status')),
                               'reference_drug': clean(p.get('reference_drug')), 'reference_standard': clean(p.get('reference_standard')),
                               'te_code': clean(p.get('te_code'))} for p in products],
        'drugsfda_submissions': [{'application_number': number, 'submission_type': clean(s.get('submission_type')),
                                  'submission_number': clean(s.get('submission_number')), 'submission_status': clean(s.get('submission_status')),
                                  'submission_status_date': iso_date(s.get('submission_status_date')),
                                  'submission_status_date_raw': clean(s.get('submission_status_date')),
                                  'submission_class_code': clean(s.get('submission_class_code')),
                                  'submission_class_code_description': clean(s.get('submission_class_code_description')),
                                  'review_priority': clean(s.get('review_priority')), 'doc_count': len(s.get('application_docs') or [])}
                                 for s in submissions]}
    title = number + (' — ' + ', '.join(brands[:4]) if brands else '')
    return {'native_id': number or None, 'title': title, 'firm': clean(row.get('sponsor_name')),
            'dates': {'original_approval_date': table['original_approval_date'], 'latest_submission_status_date': table['latest_submission_status_date']},
            'published_at': None, 'sort_date': table['original_approval_date'] or table['latest_submission_status_date'],
            'snippet': ', '.join(ingredients[:6]) or None, 'body': join_text(number, brands, ingredients), 'table': table, 'children': children}


def h_pma(row):
    openfda = row.get('openfda') if isinstance(row.get('openfda'), dict) else {}
    number, supplement = clean(row.get('pma_number')), clean(row.get('supplement_number'))
    dates = {key: iso_date(row.get(key)) for key in ('date_received', 'decision_date', 'fed_reg_notice_date')}
    table = {key: clean(row.get(key)) for key in (
        'pma_number', 'supplement_number', 'applicant', 'generic_name', 'trade_name', 'product_code', 'advisory_committee',
        'advisory_committee_description', 'supplement_type', 'supplement_reason', 'expedited_review_flag', 'decision_code', 'docket_number',
        'city', 'state', 'zip')}
    table.update(dates)
    table.update({'openfda_device_class': clean(openfda.get('device_class')), 'openfda_regulation_number': clean(openfda.get('regulation_number'))})
    return {'native_id': (number or '') + (':' + supplement if supplement else '') or None,
            'title': clean(row.get('trade_name')) or clean(row.get('generic_name')), 'firm': clean(row.get('applicant')),
            'classification': clean(openfda.get('device_class')), 'status': clean(row.get('decision_code')),
            'product_code': clean(row.get('product_code')), 'state': clean(row.get('state')), 'dates': dates, 'published_at': None,
            'sort_date': dates['decision_date'], 'snippet': clean(row.get('supplement_reason')) or clean(row.get('generic_name')),
            'body': join_text(number, row.get('generic_name'), row.get('trade_name'), row.get('ao_statement'), row.get('product_code')), 'table': table}


REGULATION = re.compile(r'(\d{3})\.(\d{1,4}[a-z]?)')


def h_classification(row):
    regulation = clean(row.get('regulation_number'))
    match = REGULATION.fullmatch(regulation or '')
    table = {key: clean(row.get(key)) for key in (
        'product_code', 'device_name', 'device_class', 'regulation_number', 'medical_specialty', 'medical_specialty_description', 'review_panel',
        'review_code', 'submission_type_id', 'definition', 'implant_flag', 'life_sustain_support_flag', 'gmp_exempt_flag', 'third_party_flag',
        'unclassified_reason', 'summary_malfunction_reporting')}
    table['cfr_id'] = 'cfr:21:' + regulation if match else None
    table['cfr_part'] = match.group(1) if match else None
    return {'native_id': clean(row.get('product_code')), 'title': clean(row.get('device_name')), 'classification': clean(row.get('device_class')),
            'product_code': clean(row.get('product_code')), 'dates': {}, 'published_at': None, 'sort_date': None,
            'snippet': join_text('21 CFR ' + regulation if regulation else None, row.get('medical_specialty_description')) or None,
            'body': join_text(row.get('device_name'), row.get('definition'), row.get('medical_specialty_description'), regulation, row.get('product_code')),
            'table': table}


def h_crl(row):
    numbers = row.get('application_number') if isinstance(row.get('application_number'), list) else [row.get('application_number')]
    numbers = [n for n in numbers if n]
    letter = iso_date(row.get('letter_date'))
    table = {'file_name': clean(row.get('file_name')), 'letter_date': letter, 'letter_date_raw': clean(row.get('letter_date')),
             'letter_type': clean(row.get('letter_type')), 'approval_status': clean(row.get('approval_status')), 'company_name': clean(row.get('company_name')),
             'application_numbers': clean(numbers), 'approver_center': clean(row.get('approver_center')), 'text_chars': len(row.get('text') or '')}
    return {'native_id': clean(row.get('file_name')), 'title': join_text(row.get('letter_type'), ', '.join(numbers)).replace(' \n', ' — '),
            'firm': clean(row.get('company_name')), 'status': clean(row.get('approval_status')), 'dates': {'letter_date': letter}, 'published_at': None,
            'sort_date': letter, 'snippet': (clean(row.get('text')) or '')[:240] or None,
            'body': join_text(row.get('letter_type'), numbers, row.get('text')), 'table': table}


def h_shortage(row):
    dates = {key: iso_date(row.get(key)) for key in ('initial_posting_date', 'update_date', 'discontinued_date', 'change_date')}
    table = {key: clean(row.get(key)) for key in (
        'package_ndc', 'generic_name', 'proprietary_name', 'company_name', 'status', 'availability', 'update_type', 'shortage_reason',
        'related_info', 'therapeutic_category', 'dosage_form', 'presentation', 'contact_info')}
    for key, value in dates.items():
        table[key] = value
        table[key + '_raw'] = clean(row.get(key))
    return {'native_id': clean(row.get('package_ndc')), 'title': clean(row.get('presentation')) or clean(row.get('generic_name')),
            'firm': clean(row.get('company_name')), 'status': clean(row.get('status')), 'dates': dates, 'published_at': dates['initial_posting_date'],
            'sort_date': dates['update_date'] or dates['initial_posting_date'], 'snippet': clean(row.get('related_info')) or clean(row.get('shortage_reason')),
            'body': join_text(row.get('generic_name'), row.get('proprietary_name'), row.get('presentation'), row.get('related_info'),
                              row.get('shortage_reason'), row.get('therapeutic_category')), 'table': table}


def h_orangebook(row):
    products = row.get('products') if isinstance(row.get('products'), list) else []
    product = products[0] if products else {}
    approval = iso_date(row.get('approval_date'))
    ingredients = [i.get('name') for i in (product.get('active_ingredients') or []) if i.get('name')]
    number = (clean(product.get('application_type')) or '') + (clean(product.get('application_number')) or '')
    table = {'application_type': clean(product.get('application_type')), 'application_number': clean(product.get('application_number')),
             'product_number': clean(row.get('product_number')), 'approval_date': approval, 'approval_date_raw': clean(row.get('approval_date')),
             'brand_name': clean(product.get('brand_name')), 'active_ingredients': clean(product.get('active_ingredients')),
             'applicant_short': clean(product.get('application_name')), 'applicant_full': clean(product.get('application_full_name')),
             'te_codes': clean(product.get('therapeutic_equivalence_codes')), 'marketing_status': clean(product.get('marketing_status')),
             'reference_listed_drug': clean(product.get('reference_listed_drug')), 'reference_standard': clean(product.get('reference_standard')),
             'dosage_form': clean(product.get('dosage_form')), 'route': clean(product.get('route')), 'products_in_record': len(products)}
    return {'native_id': (number + '-' + (clean(row.get('product_number')) or '')) if number else None, 'title': clean(product.get('brand_name')),
            'firm': clean(product.get('application_full_name')) or clean(product.get('application_name')), 'status': clean(product.get('marketing_status')),
            'dates': {'approval_date': approval}, 'published_at': None, 'sort_date': approval, 'snippet': ', '.join(ingredients) or None,
            'body': join_text(product.get('brand_name'), ingredients, number), 'table': table}


def h_warning_letter(row):
    posted, issued = iso_date(row.get('Posted Date')), iso_date(row.get('Letter Issue Date'))
    table = {'posted_date': posted, 'posted_date_raw': clean(row.get('Posted Date')), 'letter_issue_date': issued,
             'letter_issue_date_raw': clean(row.get('Letter Issue Date')), 'company_name': clean(row.get('Company Name')),
             'issuing_office': clean(row.get('Issuing Office')), 'subject': clean(row.get('Subject')),
             'response_letter': clean(row.get('Response Letter')), 'closeout_letter': clean(row.get('Closeout Letter'))}
    return {'native_id': None, 'title': clean(row.get('Subject')), 'firm': clean(row.get('Company Name')),
            'dates': {'posted_date': posted, 'letter_issue_date': issued}, 'published_at': posted, 'sort_date': posted,
            'snippet': clean(row.get('Issuing Office')), 'body': join_text(row.get('Subject'), row.get('Issuing Office')), 'table': table}


def h_press_recall(row):
    listed = iso_date(row.get('Date'))
    table = {'listed_date': listed, 'listed_date_raw': clean(row.get('Date')), 'brand_names': clean(row.get('Brand-Names')),
             'product_description': clean(row.get('Product-Description')), 'product_types': clean(row.get('Product-Types')),
             'recall_reason_description': clean(row.get('Recall-Reason-Description')), 'company_name': clean(row.get('Company-Name')),
             'terminated_recall': clean(row.get('Terminated Recall'))}
    return {'native_id': None, 'title': clean(row.get('Product-Description')) or clean(row.get('Brand-Names')), 'firm': clean(row.get('Company-Name')),
            'classification': None, 'status': clean(row.get('Terminated Recall')), 'dates': {'listed_date': listed}, 'published_at': None,
            'sort_date': listed, 'snippet': clean(row.get('Recall-Reason-Description')),
            'body': join_text(row.get('Brand-Names'), row.get('Product-Description'), row.get('Product-Types'), row.get('Recall-Reason-Description')),
            'table': table}


def h_cpsc(row):
    listed = iso_date(row.get('Date'))
    number = clean(row.get('Recall Number')) or clean(row.get('Repair Number'))
    table = {'section': row.get('_section'), 'title': clean(row.get('Title')), 'date': listed, 'date_raw': clean(row.get('Date')),
             'summary': clean(row.get('Summary')), 'recall_number': number, 'recall_url': clean(row.get('Recall URL'))}
    return {'native_id': number, 'title': clean(row.get('Title')), 'dates': {'date': listed}, 'published_at': listed, 'sort_date': listed,
            'status': None, 'snippet': (clean(row.get('Summary')) or '')[:240] or None, 'body': join_text(row.get('Title'), row.get('Summary')),
            'table': table}


TABLES = {
    'enforcement': ['recall_number', 'event_id', 'classification', 'status', 'product_type', 'product_description', 'reason_for_recall', 'recalling_firm',
                    'city', 'state', 'country', 'postal_code', 'voluntary_mandated', 'initial_firm_notification', 'distribution_pattern', 'product_quantity',
                    'recall_initiation_date', 'recall_initiation_date_raw', 'center_classification_date', 'center_classification_date_raw',
                    'report_date', 'report_date_raw', 'termination_date', 'termination_date_raw'],
    'drugsfda_applications': ['application_number', 'application_type', 'sponsor_name', 'product_count', 'submission_count', 'brand_names',
                              'active_ingredients', 'original_approval_date', 'original_approval_basis', 'latest_submission_status_date'],
    'pma': ['pma_number', 'supplement_number', 'applicant', 'generic_name', 'trade_name', 'product_code', 'advisory_committee',
            'advisory_committee_description', 'supplement_type', 'supplement_reason', 'expedited_review_flag', 'decision_code', 'docket_number',
            'city', 'state', 'zip', 'date_received', 'decision_date', 'fed_reg_notice_date', 'openfda_device_class', 'openfda_regulation_number'],
    'device_classification': ['product_code', 'device_name', 'device_class', 'regulation_number', 'cfr_id', 'cfr_part', 'medical_specialty',
                              'medical_specialty_description', 'review_panel', 'review_code', 'submission_type_id', 'definition', 'implant_flag',
                              'life_sustain_support_flag', 'gmp_exempt_flag', 'third_party_flag', 'unclassified_reason', 'summary_malfunction_reporting'],
    'complete_response_letters': ['file_name', 'letter_date', 'letter_date_raw', 'letter_type', 'approval_status', 'company_name', 'application_numbers',
                                  'approver_center', 'text_chars'],
    'drug_shortages': ['package_ndc', 'generic_name', 'proprietary_name', 'company_name', 'status', 'availability', 'update_type', 'shortage_reason',
                       'related_info', 'therapeutic_category', 'dosage_form', 'presentation', 'contact_info', 'initial_posting_date',
                       'initial_posting_date_raw', 'update_date', 'update_date_raw', 'discontinued_date', 'discontinued_date_raw', 'change_date',
                       'change_date_raw'],
    'orangebook_products': ['application_type', 'application_number', 'product_number', 'approval_date', 'approval_date_raw', 'brand_name',
                            'active_ingredients', 'applicant_short', 'applicant_full', 'te_codes', 'marketing_status', 'reference_listed_drug',
                            'reference_standard', 'dosage_form', 'route', 'products_in_record'],
    'warning_letters': ['posted_date', 'posted_date_raw', 'letter_issue_date', 'letter_issue_date_raw', 'company_name', 'issuing_office', 'subject',
                        'response_letter', 'closeout_letter'],
    'fda_press_recalls': ['listed_date', 'listed_date_raw', 'brand_names', 'product_description', 'product_types', 'recall_reason_description',
                          'company_name', 'terminated_recall'],
    'cpsc_recalls': ['section', 'title', 'date', 'date_raw', 'summary', 'recall_number', 'recall_url'],
}
CHILD_TABLES = {
    'drugsfda_products': ['application_number', 'product_number', 'brand_name', 'active_ingredients', 'dosage_form', 'route', 'marketing_status',
                          'reference_drug', 'reference_standard', 'te_code'],
    'drugsfda_submissions': ['application_number', 'submission_type', 'submission_number', 'submission_status', 'submission_status_date',
                             'submission_status_date_raw', 'submission_class_code', 'submission_class_code_description', 'review_priority', 'doc_count'],
}

ENFORCEMENT_NOTE = ('Four publisher dates are kept separate: recall_initiation_date (firm began the recall), center_classification_date (FDA center '
                    'classified it), report_date (appeared in the FDA Enforcement Report) and termination_date (FDA terminated the recall; empty while open). ')
DATASETS = [
    {'dataset': 'openfda_drug_enforcement', 'seed': 'openfda-drug-enforcement', 'endpoint': 'drug/enforcement', 'group': 'enforcement', 'table': 'enforcement',
     'handler': h_enforcement, 'label': 'FDA drug recall enforcement reports (openFDA bulk)', 'edge': 'recall',
     'published_basis': 'publisher field report_date (date the recall appeared in the FDA Enforcement Report)', 'note': ENFORCEMENT_NOTE},
    {'dataset': 'openfda_device_enforcement', 'seed': 'openfda-device-enforcement', 'endpoint': 'device/enforcement', 'group': 'enforcement',
     'table': 'enforcement', 'handler': h_enforcement, 'label': 'FDA device recall enforcement reports (openFDA bulk)', 'edge': 'recall',
     'published_basis': 'publisher field report_date (date the recall appeared in the FDA Enforcement Report)',
     'note': ENFORCEMENT_NOTE + 'code_info (lot and serial lists) is kept verbatim in the publisher record but is not full-text indexed. '},
    {'dataset': 'openfda_food_enforcement', 'seed': 'openfda-food-enforcement', 'endpoint': 'food/enforcement', 'group': 'enforcement', 'table': 'enforcement',
     'handler': h_enforcement, 'label': 'FDA food recall enforcement reports (openFDA bulk)', 'edge': 'recall',
     'published_basis': 'publisher field report_date (date the recall appeared in the FDA Enforcement Report)', 'note': ENFORCEMENT_NOTE},
    {'dataset': 'openfda_drugsfda', 'seed': 'openfda-drug-drugsfda', 'endpoint': 'drug/drugsfda', 'group': 'applications', 'table': 'drugsfda_applications',
     'handler': h_drugsfda, 'label': 'Drugs@FDA applications, products and submissions (openFDA bulk)', 'edge': 'application',
     'note': 'original_approval_date is derived (earliest ORIG submission with status AP); every submission row keeps its own status date. '},
    {'dataset': 'openfda_device_pma', 'seed': 'openfda-device-pma', 'endpoint': 'device/pma', 'group': 'devices', 'table': 'pma', 'handler': h_pma,
     'label': 'Device premarket approvals and supplements (openFDA bulk)',
     'note': 'One row per PMA original or supplement. status = publisher decision_code; classification = openfda.device_class when supplied. '},
    {'dataset': 'openfda_device_classification', 'seed': 'openfda-device-classification', 'endpoint': 'device/classification', 'group': 'devices',
     'table': 'device_classification', 'handler': h_classification, 'label': 'Device product classification (openFDA bulk)', 'edge': 'classification',
     'note': 'classification = publisher device_class (1, 2, 3, U, N, f). regulation_number links to 21 CFR only when the publisher supplies one. '},
    {'dataset': 'openfda_crl', 'seed': 'openfda-transparency-crl', 'endpoint': 'transparency/crl', 'group': 'applications',
     'table': 'complete_response_letters', 'handler': h_crl, 'label': 'Complete response letters (openFDA transparency bulk)',
     'note': 'Letter text is publisher OCR of redacted PDFs and contains recognition errors. status = publisher approval_status. '},
    {'dataset': 'openfda_drug_shortages', 'seed': 'openfda-drug-shortages', 'endpoint': 'drug/shortages', 'group': 'supply', 'table': 'drug_shortages',
     'handler': h_shortage, 'label': 'Drug shortages (openFDA bulk)', 'published_basis': 'publisher field initial_posting_date',
     'note': 'One row per package NDC and update; several rows can share an NDC (suffix ~n marks repeats). '},
    {'dataset': 'openfda_orangebook', 'seed': 'openfda-drug-orangebook', 'endpoint': 'drug/orangebook', 'group': 'applications', 'table': 'orangebook_products',
     'handler': h_orangebook, 'label': 'Orange Book products (openFDA bulk)',
     'note': 'approval_date is kept verbatim when it is not a calendar date (for example "Approved Prior to Jan 1, 1982"). '},
    {'dataset': 'fda_warning_letters', 'seed': 'fda-warning-letters-xlsx', 'group': 'warning_letters', 'table': 'warning_letters',
     'handler': h_warning_letter, 'label': 'FDA warning letters list (FDA.gov XLSX export)', 'xlsx': True,
     'published_basis': 'Posted Date column of the FDA.gov export (date FDA posted the letter; the letter issue date is a separate field)',
     'note': 'List rows only (no letter text, no letter URL, no FDA letter id in the export). '},
    {'dataset': 'fda_press_recalls', 'seed': 'fda-press-recalls-xlsx', 'group': 'press_recalls', 'table': 'fda_press_recalls', 'handler': h_press_recall,
     'label': 'FDA recalls, market withdrawals and safety alerts list (FDA.gov XLSX export)', 'xlsx': True,
     'note': 'Press-release list, not the classified recall record (that is the openFDA enforcement data). The export does not define its Date '
             'column, so it is kept as listed_date and published_at stays unknown. status = the export\'s "Terminated Recall" column. '},
    {'dataset': 'cpsc_recalls_local', 'seed': 'local-cpsc-recalls-csv', 'group': 'cpsc', 'table': 'cpsc_recalls', 'handler': h_cpsc,
     'label': 'CPSC recalls — local file of unknown provenance, fields as found', 'local': True,
     'published_basis': 'Date column as found in the local file; the file does not define it',
     'note': 'User-supplied CSV with no HTTP receipt and no capture date; cpsc.gov and saferproducts.gov refused robots.txt (403) and were not contacted, '
             'so nothing in this dataset was checked against the publisher. The file opens with CPSC\'s SaferProducts.gov disclaimer. '},
]
OPENFDA_SEEDS = [d['seed'] for d in DATASETS if d.get('endpoint')]


# ----------------------------------------------------------------------------- readers
def read_xlsx(path):
    import openpyxl
    book = openpyxl.load_workbook(path, read_only=True)
    try:
        sheet = book.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = [str(cell).strip() if cell is not None else '' for cell in next(rows)]
        for values in rows:
            if values is None or all(cell is None or str(cell).strip() == '' for cell in values):
                continue
            yield {header[i]: (cell if isinstance(cell, (datetime, date)) else (None if cell is None else str(cell))) for i, cell in enumerate(values) if i < len(header)}
    finally:
        book.close()


def read_cpsc(path):
    """The file as found: a disclaimer line, a 4-column 'Repair Number' block, then a 5-column 'Recall Number' block (cp1252)."""
    payload = Path(path).read_bytes()
    try:
        text, encoding = payload.decode('utf-8-sig'), 'utf-8'
    except UnicodeDecodeError:
        text, encoding = payload.decode('cp1252'), 'cp1252'
    header, section, preamble = None, None, []
    for values in csv.reader(io.StringIO(text, newline='')):
        if not values or not any(v.strip() for v in values):
            continue
        if values[0].strip() == 'Title' and len(values) >= 4:
            header = [v.strip() for v in values]
            section = 'repair' if 'Repair Number' in header else 'recall'
            continue
        if header is None:
            preamble.append(' '.join(values)); continue
        row = {header[i]: value for i, value in enumerate(values) if i < len(header)}
        row['_section'] = section
        yield row
    read_cpsc.last = {'encoding': encoding, 'preamble': preamble}


# ----------------------------------------------------------------------------- build
def create_schema(db):
    db.executescript('''
        create table datasets(dataset text primary key, position integer, label text, publisher text, grp text, table_name text, id_prefix text,
            rows integer, file_id text, captured_at text, captured_at_basis text, source_as_of text, source_as_of_basis text,
            published_at_basis text, publisher_last_updated text, date_types text, qualification text, endpoint text);
        create table record_index(rid integer primary key, id text unique not null, dataset text not null, native_id text not null, title text,
            firm text, firm_norm text, classification text, status text, product_code text, state text, country text, published_at text,
            sort_date text, snippet text);
        create table record_dates(rid integer not null, date_type text not null, date text not null);
        create table raw_records(rid integer primary key, raw_z blob not null);
        create table firm_names(firm_norm text primary key, firm_id text, names text, datasets text, record_count integer);
        create virtual table record_fts using fts5(title, firm, body, content='', tokenize='unicode61 remove_diacritics 2');
    ''')
    for table, columns in TABLES.items():
        db.execute('create table %s(rid integer primary key, dataset text, %s)' % (table, ', '.join(c + ' text' for c in columns)))
    for table, columns in CHILD_TABLES.items():
        db.execute('create table %s(parent_rid integer, %s)' % (table, ', '.join(c + ' text' for c in columns)))


def ecfr_sections():
    """Optional: section identifiers from the eCFR Title 21 structure snapshot the scout saved (read-only evidence)."""
    probe = ROOT / ECFR_T21_STRUCTURE_GLOB
    try:
        for line in (probe / 'receipts.jsonl').read_text(encoding='utf-8').splitlines():
            receipt = json.loads(line)
            url = receipt.get('url', '')
            if '/structure/' in url and url.endswith('title-21.json') and receipt.get('status') == 200:
                body = probe / 'bodies' / receipt['body_file']
                found = set()

                def walk(node):
                    if node.get('type') == 'section':
                        found.add(node.get('identifier'))
                    for child in node.get('children') or []:
                        walk(child)
                walk(json.loads(body.read_bytes().decode('utf-8')))
                point = re.search(r'/structure/(\d{4}-\d{2}-\d{2})/', url)
                return found, {'path': body.relative_to(ROOT).as_posix(), 'sha256': sha256_file(body), 'as_of': point.group(1) if point else None}
    except (OSError, ValueError, KeyError):
        pass
    return None, None


def build(folder=HERE, cpsc_csv=DEFAULT_CPSC, include_openfda=True, log=None):
    folder = Path(folder)
    started = time.monotonic()
    receipts = {}
    for line in (folder / 'receipts.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            receipt = json.loads(line)
            if receipt.get('outcome') == 'saved':
                receipts[receipt['seed_id']] = receipt
    checks, counts, inputs, files = [], {}, [], []

    def check(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
        return passed

    def rel(path):
        path = Path(path).resolve()
        return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()

    # --- originals: verify every saved receipt against the bytes on disk
    def register(seed, datasets, kind):
        receipt = receipts.get(seed)
        if not receipt:
            return None
        path = folder / receipt['raw_path']
        digest = sha256_file(path) if path.is_file() else None
        if not check('original_matches_receipt:' + seed, digest == receipt['sha256'], 'sha256 %s, %s bytes' % (receipt['sha256'], receipt['bytes'])):
            return None
        files.append({'file_id': seed, 'path': receipt['raw_path'], 'sha256': digest, 'bytes': path.stat().st_size, 'filename': path.name,
                      'mime': {'.zip': 'application/zip', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                               '.json': 'application/json'}.get(path.suffix, 'application/octet-stream'),
                      'publisher_url': receipt['url'], 'final_url': receipt.get('final_url'), 'http_status': receipt.get('status'),
                      'captured_at': receipt.get('completed_at'), 'kind': kind, 'datasets': datasets,
                      'representation': 'downloaded underlying file (original bytes)'})
        inputs.append({'path': rel(path), 'sha256': digest})
        return path

    index_path = register('openfda-download-index', [d['dataset'] for d in DATASETS if d.get('endpoint')], 'openfda bulk index (download.json)')
    index = json.loads(index_path.read_bytes().decode('utf-8')) if index_path else {'meta': {}, 'results': {}}
    disclaimer = index.get('meta', {}).get('disclaimer') or OPENFDA_DISCLAIMER_FALLBACK
    forbidden = [r['url'] for r in receipts.values() if re.search(r'/(drug|device)/event/', r['url'])]
    check('no_faers_or_maude_event_files', not forbidden, 'receipts contain %d drug/event or device/event URLs' % len(forbidden))

    local_copy = None
    if cpsc_csv and Path(cpsc_csv).is_file():
        (folder / 'raw' / 'local').mkdir(parents=True, exist_ok=True)
        local_copy = folder / 'raw' / 'local' / 'Recalls.csv'
        if not local_copy.is_file() or sha256_file(local_copy) != sha256_file(cpsc_csv):
            shutil.copyfile(cpsc_csv, local_copy)
        digest = sha256_file(local_copy)
        files.append({'file_id': 'local-cpsc-recalls-csv', 'path': 'raw/local/Recalls.csv', 'sha256': digest, 'bytes': local_copy.stat().st_size,
                      'filename': 'Recalls.csv', 'mime': 'text/csv', 'publisher_url': None, 'final_url': None, 'http_status': None, 'captured_at': None,
                      'kind': 'local file of unknown provenance', 'datasets': ['cpsc_recalls_local'],
                      'representation': 'byte-identical copy of a user-supplied local file; no HTTP receipt exists'})
        inputs.append({'path': rel(cpsc_csv), 'sha256': digest})

    sections, structure_input = ecfr_sections()
    if structure_input:
        inputs.append({'path': structure_input['path'], 'sha256': structure_input['sha256']})

    temp = folder / (DB_NAME + '.building')
    if temp.exists():
        temp.unlink()
    db = sqlite3.connect(temp)
    db.executescript('pragma journal_mode=off; pragma synchronous=off; pragma page_size=8192;')
    create_schema(db)
    firms = defaultdict(lambda: {'names': Counter(), 'datasets': Counter()})
    duplicate_ids, unparsed_dates, skipped = Counter(), Counter(), []
    edges_path, unresolved_path = folder / 'edges.jsonl', folder / 'unresolved.jsonl'
    edge_counts, unresolved_counts = Counter(), Counter()
    rid = 0
    with edges_path.open('w', encoding='utf-8', newline='\n') as edges, unresolved_path.open('w', encoding='utf-8', newline='\n') as unresolved:
        def emit(kind, record_id, record_type, target, relation, basis, evidence, reason=None):
            source = {'type': record_type, 'id': record_id}
            if target:
                edges.write(json.dumps({'from': source, 'to': target, 'relation': relation, 'basis': basis, 'evidence': evidence}, ensure_ascii=False) + '\n')
                edge_counts[relation] += 1
            else:
                unresolved.write(json.dumps({'from': source, 'relation': relation, 'reason': reason, 'evidence': evidence}, ensure_ascii=False) + '\n')
                unresolved_counts[relation + ': ' + reason] += 1

        for position, spec in enumerate(DATASETS):
            dataset, handler, table = spec['dataset'], spec['handler'], spec['table']
            meta, captured_at, source_as_of, as_of_basis, captured_basis, file_id = {}, None, None, None, None, None
            if spec.get('endpoint'):
                if not include_openfda:
                    skipped.append({'dataset': dataset, 'reason': 'openFDA bulk excluded by build option'}); continue
                path = register(spec['seed'], [dataset], 'openfda bulk zip ' + spec['endpoint'])
                if not path:
                    skipped.append({'dataset': dataset, 'reason': 'no saved, hash-matching original'}); continue
                rows = iter_openfda(path)
                a, b = spec['endpoint'].split('/')
                listing = index.get('results', {}).get(a, {}).get(b, {})
                source_as_of = listing.get('export_date')
                as_of_basis = 'openFDA download.json export_date for /%s (index captured %s)' % (spec['endpoint'], receipts['openfda-download-index']['completed_at'])
                prefix, publisher = 'openfda:' + spec['endpoint'] + ':', 'U.S. Food and Drug Administration (openFDA)'
            elif spec.get('xlsx'):
                path = register(spec['seed'], [dataset], 'FDA.gov XLSX export')
                if not path:
                    skipped.append({'dataset': dataset, 'reason': 'no saved, hash-matching original'}); continue
                rows = (('row', row) for row in read_xlsx(path))
                source_as_of = (receipts[spec['seed']].get('completed_at') or '')[:10] or None
                as_of_basis = 'live publisher export generated at request time; equals the capture date'
                prefix, publisher = ('fda-wl:' if dataset == 'fda_warning_letters' else 'fda-press:'), 'U.S. Food and Drug Administration (FDA.gov)'
            else:
                if not local_copy:
                    skipped.append({'dataset': dataset, 'reason': 'local CPSC file not found'}); continue
                rows = (('row', row) for row in read_cpsc(local_copy))
                prefix, publisher = 'cpsc:rcl:', 'U.S. Consumer Product Safety Commission (as stated inside the file; not verified)'
                captured_basis = 'unknown: user-supplied local file without an HTTP receipt (file system dates are not capture evidence)'
                as_of_basis = 'unknown: the file carries no export date'
            if not spec.get('local'):
                file_id = spec['seed']
                captured_at = receipts[spec['seed']].get('completed_at')
                captured_basis = 'HTTP response completed (receipts.jsonl, seed %s)' % spec['seed']
            else:
                file_id = 'local-cpsc-recalls-csv'

            seen, n, date_types = Counter(), 0, Counter()
            for kind, row in rows:
                if kind == 'meta':
                    meta = row; continue
                item = handler(row)
                native = item.get('native_id')
                public = {k: v for k, v in row.items() if not k.startswith('_')}
                raw_json = json.dumps(public, ensure_ascii=False, default=str)
                if not native:
                    native = 'x' + hashlib.sha1(raw_json.encode('utf-8')).hexdigest()[:14]
                seen[native] += 1
                if seen[native] > 1:
                    duplicate_ids[dataset] += 1
                    native = '%s~%d' % (native, seen[native])
                rid += 1; n += 1
                record_id = prefix + native
                firm = item.get('firm')
                firm_norm = norm_firm(firm) or None
                db.execute('insert into record_index values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    rid, record_id, dataset, native, item.get('title'), firm, firm_norm, item.get('classification'), item.get('status'),
                    (item.get('product_code') or '').upper() or None, item.get('state'), item.get('country'), item.get('published_at'),
                    item.get('sort_date'), (item.get('snippet') or '')[:240] or None))
                for date_type, value in item['dates'].items():
                    if value:
                        db.execute('insert into record_dates values(?,?,?)', (rid, date_type, value)); date_types[date_type] += 1
                    elif clean(row.get(date_type)) if isinstance(row, dict) else None:
                        unparsed_dates[dataset + '.' + date_type] += 1
                db.execute('insert into raw_records values(?,?)', (rid, zlib.compress(raw_json.encode('utf-8'), 6)))
                db.execute('insert into record_fts(rowid, title, firm, body) values(?,?,?,?)', (rid, item.get('title') or '', firm or '', item.get('body') or ''))
                columns = TABLES[table]
                db.execute('insert into %s values(?,?,%s)' % (table, ','.join('?' * len(columns))),
                           [rid, dataset] + [item['table'].get(c) for c in columns])
                for child, child_rows in (item.get('children') or {}).items():
                    columns = CHILD_TABLES[child]
                    db.executemany('insert into %s values(?,%s)' % (child, ','.join('?' * len(columns))),
                                   [[rid] + [r.get(c) for c in columns] for r in child_rows])
                if firm_norm:
                    firms[firm_norm]['names'][firm] += 1
                    firms[firm_norm]['datasets'][dataset] += 1
                # --- relationship edges: explicit publisher fields only
                if spec.get('edge') == 'recall':
                    emit('recall', record_id, 'fda_enforcement_recall', {'type': 'firm', 'id': firm_id(firm_norm)} if firm_norm else None, 'recalling_firm',
                         'publisher field recalling_firm; firm id is the exact normalised string, no entity resolution',
                         {'field': 'recalling_firm', 'value': firm, 'dataset': dataset}, 'recalling_firm is empty')
                elif spec.get('edge') == 'application':
                    emit('application', record_id, 'fda_drug_application', {'type': 'firm', 'id': firm_id(firm_norm)} if firm_norm else None, 'application_sponsor',
                         'publisher field sponsor_name; firm id is the exact normalised string, no entity resolution',
                         {'field': 'sponsor_name', 'value': firm, 'dataset': dataset}, 'sponsor_name is empty')
                elif spec.get('edge') == 'classification':
                    regulation, cfr = item['table'].get('regulation_number'), item['table'].get('cfr_id')
                    evidence = {'field': 'regulation_number', 'value': regulation, 'dataset': dataset}
                    if cfr and sections is not None:
                        evidence['section_in_ecfr_title21_structure'] = regulation in sections
                        evidence['ecfr_structure_as_of'] = structure_input['as_of']
                    emit('classification', record_id, 'fda_device_classification', {'type': 'cfr', 'id': cfr} if cfr else None, 'classified_under_regulation',
                         'publisher field regulation_number read as 21 CFR <part>.<section>', evidence,
                         'regulation_number is empty (unclassified, pre-amendment or PMA-only product code)' if not regulation
                         else 'regulation_number is not in <part>.<section> form')
            total = (meta.get('results') or {}).get('total') if meta else None
            if spec.get('endpoint'):
                listed = listing.get('total_records')
                check('row_count_matches_publisher:' + dataset, n == total == listed, 'parsed %s, file meta.results.total %s, download.json total_records %s' % (n, total, listed))
            db.execute('insert into datasets values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                dataset, position, spec['label'], publisher, spec['group'], table, prefix, n, file_id, captured_at, captured_basis, source_as_of,
                as_of_basis, spec.get('published_basis'), meta.get('last_updated') if meta else None, json.dumps(sorted(date_types)),
                spec['note'] + (OPENFDA_TERMS.format(disclaimer=disclaimer) if spec.get('endpoint') else ''), spec.get('endpoint')))
            counts[dataset] = n
            counts[dataset + '.date_rows'] = dict(date_types)
            if log:
                log(json.dumps({'dataset': dataset, 'rows': n, 'elapsed_s': round(time.monotonic() - started, 1)}))

    # --- dataset-specific caveats that depend on what was found
    for dataset, column in (('fda_warning_letters', 'posted_date'), ('fda_press_recalls', 'listed_date')):
        if dataset in counts:
            low, high = db.execute('select min(%s), max(%s) from %s' % (column, column, 'warning_letters' if dataset == 'fda_warning_letters' else 'fda_press_recalls')).fetchone()
            extra = 'The export returned %d rows with %s from %s to %s; %s' % (
                counts[dataset], column, low, high,
                'a round 1,000-row result indicates the publisher caps the export, so this is NOT the complete list. ' if counts[dataset] % 1000 == 0 and counts[dataset]
                else 'completeness against the on-screen list was not verified. ')
            db.execute('update datasets set qualification = qualification || ? where dataset = ?', (extra, dataset))
            counts[dataset + '.range'] = [low, high]
    if 'cpsc_recalls_local' in counts:
        low, high = db.execute('select min(date), max(date) from cpsc_recalls').fetchone()
        by_section = dict(db.execute('select section, count(*) from cpsc_recalls group by 1'))
        counts['cpsc_recalls_local.range'], counts['cpsc_recalls_local.sections'] = [low, high], by_section
        db.execute('update datasets set qualification = qualification || ? where dataset = ?', (
            'Rows as found: %s; Date values from %s to %s; text decoded as %s. ' % (by_section, low, high, getattr(read_cpsc, 'last', {}).get('encoding')),
            'cpsc_recalls_local'))

    for firm_norm in sorted(firms):
        entry = firms[firm_norm]
        db.execute('insert into firm_names values(?,?,?,?,?)', (
            firm_norm, firm_id(firm_norm), json.dumps([{'name': k, 'count': v} for k, v in entry['names'].most_common()], ensure_ascii=False),
            json.dumps(dict(entry['datasets'])), sum(entry['datasets'].values())))
    db.executescript('''
        create index ix_index_dataset on record_index(dataset, sort_date);
        create index ix_index_sort on record_index(sort_date);
        create index ix_index_firm on record_index(firm_norm);
        create index ix_index_code on record_index(product_code);
        create index ix_index_native on record_index(dataset, native_id);
        create index ix_dates on record_dates(date_type, date);
        create index ix_dates_rid on record_dates(rid);
        create index ix_class_reg on device_classification(regulation_number);
        create index ix_class_part on device_classification(cfr_part);
        create index ix_pma_code on pma(product_code);
        create index ix_products_parent on drugsfda_products(parent_rid);
        create index ix_submissions_parent on drugsfda_submissions(parent_rid);
        insert into record_fts(record_fts) values('optimize');
    ''')
    db.commit()
    check('sqlite_integrity', db.execute('pragma integrity_check').fetchone()[0] == 'ok', 'pragma integrity_check')
    index_rows = db.execute('select count(*) from record_index').fetchone()[0]
    check('every_record_has_verbatim_publisher_record', index_rows == db.execute('select count(*) from raw_records').fetchone()[0], '%d rows' % index_rows)
    if any(d.startswith('openfda_') and d.endswith('_enforcement') for d in counts):
        separate = dict(db.execute("select date_type, count(*) from record_dates where date_type in ('recall_initiation_date','center_classification_date','report_date','termination_date') group by 1"))
        check('enforcement_dates_kept_separate', len(separate) >= 3, json.dumps(separate))
    db.close()
    final = folder / DB_NAME
    temp.replace(final)

    with (folder / 'firm_names.jsonl').open('w', encoding='utf-8', newline='\n') as out:
        for firm_norm in sorted(firms):
            entry = firms[firm_norm]
            out.write(json.dumps({'firm_id': firm_id(firm_norm), 'firm_norm': firm_norm,
                                  'names': [{'name': k, 'count': v} for k, v in entry['names'].most_common()],
                                  'datasets': dict(entry['datasets']), 'record_count': sum(entry['datasets'].values()),
                                  'merge_rule': 'exact normalised string equality only'}, ensure_ascii=False) + '\n')
    (folder / 'files.json').write_text(json.dumps({'schema_version': '1', 'files': files}, indent=1, ensure_ascii=False), encoding='utf-8')
    for name in ('receipts.jsonl', 'seeds.jsonl'):
        if (folder / name).is_file():
            inputs.append({'path': rel(folder / name), 'sha256': sha256_file(folder / name)})

    def lines(path):
        with Path(path).open('rb') as stream:
            return sum(1 for _ in stream)
    data_files = [{'path': DB_NAME, 'sha256': sha256_file(final), 'rows': index_rows},
                  {'path': 'firm_names.jsonl', 'sha256': sha256_file(folder / 'firm_names.jsonl'), 'rows': len(firms)},
                  {'path': 'edges.jsonl', 'sha256': sha256_file(edges_path), 'rows': lines(edges_path)},
                  {'path': 'unresolved.jsonl', 'sha256': sha256_file(unresolved_path), 'rows': lines(unresolved_path)},
                  {'path': 'files.json', 'sha256': sha256_file(folder / 'files.json'), 'rows': len(files)}]
    counts.update({'records_total': index_rows, 'firm_strings': len(firms), 'edges': dict(edge_counts), 'unresolved': dict(unresolved_counts),
                   'duplicate_native_ids_suffixed': dict(duplicate_ids), 'date_values_not_calendar_dates_kept_raw': dict(unparsed_dates),
                   'original_files': len(files), 'original_bytes': sum(f['bytes'] for f in files), 'datasets_skipped': skipped})
    deviations = []
    gaps = folder / 'gaps.json'
    if gaps.is_file():
        try:
            deviations = [{'id': d['id'], 'severity': d.get('severity'), 'host': d.get('host'), 'summary': d.get('what_happened')}
                          for d in json.loads(gaps.read_text(encoding='utf-8')).get('deviations', [])]
        except ValueError:
            pass
    passed = all(c['passed'] for c in checks) and index_rows > 0
    gate = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': bool(passed), 'validated_at': now_iso(),
        'data_files': data_files, 'counts': counts, 'checks': checks,
        'qualification': (
            'Agency safety and enforcement data as published, not legal or medical conclusions. openFDA: "' + disclaimer + '" openFDA bulk files are '
            'public domain (CC0) exports as of their stated export dates; a recall, warning letter, shortage or complete response letter is an agency '
            'record about a product or firm and is not proof of defect, causation or liability. Firm names are exact normalised strings: different '
            'legal entities can share a string and one entity appears under many spellings; no entities were merged. The FDA.gov XLSX exports are '
            'list rows only and the warning-letter export appears capped at 1,000 rows. The CPSC dataset is a local file of unknown provenance, '
            'fields as found, never checked against cpsc.gov. Acquisition note: robots.txt on download.open.fda.gov answered HTTP 403 and the bulk '
            'files were nevertheless fetched before that was treated as a barrier; see gaps.json (deviations) — pending the user\'s decision.'),
        'license_ref': 'https://open.fda.gov/license/ (CC0 1.0) and https://open.fda.gov/terms/; FDA.gov content: U.S. government work; CPSC local file: terms unknown',
        'inputs': inputs, 'deviations': deviations}
    (folder / 'validation.json').write_text(json.dumps(gate, indent=1, ensure_ascii=False), encoding='utf-8')
    return gate


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--without-openfda-bulk', action='store_true')
    arguments = parser.parse_args()
    result = build(include_openfda=not arguments.without_openfda_bulk, log=lambda line: print(line, flush=True))
    print(json.dumps({k: result[k] for k in ('status', 'ready', 'counts')}, indent=1)[:6000])
    print(json.dumps([c for c in result['checks'] if not c['passed']], indent=1))
