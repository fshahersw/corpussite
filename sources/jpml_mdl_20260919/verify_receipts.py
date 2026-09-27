"""Verify every saved raw file against its receipt (bytes + SHA-256). Offline; prints one line per receipt."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def verify():
    rows = []
    for line in (HERE / 'receipts.jsonl').read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rp = r.get('raw_path')
        row = {'seq': r['seq'], 'url': r['url'], 'raw_path': rp, 'verdict': r.get('verdict'), 'status': r.get('status'),
               'content_type': (r.get('headers') or {}).get('Content-Type'),
               'last_modified': (r.get('headers') or {}).get('Last-Modified'), 'receipt_sha256': r.get('sha256')}
        if not rp:
            row['check'] = 'no_raw_file'
        else:
            p = HERE / rp
            if not p.exists():
                row['check'] = 'missing_file'
            else:
                data = p.read_bytes()
                h = hashlib.sha256(data).hexdigest()
                row['check'] = 'ok' if (h == r.get('sha256') and len(data) == r.get('bytes')) else 'hash_mismatch'
                row['bytes'] = len(data)
        rows.append(row)
    return rows


if __name__ == '__main__':
    for row in verify():
        print(row['check'], row['seq'], row['raw_path'], row.get('bytes'), row['content_type'], row['last_modified'])
    for mod in ('pypdf', 'openpyxl', 'lxml'):
        try:
            m = __import__(mod)
            print(mod, getattr(m, '__version__', '?'))
        except Exception as e:
            print(mod, 'MISSING', e)
