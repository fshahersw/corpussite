"""Validate saved Trellis anchors and join only exact, unique source-profile identities."""
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit
import hashlib
import json
import re
from bs4 import BeautifulSoup

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SOURCE = ROOT / 'delivery/judge_intelligence_20260913/trellis_profiles'
ENTITIES = ROOT / 'sources/judge_entities_20260918/entities.jsonl'


def sha(data): return hashlib.sha256(data).hexdigest()
def rows(path): return [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def write(path, obj): path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def main():
    filename = SOURCE / 'dashboard_report_links.jsonl'
    checksum = json.loads((SOURCE / 'files.sha256.json').read_text(encoding='utf8'))[filename.relative_to(ROOT).as_posix()]
    assert sha(filename.read_bytes()) == checksum['sha256']
    links = rows(filename)
    identity = defaultdict(set)
    for entity in rows(ENTITIES):
        for member in entity.get('members', []):
            if member.get('source_class') == 'trellis_profile':
                identity[member.get('source_url')].add(entity['entity_id'])
    by_file = defaultdict(list)
    for row in links: by_file[row['source_path']].append(row)
    output = []; unresolved = []; seen = set(); source_files = []
    for source_path, group in by_file.items():
        path = (ROOT / source_path).resolve()
        assert path.is_relative_to(ROOT) and path.is_file()
        raw = path.read_bytes(); digest = sha(raw); document = json.loads(raw)
        source_files.append({'path':source_path,'sha256':digest})
        for row in group:
            evidence = row['evidence']
            assert digest == row['source_sha256'] == evidence['source_sha256']
            html = document
            for token in evidence['json_pointer'].strip('/').split('/'):
                html = html[token.replace('~1','/').replace('~0','~')]
            fragment = html[evidence['html_character_start']:evidence['html_character_end']]
            assert sha(fragment.encode()) == evidence['html_fragment_sha256']
            hrefs = [a.get('href') for a in BeautifulSoup(fragment, 'html.parser').select('a[href]')]
            assert row['raw_href'] in hrefs
            assert row['numeric_analytics_saved'] is False and row['access_status'] == 'observed_link_only_not_fetched_by_normalizer'
            parsed = urlsplit(row['url'])
            assert parsed.scheme == 'https' and parsed.netloc == 'trellis.law' and not parsed.username
            entities = identity.get(row['source_url'], set())
            if len(entities) != 1:
                unresolved.append({'source_url':row['source_url'],'url':row['url'],'reason':'no unique exact source-profile entity'})
                continue
            entity_id = next(iter(entities)); key = (entity_id,row['url'])
            if key in seen: continue
            seen.add(key)
            output.append({'entity_id':entity_id,'source_url':row['source_url'],'url':row['url'],
                           'title':re.sub(r'\s+',' ',row.get('label') or 'Publisher report').strip(),
                           'kind':row['kind'],'availability':'publisher_link','publisher':'Trellis',
                           'numeric_analytics_saved':False,'source_sha256':digest,'anchor_sha256':evidence['html_fragment_sha256']})
    manifest = OUT / 'links.jsonl'
    manifest.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in output), encoding='utf8')
    write(OUT/'unresolved.json', unresolved)
    counts = {'references':len(output),'profiles':len({r['entity_id'] for r in output}), 'unresolved_references':len(unresolved),
              'verified_source_files':len(source_files),'numeric_analytics_added':0}
    write(OUT/'validation.json', {'schema_version':'1','status':'passed','ready':True,'validated_at':datetime.now(timezone.utc).isoformat(),
          'data_files':[{'path':'links.jsonl','sha256':sha(manifest.read_bytes()),'rows':len(output)}],
          'counts':counts,'qualification':'Saved source-page anchors; report contents were not downloaded. Exact unique source-profile join; no name matching.',
          'inputs':[{'path':str(filename.relative_to(ROOT)),'sha256':checksum['sha256']},{'path':str(ENTITIES.relative_to(ROOT)),'sha256':sha(ENTITIES.read_bytes())}],
          'source_files':source_files,'checks':['Every parent source hash and anchor fragment reverified','No duplicate entity/target pair','No numeric analysis inferred']})
    print(json.dumps(counts))


if __name__ == '__main__': main()
