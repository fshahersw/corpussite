"""Build jurisdiction baseline and extract court source entries from primary indexes."""
import csv, json, re, sys
from collections import Counter
from urllib.parse import urljoin,urlparse,urldefrag
from bs4 import BeautifulSoup
from collect import ROOT, MANIFEST, batch, fetch, links, read_jsonl, write_json

def export(name,rows):
    write_json(ROOT/'datasets'/f'{name}.json',rows)
    if rows:
        fields=list(dict.fromkeys(k for r in rows for k in r))
        with (ROOT/'datasets'/f'{name}.csv').open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader();w.writerows(rows)

def current(): return list({r['requested_url']:r for r in read_jsonl(MANIFEST)}.values())
def baseline():
    counties=json.loads((ROOT/'datasets/2026_Gaz_counties_national.json').read_text(encoding='utf-8'))
    states=json.loads((ROOT/'datasets/2026_Gaz_state_national.json').read_text(encoding='utf-8'))
    wanted=[s for s in states if s['USPS']!='PR']
    lookup={s['USPS']:s for s in wanted}
    export('states_50_plus_dc',[dict(state=s['NAME'],usps=s['USPS'],state_fips=s['GEOID'],geography_vintage=2026,source_dataset='2026_Gaz_state_national.txt') for s in wanted])
    export('counties_50_plus_dc',[dict(state=lookup[c['USPS']]['NAME'],usps=c['USPS'],state_fips=c['GEOID'][:2],county_fips=c['GEOID'][2:],geoid=c['GEOID'],name=c['NAME'],geography_vintage=2026,source_dataset='2026_Gaz_counties_national.txt',geography_note='Census county or county-equivalent; not necessarily a county court jurisdiction',**{k:c.get(k) for k in ['ALAND','AWATER','ALAND_SQMI','AWATER_SQMI','INTPTLAT','INTPTLONG']}) for c in counties if c['USPS'] in lookup])

def doj():
    baseline()
    states=json.loads((ROOT/'datasets/states_50_plus_dc.json').read_text(encoding='utf-8'))
    wanted={s['state'].casefold():s for s in states}
    sources=[]; portals=[]; seeds=[]
    for rec in current():
        if rec['category']!='doj_state_court_index' or rec['verification_status']!='retrieved':continue
        state=wanted.get(rec['jurisdiction'].casefold())
        if not state:continue
        soup=BeautifulSoup((ROOT/rec['raw_path']).read_bytes(),'html.parser')
        section='';sub='';candidates=[]
        for node in soup.find_all(['h2','h3','a']):
            label=node.get_text(' ',strip=True)
            if node.name=='h2':section=label;sub='';continue
            if node.name=='h3':sub=label;continue
            href=node.get('href','')
            if not href or href.startswith('#'):continue
            url=urldefrag(urljoin(rec['final_url'],href))[0]
            if urlparse(url).scheme not in ('http','https'):continue
            if section not in ['State and Local Courts','Local Government','Legislature and Laws']:continue
            sources.append(dict(state=state['state'],usps=state['usps'],category=section,subcategory=sub,label=label,official_url=url,discovered_from=rec['final_url'],discovery_evidence_path=rec['raw_path'],verification_status='discovered_not_fetched',official_status='government_index_link; destination not independently classified'))
            if section=='State and Local Courts' and sub!='Criminal Information':
                if any(x in urlparse(url).netloc.lower() for x in ['findlaw','justia','municode','netronline','onlinesearches','courtreference','statelocalgov','courtsearch','searchsystems']):continue
                candidates.append((sub,label,url))
        primary=[c for c in candidates if c[0] in ['State Judiciary','District Judiciary']][:1]
        if not primary: primary=[c for c in candidates if c[0]=='Supreme Court'][:1]
        for sub,label,url in primary:
            request_url='https:'+url[5:] if url.startswith('http:') else url
            portals.append(dict(state=state['state'],usps=state['usps'],state_fips=state['state_fips'],category='state_judiciary_portal',label=label,official_url=request_url,discovered_url=url,discovered_from=rec['final_url'],discovery_evidence_path=rec['raw_path'],verification_status='discovered_not_fetched'))
            seeds.append(dict(jurisdiction=state['state'],usps=state['usps'],category='state_judiciary_portal',url=request_url,discovered_from=rec['final_url'],label=label,discovered_url=url))
        local=[c for c in candidates if c[0] in ['Local/Lower Courts','Court Forms and Administration']]
        local.sort(key=lambda c: (not bool(re.search(r'directory|court links|locations|local courts|trial courts|judicial circuits|district courts',c[1],re.I)),not bool(re.search(r'case|record|search|docket',c[1],re.I))))
        for sub,label,url in local[:4]:
            if re.search(r'bar|inmate|prison|sheriff|correction|forms',label,re.I):continue
            seeds.append(dict(jurisdiction=state['state'],usps=state['usps'],category='court_directory_or_case_access',url='https:'+url[5:] if url.startswith('http:') else url,discovered_from=rec['final_url'],label=label,discovered_url=url))
    export('doj_discovered_sources',sources)
    export('state_judiciary_portals',portals)
    write_json(ROOT/'manifests/stage2_seeds.json',list({s['url']:s for s in seeds}.values()))
    print('Baseline and source counts:',len(states),len(sources),len(portals),len(seeds))

