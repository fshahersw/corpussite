"""Install reviewed open-source skills as project skills (2026-09-20).

Copies the chosen skill folders from the pinned lq-skills copy into <project>/.claude/skills/<name>/, byte for byte (SHA-256 of each
installed file is checked against the fetch manifest), and writes PROVENANCE.md beside them. Nothing is executed; a skill is text.
Only skills whose SKILL.md was read in full, and whose supporting files were scanned and found free of external calls or hidden instructions, are listed in REVIEWED.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / 'repos/LegalQuants__lq-skills'
TARGET = ROOT / '.claude/skills'
REVIEWED = {
    'statutory-analysis': 'First-pass framework for reading US statutes and regulations; draft-only, attorney review required.',
    'enhance-prompt': 'Rewrites a short prompt into a structured legal prompt and shows what changed; never silent.',
    'building-chronologies': 'Sourced event chronologies from documents; every event cites its source; gaps are listed, not filled.',
    'proposition-checking': 'Checks whether cited authorities or record material actually support each proposition.',
    'adversarial-qc': 'Two-reviewer quality control of a deliverable before it goes to a person.',
}


def main():
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    meta = json.loads((SOURCE / 'meta.json').read_text(encoding='utf-8'))
    installed = []
    for name in REVIEWED:
        prefix = 'skills/%s/' % name
        files = [path for path in manifest['files'] if path.startswith(prefix)]
        if not files:
            raise SystemExit('skill was not fetched: ' + name)
        for path in files:
            source = SOURCE / 'files' / path
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest['files'][path]['sha256']:
                raise SystemExit('pinned file changed since it was fetched: ' + path)
            destination = TARGET / name / path[len(prefix):]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            installed.append(path)
    shutil.copyfile(SOURCE / 'files/LICENSE', TARGET / 'LICENSE-lq-skills-Apache-2.0.txt')
    lines = ['# Project skills: provenance', '',
             'Installed %s from https://github.com/LegalQuants/lq-skills at commit `%s` (Apache-2.0; licence text in `LICENSE-lq-skills-Apache-2.0.txt`).' % (
                 datetime.now(timezone.utc).date().isoformat(), meta['commit']),
             'Files are unmodified copies; each was checked against the SHA-256 recorded when it was fetched',
             '(`sources/upstream_open_data_20260920/repos/LegalQuants__lq-skills/manifest.json`). Authors are named in each SKILL.md.', '',
             'Before installation each SKILL.md was read in full and every supporting file was scanned for external calls, credential requests and',
             'instructions to override the conversation: none was found. All of them produce drafts for attorney review, not legal advice.', '', '| Skill | What it is for |', '|---|---|']
    lines += ['| `%s` | %s |' % (name, note) for name, note in REVIEWED.items()]
    lines += ['', 'Reviewed and not installed: `bart-statutory-reference-checker` and `text-provenance` (Singapore-specific), `case-file-analyzer` (proof of concept),',
              '`legal-claim-economics` (UK funding structures). They remain in the pinned copy if wanted later.', '',
              'To refresh: run `fetch.py survey`, `fetch.py get LegalQuants/lq-skills "skills/<name>/*"`, re-read the changed files, then run this script again.', '']
    (TARGET / 'PROVENANCE.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'skills': list(REVIEWED), 'files_installed': len(installed), 'target': str(TARGET)}, indent=1))


if __name__ == '__main__':
    main()
