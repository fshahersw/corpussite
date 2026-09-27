"""Build a finite, source-bound county frontier from existing observed links."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'sources/county_litigation_seed_review_20260919'
BATCHES=['20260914T075036205099Z','20260918T163032097064Z']
EXCLUDE=re.compile(r'\b(?:election|voter|employment|recruitment|job application|recreation|basketball|tennis|volleyball|pickleball|parks?|zoning|building permit|tax assessor|commissioners?|quorum|quroum|sheriffs?|criminal investigations|vehicle|auto titles?|titles|council meeting|news|press release|birth certificate|marriage licen[sc]e)\b',re.I)


def canonical(url):
    u=urlsplit(url)
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:return None
    return urlunsplit((u.scheme,u.netloc,u.path or '/',u.query,''))


def topic(label,url):
    value=(label+' '+urlsplit(url).path).replace('_',' ').replace('-',' ')
    if EXCLUDE.search(value):return None
    for kind,pattern,priority in [
        ('local_rule',r'\blocal\s+(?:court\s+)?rules?\b|\brules? of (?:court|practice)\b',1),
        ('standing_order',r'\b(?:standing|administrative|general)\s+orders?\b',1),
        ('fee_schedule',r'\b(?:court|filing|civil|probate)\s+(?:costs?|fees?)\b|\bfee\s+schedule\b',2),
        ('filing_guidance',r'\be\s?filing\b|\bfiling\s+(?:instructions|procedures|guidance)\b',2),
        ('court_form',r'\b(?:court|civil|criminal|family|probate|juvenile)\s+forms?\b',2),
        ('court_staff',r'\b(?:court|judicial)\s+staff\b',3),
        ('court_information',r'\b(?:court|courts|circuit clerk|clerk of court|judicial)\b',3),
    ]:
        if re.search(pattern,value,re.I):return kind,priority
    return None


@lru_cache(maxsize=2048)
def observed_links(path,expected,base):
    rawpath=(ROOT/path).resolve()
    if not rawpath.is_relative_to(ROOT) or not rawpath.is_file():return frozenset()
    raw=rawpath.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:return frozenset()
    soup=BeautifulSoup(raw,'html.parser');links=set()
    for a in soup.select('a[href]'):
        try:
            url=canonical(urljoin(base,a.get('href')))
            if url:links.add(url)
        except ValueError:pass
    return frozenset(links)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    baseline={r['geoid']:r for r in json.loads((ROOT/'sources/official_courts/datasets/counties_50_plus_dc.json').read_text(encoding='utf-8'))}
    county_slug={g:re.sub(r'[^a-z0-9]+','-',r['name'].lower()).strip('-') for g,r in baseline.items()}
    records={};counts=Counter();inputs=[]
    for batch in BATCHES:
        path=ROOT/'sources/counties/local_documents_20260914/batches'/batch/'all_candidates.jsonl'
        raw=path.read_bytes();inputs.append({'path':str(path.relative_to(ROOT)).replace('\\','/'),'sha256':hashlib.sha256(raw).hexdigest()})
        for line in raw.decode('utf-8-sig').splitlines():
            if not line.strip():continue
            row=json.loads(line);counts['input_rows']+=1
            if row.get('preserved_barriers') or row.get('selection_status') not in {'selected','deferred_batch_limit_or_per_county_cap','deferred_existing_saved_or_enqueued_url'}:
                counts['preserved_exclusion_or_barrier']+=1;continue
            fips=row.get('county_geoid');geo=baseline.get(fips)
            if not geo or row.get('usps')!=geo['usps']:
                counts['invalid_geography']+=1;continue
            try:url=canonical(row['url'])
            except ValueError:url=None
            if not url:counts['invalid_url']+=1;continue
            county_path=re.search(r'/([a-z-]+-county)(?:/|$)',urlsplit(url).path.lower())
            if county_path and county_path.group(1)!=county_slug[fips] and any(county_path.group(1)==county_slug[g] and baseline[g]['usps']==geo['usps'] for g in baseline):
                counts['explicit_other_county_path_held']+=1;continue
            found=None
            for p in row.get('provenance') or []:
                label=' '.join((p.get('anchor_text') or '').split());classification=topic(label,url)
                if not classification:continue
                base=p.get('observed_link_base_url') or p.get('parent_url','')
                if urlsplit(base).hostname!=urlsplit(url).hostname:continue
                if url in observed_links(p.get('parent_raw_path',''),p.get('parent_raw_sha256',''),base):
                    found=(p,label,classification);break
            if not found:counts['no_reproduced_relevant_same_host_link']+=1;continue
            p,label,(kind,priority)=found
            if url in records:
                existing=records[url]
                if existing['state']!=geo['usps']:counts['cross_state_shared_url_held']+=1;continue
                if fips not in existing['county_geoids']:existing['county_geoids'].append(fips)
                counts['url_duplicates_combined']+=1;continue
            records[url]={'id':'county-litigation-seed:'+hashlib.sha256(url.encode()).hexdigest()[:24],
                'url':url,'source_url':url,'resource_type':kind,'state':geo['usps'],'county':geo['name'],
                'county_fips':fips,'county_geoids':[fips],'parent_url':p['parent_url'],
                'parent_capture_id':p.get('source_fetch_id'),'parent_raw_path':p['parent_raw_path'].replace('\\','/'),
                'parent_raw_sha256':p['parent_raw_sha256'],'anchor_text':label,
                'source_authority':{'class':'exact_link_on_saved_county_or_court_source','verified':False,'evidence':[{'url':p['parent_url'],'anchor':label}],'note':'Destination authority and court territory require source-content review.'},
                'association':{'status':'discovery_association_only','basis':'Existing saved county source association; exact link re-parsed and parent SHA256 verified','evidence':{'county_geoid':fips,'prior_strength':row.get('source_association_strength'),'parent_raw_sha256':p['parent_raw_sha256']}},
                'applicability':{'level':'unknown','state':geo['usps'],'county_fips':None,'status':'destination_review_required'},
                'priority':priority,'depth':0,'allowed_hosts':[urlsplit(url).hostname],
                'review_status':'exact_saved_link_reproduced_destination_unreviewed','existing_capture_suffices':False,
                'source_batch':batch}
    rows=sorted(records.values(),key=lambda r:(r['priority'],r['state'],r['county_fips'],r['url']))
    data=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode('utf-8')
    target=OUT/'seeds.jsonl';target.write_bytes(data)
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'inputs':inputs,'seeds':len(rows),
        'states':len({r['state'] for r in rows}),'county_associations':len({g for r in rows for g in r['county_geoids']}),
        'by_type':dict(Counter(r['resource_type'] for r in rows)),'selection':dict(counts),
        'sha256':hashlib.sha256(data).hexdigest(),'network_requests':0,
        'qualification':'Finite observed-link acquisition candidates. Not verified governing jurisdiction, downloaded content, or nationwide completeness.'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary))


if __name__=='__main__':main()
