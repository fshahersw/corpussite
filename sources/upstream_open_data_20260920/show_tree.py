"""Print a surveyed repository's file list compactly: python show_tree.py <owner/name> [substring]"""
import json
import sys
from pathlib import Path

repo = sys.argv[1]
needle = sys.argv[2] if len(sys.argv) > 2 else ''
files = json.loads((Path(__file__).resolve().parent / 'repos' / repo.replace('/', '__') / 'tree.json').read_text(encoding='utf-8'))
shown = [f for f in files if needle in f['path']]
print(repo, len(shown), 'of', len(files), 'files,', sum(f['bytes'] or 0 for f in shown), 'bytes')
for f in shown[:400]:
    print('%10s  %s' % (f['bytes'], f['path']))
