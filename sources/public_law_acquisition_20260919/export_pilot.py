"""Publish a bounded, hash-validated view of successful crawler captures only."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CORPUS = HERE / 'corpus'
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
from readable import reading_view
from pilot_reader import page_reading


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing artifact or path outside registered source root')
    return path


def source_error_page(title):
    return bool(re.search(r'(?i)^(?:404\b|page\s+not\s+found\b|access\s+denied\b|forbidden\b|error\s+40[134]\b)', (title or '').strip()))


def check_capture(seed, row, receipt, corpus=CORPUS):
    if row['status'] != 'downloaded' or receipt.get('status') != 'downloaded' or receipt.get('http_status') != 200 or receipt.get('raw_complete') is not True:
        raise ValueError('Not a complete successful source capture')
    if seed['url'] != row['url'] or receipt.get('requested_url') != row['url']:
        raise ValueError('Requested source identity mismatch')
    if source_error_page(receipt.get('title')):
        raise ValueError('Source error page returned with HTTP200')
    raw = checked_path(corpus, row['raw_path'])
    text = checked_path(corpus, row['text_path'])
    raw_data, text_data = raw.read_bytes(), text.read_bytes()
    if sha(raw_data) != row['sha256'] or sha(raw_data) != receipt.get('sha256') or len(raw_data) != receipt.get('byte_count'):
        raise ValueError('Raw hash/byte receipt mismatch')
    if sha(text_data) != receipt.get('text_sha256'):
        raise ValueError('Extracted text hash mismatch')
    if not text_data.decode('utf-8').strip():
        raise ValueError('Empty extraction is not a readable capture')
    return raw, text, raw_data, text_data


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def main():
    atomic_json(HERE / 'validation.json', {'status': 'quality_review_pending', 'passed': False})
    seeds = {x['url']: x for x in map(json.loads, (HERE / 'seeds.jsonl').read_text(encoding='utf-8').splitlines())}
    db = sqlite3.connect((CORPUS / 'corpus.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute('SELECT * FROM resources ORDER BY id')]
    if {r['url'] for r in rows} != set(seeds):
        raise ValueError('Crawler scope differs from frozen exact seed set')
    if db.execute('SELECT count(*) FROM runs WHERE ended_at IS NULL').fetchone()[0]:
        raise ValueError('Wait for the active collector to checkpoint')
    resources, gaps = [], []
    (HERE / 'reading').mkdir(exist_ok=True)
    for row in rows:
        seed = seeds[row['url']]
        receipt_path = checked_path(CORPUS, row['metadata_path'])
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if row['status'] != 'downloaded' or source_error_page(receipt.get('title')):
            gaps.append({'source_url': row['url'], 'kind': seed['category'], 'status': 'soft_404' if source_error_page(receipt.get('title')) else row['status'], 'http_status': row['last_http_status'], 'redirect_url': row['redirect_url'], 'error': row['error'] or ('Error page despite HTTP200' if source_error_page(receipt.get('title')) else None), 'access_receipt_path': receipt_path.relative_to(ROOT).as_posix(), 'access_receipt_sha256': sha(receipt_path.read_bytes()), 'directory_evidence': seed['directory_evidence']})
            continue
        raw, extracted, raw_data, text_data = check_capture(seed, row, receipt)
        cleaned = page_reading(raw_data, row['url'], row['title'] or seed['title'])
        if not cleaned['text'].strip():
            raise ValueError('Reader produced empty text for ' + row['url'])
        ident = sha(row['url'].encode())[:24]
        read_path = HERE / 'reading' / (ident + '.txt')
        read_path.write_text(cleaned['text'], encoding='utf-8')
        metadata_path = HERE / 'reading' / (ident + '.json')
        atomic_json(metadata_path, {'reading_notes': cleaned['notes'], 'links': cleaned['links'], 'parent_raw_sha256': row['sha256'], 'parent_extracted_sha256': sha(text_data), 'source_url': row['url']})
        resources.append({'id': 'public-law:' + ident, 'source_url': seed['url'], 'final_url': receipt['requested_url'], 'title': row['title'] or seed['title'], 'kind': seed['category'], 'captured_at': receipt['fetched_at'], 'raw_path': raw.relative_to(ROOT).as_posix(), 'raw_sha256': sha(raw_data), 'raw_bytes': len(raw_data), 'text_path': read_path.relative_to(ROOT).as_posix(), 'text_sha256': sha(read_path.read_bytes()), 'text_characters': len(cleaned['text']), 'mime_type': receipt.get('headers', {}).get('content-type', '').split(';')[0], 'extracted_path': extracted.relative_to(ROOT).as_posix(), 'extracted_sha256': sha(text_data), 'reading_metadata_path': metadata_path.relative_to(ROOT).as_posix(), 'reading_metadata_sha256': sha(metadata_path.read_bytes()), 'access_receipt_path': receipt_path.relative_to(ROOT).as_posix(), 'access_receipt_sha256': sha(receipt_path.read_bytes()), 'jurisdiction': seed['jurisdiction'], 'directory_evidence': seed['directory_evidence'], 'source_as_of': None, 'http_last_modified': receipt.get('headers', {}).get('last-modified'), 'http_status': receipt['http_status'], 'capture_kind': 'http_original', 'content_scope_note': 'This saves the linked page, not every document or judge profile linked from it.'})
    resources.sort(key=lambda x: x['source_url'])
    if len({x['id'] for x in resources}) != len(resources):
        raise ValueError('Duplicate resource identity')
    path = HERE / 'resources.jsonl'
    tmp = path.with_suffix('.jsonl.tmp')
    tmp.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in resources), encoding='utf-8')
    tmp.replace(path)
    atomic_json(HERE / 'gaps.json', {'items': gaps})
    summary = {'generated_at': datetime.now(timezone.utc).isoformat(), 'selected': len(seeds), 'saved': len(resources), 'readable': len(resources), 'selected_hosts': len({urlsplit(x).netloc for x in seeds}), 'saved_hosts': len({urlsplit(x['source_url']).netloc for x in resources}), 'statuses': dict(Counter(x['status'] for x in rows)), 'kinds': dict(Counter(x['kind'] for x in resources)), 'raw_bytes': sum(x['raw_bytes'] for x in resources), 'text_characters': sum(x['text_characters'] for x in resources), 'observed_links_not_fetched': db.execute('SELECT count(*) FROM links').fetchone()[0], 'initial_sandbox_socket_failures': db.execute("SELECT count(*) FROM fetches WHERE error LIKE '%WinError 10013%'").fetchone()[0], 'network_run': dict(db.execute('SELECT id,started_at,ended_at,processed,stop_reason FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()), 'full_source_corpus_complete': False, 'source_as_of': None, 'firecrawl_credits_used': 0}
    atomic_json(HERE / 'summary.json', summary)
    atomic_json(HERE / 'validation.json', {'status': 'passed', 'passed': True, 'validated_at': summary['generated_at'], 'resources_sha256': sha(path.read_bytes()), 'counts': {'resources': len(resources), 'selected': len(seeds), 'gaps': len(gaps), 'hosts': summary['saved_hosts']}, 'checks': ['Frozen exact URL set', 'Successful complete HTTP200 receipts', 'Reject explicit soft404/error titles', 'Original bytes hash/length', 'Extracted text hash', 'Nonempty cleaned reading copies', 'Workspace-root artifact confinement', 'Requested URL identity binding', 'Unique stable resource IDs'], 'summary_sha256': sha((HERE / 'summary.json').read_bytes()), 'gaps_sha256': sha((HERE / 'gaps.json').read_bytes())})
    db.close()
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
