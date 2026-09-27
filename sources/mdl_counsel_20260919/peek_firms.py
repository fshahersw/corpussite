"""Read-only diagnostic for the 2026-09-19 firm-text repair. Prints to the console only (never writes a file):
which search-index firm texts the rules reject, and which published firm texts still look unusual."""
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

manifest = json.loads((HERE / 'receipts' / 'manifest.json').read_text(encoding='utf-8'))
texts = []
for entry in manifest['responses']:
    if entry['is_error'] or entry['tool'].split('__')[-1] != 'search':
        continue
    data = json.loads((HERE / 'receipts' / entry['file']).read_text(encoding='utf-8'))
    for res in data.get('results') or []:
        texts += [build.clean(t) for t in res.get('firm') or [] if build.clean(t)]
print('search-index firm texts', len(texts))
print('\n== rejected')
for t in sorted(set(texts)):
    s = build.strip_admission_note(t)
    why = 'only note' if not s else build.firm_text_problem(s)
    if why:
        print('  [%s] %s' % (why[:38], t[:120]))
rows = [json.loads(l) for l in (HERE / 'firms.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
print('\n== published firm rows', len(rows))
odd = re.compile(r'\d|\b(building|tower|plaza|center|centre|ctr|square|house|place|bar|pro se|nj|phv|court|docket|case|plaintiff|defendant)\b|admit|adamit|u[sd]{2}c|member|pro hac|^(and|of)\b|^[^A-Za-z]', re.I)
for r in rows:
    if odd.search(r['name']) and not r['name'].lower().startswith('the '):
        print('  ', r['name'][:120])
print('\n== lower-case leading "the" after note removal')
print('  ', sum(1 for r in rows if r['name'].startswith('the ')), 'rows, e.g.', [r['name'] for r in rows if r['name'].startswith('the ')][:3])
atts = [json.loads(l) for l in (HERE / 'attorneys.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
flag = re.compile(r'#|\b(correction(al|s)?|prison|penitentiary|detention|inmate|jail)\b|^c\s*/\s*o\b', re.I)
print('\n== attorney names matching custody/hash patterns:', sum(1 for a in atts if flag.search(a['name'])))
