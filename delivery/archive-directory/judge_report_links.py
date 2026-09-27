"""Source-bound saved dashboard references; never promoted to analytic measures."""
from functools import lru_cache
from pathlib import Path
import hashlib
import json

FOLDER = Path(__file__).resolve().parents[2] / 'sources/judge_report_links_20260919'


@lru_cache(maxsize=4)
def _load(folder, gate_stamp, data_stamp):
    base = Path(folder)
    gate = json.loads((base/'validation.json').read_text(encoding='utf8'))
    if gate.get('status') != 'passed' or gate.get('ready') is not True: return {}
    raw = (base/'links.jsonl').read_bytes()
    file = next((f for f in gate.get('data_files',[]) if f.get('path') == 'links.jsonl'), {})
    if hashlib.sha256(raw).hexdigest() != file.get('sha256'): return {}
    output = {}
    for line in raw.decode('utf8').splitlines():
        row = json.loads(line)
        if row.get('availability') != 'publisher_link' or row.get('numeric_analytics_saved') is not False: return {}
        output.setdefault(row['entity_id'], []).append({k:row[k] for k in ('title','url','kind','availability','publisher','source_url')})
    return output


def index(folder=FOLDER):
    try:
        gate, data = Path(folder)/'validation.json', Path(folder)/'links.jsonl'
        stamps = [(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in (gate,data)]
        return _load(str(folder), *stamps)
    except (OSError,ValueError,TypeError,KeyError): return {}


def references(entity_id, folder=FOLDER):
    return index(folder).get(entity_id, [])
