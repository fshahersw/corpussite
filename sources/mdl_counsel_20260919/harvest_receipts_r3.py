"""Round 3, ADDITIVE harvester: append this session's CourtListener connector responses to receipts/.

Same fidelity rule as harvest_receipts.py (verbatim text content of each MCP tool result copied from the agent harness
log; connector responses, NOT original HTTP/court bytes). Differences: never rewrites an existing receipt file,
numbers new receipts after the highest existing seq, records the harness tool_use id so a re-run is idempotent, and
only appends entries to manifest.json (existing entries are left byte-for-byte as they were).

Usage: python harvest_receipts_r3.py <agent-transcript.jsonl>
"""
from __future__ import annotations

import hashlib
import json
import sys

from harvest_receipts import CONNECTORS, OUT, _payload, _text


def harvest(transcript):
    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    seen = {e.get('tool_use_id') for e in manifest['responses'] if e.get('tool_use_id')}
    seq = max(e['seq'] for e in manifest['responses'])
    uses, order, results = {}, [], {}
    with open(transcript, encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            content = (row.get('message') or {}).get('content')
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    name = block.get('name', '')
                    for prefix, connector in CONNECTORS.items():
                        if name.startswith(prefix) and block['id'] not in uses:
                            uses[block['id']] = {'connector': connector, 'tool': name, 'tool_short': name[len(prefix):],
                                                 'arguments': block.get('input') or {}, 'called_utc': row.get('timestamp')}
                            order.append(block['id'])
                elif block.get('type') == 'tool_result' and block.get('tool_use_id') in uses:
                    results[block['tool_use_id']] = {'text': _text(block.get('content')), 'received_utc': row.get('timestamp'),
                                                     'is_error': bool(block.get('is_error'))}
    added = []
    for use_id in order:
        if use_id not in results or use_id in seen:
            continue
        use, res = uses[use_id], results[use_id]
        payload, spilled = _payload(res['text'])
        ext = 'json' if payload.lstrip()[:1] in (b'{', b'[') else 'txt'
        seq += 1
        name = f"{seq:03d}_{use['connector']}_{use['tool_short']}.response.{ext}"
        if (OUT / name).exists():
            raise SystemExit(f'refusing to overwrite existing receipt {name}')
        (OUT / name).write_bytes(payload)
        added.append({
            'seq': seq, 'connector': use['connector'], 'tool': use['tool'], 'arguments': use['arguments'],
            'called_utc': use['called_utc'], 'received_utc': res['received_utc'],
            'timestamp_basis': 'agent harness log timestamps of the tool call and of its result (UTC)',
            'file': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'is_error': (res['is_error'] and not spilled) or ext == 'txt',
            'spilled_by_harness': spilled,
            'fidelity': 'verbatim text content of the MCP tool result; connector response, not original HTTP/court bytes',
            'round': 3, 'tool_use_id': use_id,
        })
    manifest['responses'].extend(added)
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    return added


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    done = harvest(sys.argv[1])
    print(json.dumps({'receipts_added': len(done), 'first_seq': done[0]['seq'] if done else None,
                      'last_seq': done[-1]['seq'] if done else None, 'bytes': sum(e['bytes'] for e in done),
                      'errors': sum(1 for e in done if e['is_error'])}))
