"""Write get_state_coverage text payloads from the collecting agent's compact transcription.

The Trellis connector returns a fully regular payload for get_state_coverage:
  {"error":null,"access":null,"no_results":null,"coverage_flags":{<11 flags>},"counties":[{"county_name":..,
   "courthouse_count":N,"has_documents":bool},...]}
The agent transcribes only the variable parts (flag bits, county names, non-default counts/flags) and this
template re-emits the constant boilerplate character for character. `--selftest` proves the template
reproduces, byte for byte, the New Jersey payload that the scouting agent saved verbatim earlier today.
Irregular responses (errors, unexpected keys) are NOT written with this tool; they are saved directly.

stdin format (one or more blocks):
  @ <response file name>
  flags <11 chars of 0/1 in FLAG order>
  suffix <text appended to names that end with '+'>      (optional, default ' County')
  <name>[+][|<courthouse_count>|<has_documents 0/1>]
Names are written exactly as they appear inside the JSON payload (JSON escapes kept as-is).
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / 'raw'
FLAGS = ['civil_cases', 'family_cases', 'probate_cases', 'documents', 'verdicts', 'judge_bios', 'judge_analytics',
         'court_comparison', 'state_law', 'motion_and_issues', 'tentative_rulings']
SCOUT_NJ = HERE.parents[1] / 'reports/corpus_upgrade_20260919/understand/connector_samples/13_trellis_get_state_coverage_nj.response.json'


def render(bits, counties):
    if len(bits) != len(FLAGS) or set(bits) - {'0', '1'}: raise SystemExit('bad flags: ' + bits)
    flags = ','.join('"%s":%s' % (name, 'true' if bit == '1' else 'false') for name, bit in zip(FLAGS, bits))
    rows = ','.join('{"county_name":"%s","courthouse_count":%d,"has_documents":%s}' % (name, count, 'true' if docs else 'false')
                    for name, count, docs in counties)
    return '{"error":null,"access":null,"no_results":null,"coverage_flags":{%s},"counties":[%s]}' % (flags, rows)


def parse(text):
    blocks, current = [], None
    for line in text.splitlines():
        line = line.rstrip('\r')
        if not line.strip(): continue
        if line.startswith('@ '):
            current = {'file': line[2:].strip(), 'flags': None, 'suffix': ' County', 'counties': []}
            blocks.append(current)
        elif line.startswith('flags '): current['flags'] = line[6:].strip()
        elif line.startswith('suffix '): current['suffix'] = line[7:]
        elif line.startswith('docs '): current['docs'] = line[5:].strip() == '1'   # block default for has_documents
        else:
            name, count, docs = line, 0, current.get('docs', True)
            if '|' in line:
                name, count, docs = line.split('|')
                count, docs = int(count), docs == '1'
            if name.endswith('+'): name = name[:-1] + current['suffix']
            current['counties'].append((name, count, docs))
    return blocks


def selftest():
    wrapper = json.loads(SCOUT_NJ.read_text(encoding='utf-8'))
    expected = wrapper['result'][0]['text']
    parsed = json.loads(expected)
    bits = ''.join('1' if parsed['coverage_flags'][k] else '0' for k in FLAGS)
    assert list(parsed['coverage_flags']) == FLAGS
    rows = [(c['county_name'], c['courthouse_count'], c['has_documents']) for c in parsed['counties']]
    assert render(bits, rows) == expected, 'template does not reproduce the verbatim scout payload'
    print('selftest ok: template reproduces scout NJ payload byte for byte,', len(expected.encode('utf-8')), 'bytes')


def main():
    if '--selftest' in sys.argv: return selftest()
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    inventory = {}
    for line in (HERE.parents[1] / 'delivery/focused_legal_corpus/counties/counties.jsonl').read_text(encoding='utf-8-sig').splitlines():
        if line.strip():
            row = json.loads(line)
            inventory.setdefault(row['usps'].lower(), set()).add(row['name'])
    for block in parse(sys.stdin.read()):
        target = RAW / block['file']
        if target.parent != RAW or not block['file'].endswith('.response.json'): raise SystemExit('bad name')
        if target.exists(): raise SystemExit('refusing to overwrite ' + block['file'])
        payload = render(block['flags'], block['counties']).encode('utf-8')
        json.loads(payload.decode('utf-8'))
        target.write_bytes(payload)
        names = [c[0] for c in block['counties']]
        dupes = sorted({n for n in names if names.count(n) > 1})
        print(block['file'], 'counties', len(names), 'bytes', len(payload), hashlib.sha256(payload).hexdigest()[:16],
              'DUPLICATES ' + repr(dupes) if dupes else '')
        # Transcription aid only: names that do not match the county inventory are re-read against the response.
        known = inventory.get(block['file'].split('_')[-1].split('.')[0], set())
        decoded = [json.loads('"%s"' % n) for n in names]
        print('   inventory', len(known), 'not-in-inventory:', [n for n in decoded if n not in known],
              '| inventory-not-listed:', len(known - set(decoded)))


if __name__ == '__main__':
    main()
