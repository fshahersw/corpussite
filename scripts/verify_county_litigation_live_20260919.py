"""Verify a published county packet against the actual localhost application."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import requests

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8769'


def main():
    rows=[json.loads(x) for x in (ROOT/'sources/county_litigation_20260919/resources.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    tested=[];errors=[]
    session=requests.Session()
    def check(condition, label):
        if not condition:errors.append(label)
    listed=session.get(BASE+'/api/county-litigation',params={'include_directories':'1','limit':100},timeout=60)
    check(listed.status_code==200,'listing_http')
    listing=listed.json();check(listing.get('available') is True,'listing_available');check(listing.get('total')==len(rows),'published_count')
    candidates=[]
    for predicate in [lambda r:r.get('county_fips')=='06059' and r['resource_kind']=='local_rule',
                      lambda r:r.get('county_fips')=='01017',
                      lambda r:r.get('raw_path','').endswith('.pdf') and r.get('county_geoids') and r['resource_kind'] in ('local_rule','court_form','standing_order')]:
        item=next((r for r in rows if predicate(r)),None)
        if item and item['id'] not in [x['id'] for x in candidates]:candidates.append(item)
    if not candidates:candidates=rows[:1]
    for row in candidates:
        ident=row['id'];geoid=(row.get('county_geoids') or [row.get('county_fips')])[0]
        county=session.get(BASE+'/api/county-litigation',params={'geoid':geoid,'include_directories':1},timeout=60).json()
        check(all(geoid in r['county_geoids'] for r in county.get('items',[])),ident+':county_filter')
        state=session.get(BASE+'/api/county-litigation',params={'state':row['state'],'include_directories':1},timeout=60).json()
        check(all(r['state']==row['state'] for r in state.get('items',[])),ident+':state_filter')
        result=session.get(BASE+'/api/county-litigation-record',params={'id':ident},timeout=60)
        check(result.status_code==200,ident+':reader_http');detail=result.json()
        check(not re.search(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]',json.dumps(detail)),ident+':private_absolute_path')
        for kind,key,hashkey in [('original','original_url','sha256'),('text','text_url','text_sha256')]:
            if not detail.get(key):continue
            asset=session.get(BASE+detail[key],timeout=60)
            check(asset.status_code==200,ident+':'+kind+'_http')
            check(hashlib.sha256(asset.content).hexdigest()==row.get(hashkey),ident+':'+kind+'_sha256')
            if kind=='original' and row.get('raw_path','').endswith('.pdf'):
                check(b'%PDF-' in asset.content[:1024],ident+':pdf_bytes')
                check('attachment' in asset.headers.get('Content-Disposition',''),ident+':original_attachment')
        tested.append({'id':ident,'title':row['title'],'county_geoid':geoid,'kind':row['resource_kind']})
    missing_text=next((r for r in rows if r.get('metadata',{}).get('extraction',{}).get('status') in ('native_text_missing','text_gap') and r.get('raw_path')),None)
    if missing_text:
        response=session.get(BASE+'/api/county-litigation-record',params={'id':missing_text['id']},timeout=60)
        detail=response.json()
        check(response.status_code==200,'text_gap_reader_http')
        check(detail.get('has_original') is True,'text_gap_original_available')
        check(detail.get('has_text') is False and not detail.get('text_url'),'text_gap_not_counted_as_readable')
        tested.append({'id':missing_text['id'],'check':'original_saved_reading_copy_missing'})
    check(session.get(BASE+'/api/county-litigation-record',params={'id':'missing'},timeout=15).status_code==404,'missing_record_404')
    check(session.get(BASE+'/api/county-litigation-asset',params={'id':'../../secret','kind':'original'},timeout=15).status_code==404,'traversal_not_served')
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'status':'passed' if not errors else 'failed',
            'published_resources':len(rows),'tested':tested,'errors':errors,'main_catalog_bytes':(ROOT/'delivery/archive-directory/directory.sqlite3').stat().st_size}
    out=ROOT/'reports/county_litigation_20260919/live_verification.json';out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result));raise SystemExit(bool(errors))


if __name__=='__main__':main()
