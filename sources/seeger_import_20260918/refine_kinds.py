"""Classify supplied law families without treating procedural rules as statutes."""
from collections import Counter
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
rows = [json.loads(line) for line in (OUT / 'resources.jsonl').read_text(encoding='utf-8').splitlines()]
for row in rows:
    if row['metadata']['record_type'] == 'original_document':
        row['metadata']['kind_basis'] = 'source catalog/title organizing hint, not an independent content review'
    elif row['metadata']['family'] == 'usc':
        source = json.loads((ROOT / row['metadata_path']).read_bytes())['source_record']
        if source['family'] not in ('USC9', 'USC28'):
            row['kind'] = row['resource_kind'] = 'rule_provision'
for name, records in [('resources.jsonl', rows), ('originals.jsonl', [r for r in rows if r['metadata']['record_type'] == 'original_document'])]:
    with (OUT / name).open('wb') as stream:
        for row in records: stream.write(json.dumps(row, ensure_ascii=False).encode('utf-8') + b'\n')
summary = json.loads((OUT / 'summary.json').read_bytes())
summary.update(by_kind=dict(Counter(r['kind'] for r in rows)),
    resources_sha256=hashlib.sha256((OUT / 'resources.jsonl').read_bytes()).hexdigest(),
    originals_sha256=hashlib.sha256((OUT / 'originals.jsonl').read_bytes()).hexdigest())
(OUT / 'summary.json').write_bytes((json.dumps(summary, indent=2) + '\n').encode('utf-8'))
(OUT / 'progress.json').write_bytes((json.dumps({'status': 'import_finished_pending_validation', **summary}, indent=2) + '\n').encode('utf-8'))
print(json.dumps({'by_kind': summary['by_kind'], 'resources_sha256': summary['resources_sha256']}, indent=2))
