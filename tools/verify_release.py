"""Compares what GitHub actually holds with data_manifest.json: every part present, same size, same SHA-256 (GitHub reports
a digest per asset). Read-only.  python tools/verify_release.py"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'data_manifest.json').read_text(encoding='utf-8'))
repo = manifest['repository']


def assets(tag):
    release = subprocess.run(['gh', 'api', 'repos/%s/releases/tags/%s' % (repo, tag), '-q', '.id'], capture_output=True, text=True, check=True).stdout.strip()
    listed = subprocess.run(['gh', 'api', '--paginate', 'repos/%s/releases/%s/assets?per_page=100' % (repo, release), '-q', '.[] | [.name, .size, (.digest // ""), .state] | @tsv'],
                            capture_output=True, text=True, check=True).stdout
    return {line.split('\t')[0]: line.split('\t') for line in listed.splitlines() if line}


found = {}
for tag in sorted({unit['release_tag'] for unit in manifest['units']}):
    found.update(assets(tag))
missing, wrong_size, wrong_digest, without_digest, expected = [], [], [], 0, set()
for unit in manifest['units']:
    for part in unit['parts']:
        expected.add(part['name'])
        row = found.get(part['name'])
        if row is None or row[3] != 'uploaded':
            missing.append(part['name'])
        elif int(row[1]) != part['bytes']:
            wrong_size.append(part['name'])
        elif not row[2]:
            without_digest += 1
        elif row[2] != 'sha256:' + part['sha256']:
            wrong_digest.append(part['name'])
orphans = sorted(set(found) - expected - {'data_manifest.json'})
print(json.dumps({'units': len(manifest['units']), 'units_uploaded': manifest['units_uploaded'], 'parts_expected': len(expected), 'assets_on_github': len(found),
                  'missing': missing, 'wrong_size': wrong_size, 'wrong_sha256': wrong_digest, 'parts_github_gave_no_digest_for': without_digest,
                  'assets_not_in_manifest': orphans[:20], 'raw_gb': round(manifest['bytes_total'] / 2**30, 2),
                  'packed_gb': round(sum(p['bytes'] for u in manifest['units'] for p in u['parts']) / 2**30, 2)}, indent=1))
sys.exit(1 if missing or wrong_size or wrong_digest else 0)
