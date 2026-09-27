"""Export existing observed legal URLs for the shared crawler, without fetching."""
import json,re,sys
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import urlparse,urlsplit,urlunsplit,unquote
from collect import ROOT,read_jsonl,write_json,now
sys.path.insert(0,str(ROOT.parents[1]/'pipeline'))
import corpus_crawler as crawler

OUT=ROOT/'continuation';OUT.mkdir(exist_ok=True)
def jsonl(path,rows):
    with path.open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
def official_host(host):
    return host.endswith('.gov') or bool(re.search(r'\.(?:state\.[a-z]{2}|(?:co|ci)\.[^.]+\.[a-z]{2})\.us$',host))

def export():
    sources=read_jsonl(ROOT/'manifests/sources.jsonl')
    frontier=read_jsonl(ROOT/'manifests/remaining_frontier.jsonl')
    attempted={};captured={};skip=set();blocked=set()
    for r in sources:
        for value in [r['requested_url'],r.get('final_url')]:
            u=crawler.canonical_url(value)
            if not u:continue
            attempted[u]=dict(url=u,verification_status=r['verification_status'],source_id=r['source_id'],evidence_path=str(ROOT/r['raw_path']) if r.get('raw_path') else None,sha256=r.get('sha256'))
            skip.add(u);p=urlsplit(u);skip.add(urlunsplit(('http' if p.scheme=='https' else 'https',p.netloc,p.path,p.query,'')))
            if r.get('raw_path'):captured[u]=attempted[u]
            if r['verification_status'] in ['access_barrier','challenge_page']:blocked.add(p.netloc)
    jsonl(OUT/'captured_url_manifest.jsonl',list(captured.values()))
    jsonl(OUT/'attempted_url_manifest.jsonl',list(attempted.values()))
    (OUT/'existing_url_skip.txt').write_text('\n'.join(sorted(skip))+'\n',encoding='utf-8')
    write_json(OUT/'blocked_hosts.json',sorted(blocked))
    state_codes={s['state']:s['usps'] for s in json.loads((ROOT/'datasets/states_50_plus_dc.json').read_text())}
    known_court_hosts={urlparse(p['official_url']).netloc for p in json.loads((ROOT/'datasets/state_judiciary_portals_verified.json').read_text())}
    known_court_hosts.update(['www.oscn.net','oscn.net','www.cockecircuit.com','www.cookcountyclerkofcourt.org','services.cookcountyclerkofcourt.org'])
    rejected=[];seeds=[];seen=set();host_paths=defaultdict(set)
    bad=re.compile(r'facebook|twitter|instagram|linkedin|youtube|vimeo|google|findlaw|justia|trellis|lexis|westlaw|lawmoose|municode|amlegal|courtlistener|casetext|courtreference|onlinesearches|searchsystems|govpay|lawyersweekly|socialaw|law\.duke|law\.cornell',re.I)
    unrelated=re.compile(r'(?:^|/)(?:news|press|media-releases|events|employment|jobs|careers|donate|donations|subscribe|subscription|cart|checkout|payment|payments|contact-us|feedback|survey|complaint|training|education|recruitment|bid|bids|procurement|purchasing|vendors|accessibility|privacy|terms)(?:/|[.?_-]|$)',re.I)
    legal=re.compile(r'court|judg|justice|docket|case|rule|statut|constitut|motion|bench.?book|clerk|judicia|legal|law|opinion|order|county|counties|district',re.I)
    default=crawler.Config.from_dict({'allow':[{'host':'placeholder.invalid','path_prefixes':['/']}]})
    for row in frontier:
        u=crawler.canonical_url(row['official_url']);reason=None
        if not u:reason='invalid_url'
        elif u in skip:reason='already_archived_or_attempted'
        elif u in seen:reason='duplicate'
        else:
            p=urlsplit(u);host=p.netloc
            if host in blocked:reason='host_has_observed_access_barrier'
            elif bad.search(host):reason='nonofficial_reference'
            elif unrelated.search(p.path):reason='noncorpus_or_control_path'
            elif not legal.search(row['label']+' '+p.path):reason='not_legal_or_court_link'
            else:
                origin_host=urlparse(row['discovered_from']).netloc
                trusted=official_host(host) or host in known_court_hosts
                if not trusted:reason='external_host_not_officially_verified'
                elif any(crawler.ACTION_SEGMENT.fullmatch(segment) for segment in p.path.split('/')):reason='control_path'
                elif Path(p.path).suffix.lower() in crawler.ASSET_SUFFIXES:reason='asset'
                elif re.search(r'(?:^|[?&])(?:action|do|method|operation)=(?:delete|remove|create|edit|update|submit|upload|logout|login|purchase|checkout)',p.query,re.I):reason='control_query'
        if reason:rejected.append(dict(**row,exclusion_reason=reason));continue
        p=urlsplit(u);host=p.netloc;path=unquote(p.path or '/')
        if '?' in path or '#' in path:
            rejected.append(dict(**row,exclusion_reason='unrepresentable_exact_scope'));continue
        test_cfg=crawler.Config.from_dict({'allow':[{'host':host,'path_prefixes':[path]}]})
        accepted,why=test_cfg.allowed(u)
        if not accepted:
            rejected.append(dict(**row,exclusion_reason=why));continue
        seen.add(u);host_paths[host].add(path)
        state=row['jurisdiction'].split('/')[0]
        jurisdiction={'country':'US','state':state,'state_code':state_codes.get(state,'')}
        if '/' in row['jurisdiction']:jurisdiction['county']=row['jurisdiction'].split('/',1)[1]
        seeds.append(dict(url=u,source_family='official_courts',jurisdiction=jurisdiction,category=row['category'],discovered_from=row['discovered_from'],scope={'host':host,'path_prefixes':[path]},label=row['label'],discovery_evidence_path=str(ROOT/row['discovery_evidence_path']),official_basis='government domain or verified official state/county court host'))
    priority={'document':0,'judge_profile_or_directory':1,'court_rules':2,'docket_or_case_access':3,'court_or_county_directory':4,'court_related':5}
    seeds.sort(key=lambda r:(priority.get(r['category'],9),r['jurisdiction'].get('state',''),r['url']))
    config=dict(allow=[{'host':h,'path_prefixes':sorted(paths)} for h,paths in sorted(host_paths.items())],workers=4,per_host_delay=2.0,timeout_seconds=30,max_transfer_seconds=180,max_response_bytes=67108864,max_extract_chars=20971520,min_free_bytes=2147483648,max_depth=0,max_retries=2,backoff_base_seconds=30,max_backoff_seconds=3600,respect_robots=True,follow_links=False,follow_external_allowed_links=False,pause_host_on_access_block=True,user_agent='LegalCorpusResearch/1.0')
    cfg=crawler.Config.from_dict(config)
    for seed in seeds:assert cfg.allowed(seed['url'],seed['scope'])[0],seed['url']
    assert not ({s['url'] for s in seeds}&skip)
    jsonl(OUT/'official_courts.seeds.jsonl',seeds);write_json(OUT/'official_courts.config.json',config);jsonl(OUT/'excluded_frontier.jsonl',rejected)
    summary=dict(generated_at_utc=now(),eligible_seeds=len(seeds),exact_hosts=len(host_paths),captured_url_records=len(captured),attempted_url_records=len(attempted),skip_urls_including_http_https_aliases=len(skip),blocked_hosts=len(blocked),excluded=dict(Counter(r['exclusion_reason'] for r in rejected)),seed_categories=dict(Counter(s['category'] for s in seeds)),seed_config_validation='all seeds satisfy shared crawler Config.allowed; zero intersection with archived/attempted skip set',automatic_link_expansion=False,note='This batch fetches observed URLs only. New links are recorded for a subsequent filtered export. Captured URL evidence and protocol aliases are supplied separately for integration skip checks, including redirects.')
    write_json(OUT/'handoff_summary.json',summary);print(json.dumps(summary,indent=2))
    (OUT/'README.md').write_text('''# Official court continuation

`official_courts.seeds.jsonl` and `official_courts.config.json` match `pipeline/corpus_crawler.py`. The batch contains only observed, not-yet-attempted legal/court links on government domains or verified official court hosts. Already captured/attempted URLs and HTTP/HTTPS aliases are removed. Known blocked hosts and arbitrary external domains are excluded.

`captured_url_manifest.jsonl` provides original evidence paths/hashes. `existing_url_skip.txt` additionally includes attempted URLs and protocol aliases; these aliases are skip keys, not claims of separate captures. Import that skip set when combining with an existing corpus, including during redirect handling.

Use the shared crawler's `ingest --seeds <absolute seeds path> --config <absolute config path>` with the intended corpus `--root`, then `run`. This config limits acquisition to the exported observed paths and disables automatic link expansion. New link observations should undergo the same filter before another batch; this prevents previously archived links from being fetched through navigation.

All selected seeds pass the shared crawler's own `Config.allowed` validation. No requests are made by this exporter. The raw unrestricted discovery graph remains in `../manifests/source_map.jsonl` for review.
''',encoding='utf-8')

if __name__=='__main__':export()
