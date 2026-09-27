"""Verify derived reader hashes and the complete validated Seeger recovery overlay."""
from pathlib import Path
import datetime
import hashlib
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
from server import sid
from readable import VERSION


def main():
    folder = ROOT / 'sources/reading_views_20260918'
    db = sqlite3.connect((folder / 'reading.sqlite3').as_uri() + '?mode=ro', uri=True)
    errors = []

    def check(row):
        record_id, text, raw_notes, version = row
        notes = json.loads(raw_notes)
        if hashlib.sha256(text.encode()).hexdigest() != notes.get('output_sha256'):
            errors.append([record_id, 'output_hash'])
        if version != VERSION or notes.get('version') != version:
            errors.append([record_id, 'version'])
        return notes

    sample = db.execute('SELECT record_id,text,notes,version FROM reading ORDER BY record_id LIMIT 200').fetchall()
    for row in sample:
        check(row)
    checked = 0
    manifest = ROOT / 'sources/seeger_text_recovery_20260918/resources.jsonl'
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        item = json.loads(line)
        if item['status'] != 'recovered':
            continue
        # The directory namespaces/hashes imported source IDs, as its builder does.
        record_id = sid('seeger:' + item['id'])
        row = db.execute('SELECT record_id,text,notes,version FROM reading WHERE record_id=?', (record_id,)).fetchone()
        if row is None:
            errors.append([record_id, 'missing'])
            continue
        notes = check(row)
        if notes.get('parent_raw_sha256') != item['raw_sha256']:
            errors.append([record_id, 'raw_parent'])
        if notes.get('parent_text_sha256', notes.get('input_text_sha256')) != item['text_sha256']:
            errors.append([record_id, 'text_parent'])
        if not row[1].strip():
            errors.append([record_id, 'empty_recovery'])
        checked += 1
    total, readable = db.execute('SELECT count(*),sum(text!=\'\') FROM reading WHERE version=?', (VERSION,)).fetchone()
    db.close()
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8-sig'))
    if (total, readable) != (summary['records'], summary['readable_records']):
        errors.append(['summary', 'counts'])
    receipt = {
        'validated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'records': total, 'readable_records': readable, 'empty_records': total-readable,
        'output_hash_samples_verified': len(sample),
        'all_recovered_rows_and_parent_hashes_verified': checked,
        'focused_canonical_binding_receipt': 'reports/enrichment_upgrade_20260918/reader_source_audit.json',
        'errors': errors, 'status': 'passed' if not errors else 'failed',
        'not_a_legal_currency_or_ocr_accuracy_assessment': True,
    }
    (folder / 'validation.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'errors'}))
    if errors:
        raise SystemExit(f'{len(errors)} validation failures; see validation.json')


if __name__ == '__main__':
    main()
