"""Combine reviewed county-entry inventories without making network requests."""
import datetime,json,pathlib,urllib.parse

ROOT=pathlib.Path(__file__).resolve().parents[1]
GEO=ROOT/'reports/geography'
def main():
    files=[GEO/'cisa_county_entries/seeds.jsonl',GEO/'official_county_website_seeds.jsonl']
    rows=[json.loads(line) for file in files for line in file.read_text(encoding='utf-8').splitlines() if line.strip()]
    config=json.loads((GEO/'cisa_county_entries/config.json').read_text(encoding='utf-8'))
    hosts={rule['host']:set(rule['path_prefixes']) for rule in config['allow']}
    for row in rows:
        parsed=urllib.parse.urlsplit(row['url'])
        if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
            raise ValueError('Invalid public county entry URL')
        scope=row.get('scope') or {}
        hosts.setdefault(parsed.netloc.lower(),set()).update(scope.get('path_prefixes') or [parsed.path or '/'])
    config['allow']=[{'host':host,'path_prefixes':sorted(prefixes)} for host,prefixes in sorted(hosts.items())]
    config.update(min_free_bytes=10*1024**3,follow_links=False,max_depth=0,workers=3)
    out=GEO/'county_sites_batch';out.mkdir(exist_ok=True)
    (out/'seeds.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    (out/'config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    summary={'prepared_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seed_records':len(rows),'unique_urls':len({r['url'] for r in rows}),'source_files':[str(file.relative_to(ROOT)) for file in files],'network_requests':0,'full_corpus_complete':False,'notes':['CISA entries verify domain registration, not a website or county/court assignment.','Trellis website associations retain their unverified authority label.','This is a finite entry-page batch; saved legal links require subsequent review and acquisition.','Do not rebuild this input snapshot to resume an already ingested queue.']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
