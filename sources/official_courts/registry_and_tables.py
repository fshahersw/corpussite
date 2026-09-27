"""Offline structure extraction: registry-backed domains and source table rows."""
import csv, io, json, re
from collections import Counter
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from collect import ROOT,MANIFEST,read_jsonl,write_json
from discover import current,export

def registry():
    source=next(r for r in current() if r['requested_url']=='https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv')
    data=list(csv.DictReader(io.StringIO((ROOT/source['raw_path']).read_text(encoding='utf-8-sig'))))
    counties=json.loads((ROOT/'datasets/counties_50_plus_dc.json').read_text(encoding='utf-8'))
    rows=[]
    for row in data:
        if row['Domain type']!='County':continue
        org=(row['Organization name']+' '+row.get('Suborganization name','')).lower()
        matches=[]
        for county in counties:
            if county['usps']!=row['State']:continue
            name=county['name'].lower()
            stem=re.sub(r' (county|parish|borough|census area|municipality|planning region|city and borough)$','',name)
            if re.search(r'(?<!\w)'+re.escape(stem)+r'(?!\w)',org):matches.append(county)
        rows.append(dict(domain=row['Domain name'],official_url='https://'+row['Domain name']+'/',domain_type=row['Domain type'],organization=row['Organization name'],suborganization=row.get('Suborganization name',''),city=row['City'],usps=row['State'],candidate_geoid=matches[0]['geoid'] if len(matches)==1 else '',candidate_county_name=matches[0]['name'] if len(matches)==1 else '',county_match_status='single_name_match_unreviewed' if len(matches)==1 else 'ambiguous' if matches else 'not_matched',county_match_method='same USPS and whole county-name stem in registry organization; not a confirmed court-jurisdiction match',verification_status='official_registration_verified_website_not_tested',discovered_from=source['requested_url'],discovery_evidence_path=source['raw_path']))
    export('cisa_county_government_domains',rows)
    courts=[dict(domain=r['Domain name'],official_url='https://'+r['Domain name']+'/',domain_type=r['Domain type'],organization=r['Organization name'],suborganization=r.get('Suborganization name',''),usps=r['State'],verification_status='official_registration_verified_website_not_tested',discovered_from=source['requested_url'],discovery_evidence_path=source['raw_path']) for r in data if re.search(r'court|judicia',r['Organization name']+' '+r['Domain name']+' '+r.get('Suborganization name',''),re.I)]
    export('cisa_court_government_domains',courts)
    write_json(ROOT/'reports/registry_summary.json',dict(all_registry_rows=len(data),county_domains=len(rows),court_named_domains=len(courts),unique_county_fips_candidates=len({r['candidate_geoid'] for r in rows if r['candidate_geoid']}),county_match_status=dict(Counter(r['county_match_status'] for r in rows)),registry_fetch=source['fetched_at_utc'],warning='Domain registration is authoritative; the registry does not guarantee a website is running. Name matches are candidates, not reviewed county/court mappings.'))

def table_rows():
    rows=[];tables=[]
    for rec in current():
        if rec.get('verification_status')!='retrieved' or not rec.get('raw_path','').endswith('.html'):continue
        if rec.get('category') not in ['state_judiciary_portal','judge_directory','court_directory','court_directory_or_case_access','county_court_directory']:continue
        if not re.search(r'court|judic|oscn|alacourt|isc\.idaho|jud\.ct|cockecounty',urlparse(rec.get('final_url','')).netloc):continue
        soup=BeautifulSoup((ROOT/rec['raw_path']).read_bytes(),'html.parser')
        for ti,table in enumerate(soup.find_all('table')):
            trs=table.find_all('tr')
            if len(trs)<2:continue
            matrix=[[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'],recursive=False)] for tr in trs]
            matrix=[r for r in matrix if r]
            if not matrix or max(map(len,matrix))<2:continue
            joined=' '.join(matrix[0])
            if not re.search(r'judge|justice|county|court|clerk|name|location|address|district',joined,re.I):continue
            headers=matrix[0]
            base=dict(jurisdiction=rec['jurisdiction'],category=rec['category'],source_url=rec['final_url'],source_id=rec['source_id'],evidence_path=rec['raw_path'],table_index=ti,headers=headers)
            tables.append(dict(**base,rows=matrix[1:],row_count=len(matrix)-1))
            for ri,row in enumerate(matrix[1:],start=1):
                rows.append(dict(**base,row_index=ri,values=row,extraction_note='Verbatim HTML table row; semantic labels and officer roles are not inferred.'))
    for name,data in [('directory_tables',tables),('directory_table_rows',rows)]:
        with (ROOT/'datasets'/f'{name}.jsonl').open('w',encoding='utf-8') as f:
            for row in data:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    write_json(ROOT/'reports/table_extraction_summary.json',dict(table_count=len(tables),table_rows=len(rows),states=sorted(set(r['jurisdiction'] for r in rows)),by_state=dict(Counter(r['jurisdiction'] for r in rows)),warning='Rows preserve tables from official court directory sources. Rows are not asserted to be unique judges, courts, or cases.'))

if __name__=='__main__':
    registry();table_rows()
