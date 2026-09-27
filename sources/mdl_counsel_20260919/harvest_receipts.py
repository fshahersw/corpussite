"""Copy connector responses for this run, verbatim, from the agent harness log into receipts/.

Why a harvester: MCP connector responses reach the agent as tool-result text. The harness logs that text
(and spills large results to a side file) with exact UTC timestamps. Copying from the log avoids any agent
re-typing, so every receipt is the byte-exact text content the connector returned.

These are CONNECTOR RESPONSES, NOT ORIGINAL HTTP BYTES / NOT ORIGINAL COURT BYTES. The SHA-256 is of the saved
receipt file. This script is a one-time acquisition helper; build.py never needs the harness log (it reads
receipts/ only).

Usage: python harvest_receipts.py <agent-transcript.jsonl>
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'receipts'
CONNECTORS = {
    'mcp__ae4a3886-60bb-4f1d-bd3a-ae864e2ec458__': 'courtlistener',
}
SPILL_TXT = re.compile(r'Output has been saved to (.+?\.txt)\.?\s', re.S)
SPILL_JSON = re.compile(r'Full output saved to: (.+?\.json)\s', re.S)
LABEL = ('CONNECTOR RESPONSES - not original HTTP bytes and not original court bytes. Each file is the verbatim text '
         'content of one MCP tool result as logged by the agent harness (large results were spilled by the harness to a '
         'side file; the text content of that side file was copied). sha256 is of the saved receipt file. Receipts '
         'contain attorney contact text (e-mail, telephone, street address) exactly as returned by the connector; they '
         'are raw evidence and must never be served publicly.')


def _text(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return '\n'.join(parts)


def _payload(text):
    """Return (bytes, spill_kind). Handles the two spill formats the harness uses."""
    match = SPILL_TXT.search(text + ' ')
    if text.startswith('Error: result (') and match:
        return Path(match.group(1).strip()).read_bytes(), 'side_file_txt'
    match = SPILL_JSON.search(text + ' ')
    if '<persisted-output>' in text and match:
        blocks = json.loads(Path(match.group(1).strip()).read_text(encoding='utf-8'))
        return _text(blocks).encode('utf-8'), 'side_file_json_text_blocks'
    return text.encode('utf-8'), None


def harvest(transcript):
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
    OUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for seq, use_id in enumerate(order, 1):
        if use_id not in results:
            continue
        use, res = uses[use_id], results[use_id]
        payload, spilled = _payload(res['text'])
        stripped = payload.lstrip()[:1]
        ext = 'json' if stripped in (b'{', b'[') else 'txt'
        name = f"{seq:03d}_{use['connector']}_{use['tool_short']}.response.{ext}"
        (OUT / name).write_bytes(payload)
        entries.append({
            'seq': seq, 'connector': use['connector'], 'tool': use['tool'], 'arguments': use['arguments'],
            'called_utc': use['called_utc'], 'received_utc': res['received_utc'],
            'timestamp_basis': 'agent harness log timestamps of the tool call and of its result (UTC)',
            'file': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'is_error': (res['is_error'] and not spilled) or ext == 'txt',
            'spilled_by_harness': spilled,
            'fidelity': 'verbatim text content of the MCP tool result; connector response, not original HTTP/court bytes',
        })
    manifest = {'label': LABEL, 'responses': entries}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    return entries


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    done = harvest(sys.argv[1])
    print(json.dumps({'receipts': len(done), 'bytes': sum(e['bytes'] for e in done),
                      'errors': sum(1 for e in done if e['is_error'])}))