def choose_followups():
    selected=[]; discovered=[]
    for rec in current():
        if rec['category'] not in ['state_judiciary_portal','court_directory_or_case_access','county_government','county_court_directory'] or rec['verification_status']!='retrieved':continue
        host=urlparse(rec['final_url']).netloc.lower().removeprefix('www.')
        if not re.search(r'court|judic|oscn|alacourt|isc\.idaho|jud\.ct|cockecounty',host):continue
        candidates=[]
        for a in links(rec):
            label=a['label'];url=a['url'];target=urlparse(url).netloc.lower().removeprefix('www.')
            combined=label+' '+urlparse(url).path
            if not re.search(r'judg|court|docket|case|county|counties|director|clerk',combined,re.I):continue
            if re.search(r'news|article|press.release|calendar.event|facebook|twitter|youtube|linkedin|job|career',combined,re.I):continue
            category='judge_directory' if re.search(r'judg|justices',combined,re.I) else 'court_directory' if re.search(r'county|counties|local.court|trial.court|district.court|locations|director|clerk',combined,re.I) else 'case_access' if re.search(r'docket|case|record',combined,re.I) else 'court_source'
            item=dict(jurisdiction=rec['jurisdiction'],usps=rec.get('usps',''),category=category,url=url,discovered_from=rec['final_url'],label=label,discovery_evidence_path=rec['raw_path'],verification_status='discovered_not_fetched')
            discovered.append(item)
            # Follow exact links within the source court's host. External links remain in the inventory.
            if target!=host and not target.endswith('.'+host):continue
            if category=='court_source':continue
            if re.search(r'login|register|sign.in|logout|mailto|feedback|apply',combined,re.I):continue
            score=5*bool(re.search(r'directory|all judges|find a court|court locations|court listing|county courts|courts by county|judicial districts|trial courts|county map|judges by',combined,re.I))+3*bool(re.search(r'judges|justices|case search|docket search|records search',combined,re.I))+2*bool(re.search(r'\.pdf$',url,re.I))-len(urlparse(url).path)/200
            candidates.append((score,item))
        for _,item in sorted(candidates,key=lambda x:-x[0])[:6]:selected.append(item)
    write_json(ROOT/'manifests/discovered_court_links.json',list({(s['jurisdiction'],s['url']):s for s in discovered}.values()))
    unique=list({s['url']:s for s in selected}.values())
    capped=[]
    for state in sorted(set(s['jurisdiction'] for s in unique)):
        group=[s for s in unique if s['jurisdiction']==state]
        group.sort(key=lambda s: (-3*bool(re.search(r'directory|judicial districts|court locations|county courts|trial courts|judges|justices|\.pdf$',s['label']+' '+s['url'],re.I)),len(s['url'])))
        capped.extend(group[:10])
    write_json(ROOT/'manifests/stage3_seeds.json',capped)
    print('Discovered court links',len(discovered),'selected directory followups',len(selected))

if __name__=='__main__':
    if sys.argv[1:] == ['doj']:doj()
    elif sys.argv[1:] == ['followups']:choose_followups()
