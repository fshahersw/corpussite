"""Append connector-call entries (tool, arguments, UTC clock reading) to raw/_call_log.jsonl.

Usage:
  python log_calls.py get_usage before
  python log_calls.py get_state_coverage al ak az
  python log_calls.py get_county_coverage "pa:Philadelphia" "il:Cook"
  python log_calls.py --at "2026-09-19T05:31:00Z approx" get_usage before

The clock reading is taken by this script in the same tool block as the connector calls; the
connector itself exposes no per-call timestamp. This script never touches the network.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / 'raw/_call_log.jsonl'
TOOLS = {'get_usage', 'get_state_coverage', 'get_county_coverage'}


def main(argv):
    at = None
    if argv and argv[0] == '--at':
        at, argv = argv[1], argv[2:]
    tool, items = argv[0], argv[1:]
    if tool not in TOOLS: raise SystemExit('tool not allowed: ' + tool)
    existing = LOG.read_text(encoding='utf-8').splitlines() if LOG.exists() else []
    seq = len(existing)
    now = at or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    lines = []
    for item in items:
        seq += 1
        if tool == 'get_usage':
            arguments, tag = {}, item
        elif tool == 'get_state_coverage':
            arguments, tag = {'state': item}, item
        else:
            state, county = item.split(':', 1)
            arguments = {'state': state, 'county': county}
            tag = state + '_' + re.sub(r'[^a-z0-9]+', '-', county.lower()).strip('-')
        name = '%04d_%s_%s.response.json' % (seq, tool, tag)
        lines.append(json.dumps({'seq': seq, 'tool': tool, 'arguments': arguments, 'called_at_utc': now,
                                 'called_at_basis': 'explicit note' if at else 'agent clock reading taken in the same tool block as the connector call; the connector exposes no per-call time',
                                 'response_file': name}, ensure_ascii=False))
        print(seq, name, now)
    with open(LOG, 'a', encoding='utf-8', newline='\n') as handle:
        handle.write(''.join(line + '\n' for line in lines))


if __name__ == '__main__':
    main(sys.argv[1:])
