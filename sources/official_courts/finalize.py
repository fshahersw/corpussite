"""Audit saved responses and create reviewable, measured collection manifests."""
import hashlib,json,re,csv,io,zipfile
from collections import Counter,defaultdict
from urllib.parse import urljoin,urlparse,urldefrag
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from collect import ROOT,MANIFEST,read_jsonl,write_json,now,relative,append_jsonl
from discover import current,export

def jsonl(name,rows):
    with (ROOT/name).open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')

def audit():
    sources=[]
    originals={r['requested_url']:r for r in current()}
    for rec in originals.values():
        rec=dict(rec);rec['assessment_at_utc']=now()
        rec['official_url']=rec.get('final_url',rec['requested_url'])
        if rec.get('raw_path'):
            raw=ROOT/rec['raw_path'];body=raw.read_bytes()
            rec['sha256_verified']=hashlib.sha256(body).hexdigest()==rec['sha256']
            if rec['http_status']==200 and raw.suffix=='.html':
                # Early Nebraska templates contained malformed links that interrupted extraction.
                if rec.get('verification_status')=='transport_error':
                    soup=BeautifulSoup(body,'html.parser');out=[];seen=set()
                    for a in soup.find_all('a',href=True):
                        try:u=urldefrag(urljoin(rec['final_url'],a['href']))[0];p=urlparse(u)
                        except ValueError:continue
                        if p.scheme not in ('https','http') or not p.netloc:continue
                        label=a.get_text(' ',strip=True)
                        if (u,label) in seen:continue
                        seen.add((u,label));out.append(dict(url=u,label=label,discovered_from=rec['final_url'],source_id=rec['source_id']))
                    lp=ROOT/'links'/f"{rec['source_id']}.json";write_json(lp,out)
                    rec.update(original_fetch_status=rec['verification_status'],verification_status='retrieved',links_path=relative(lp),link_count=len(out),extraction_repaired_offline=True)
                text=(ROOT/rec['text_path']).read_text(encoding='utf-8') if rec.get('text_path') else ''
                if re.search(r'verify you are human|validate your browser|checking your browser|request rejected|enable javascript and cookies to continue',text[:3000],re.I) or re.search(r'^(Access Denied|Just a moment|Attention Required)',rec.get('title',''),re.I):rec['verification_status']='challenge_page'
                elif re.search(r'page not found|404.*not found|not found.*404',rec.get('title',''),re.I):rec['verification_status']='soft_http_error'
                elif len(text)<100 and len(body)>1000:rec['verification_status']='javascript_required'
                rec['extracted_text_characters']=len(text)
            elif raw.suffix=='.pdf' and rec.get('text_path'):
                text=(ROOT/rec['text_path']).read_text(encoding='utf-8');rec['extracted_text_characters']=len(text)
                rec['pdf_text_status']='text_extracted' if len(text.strip())>=100 else 'needs_ocr_or_text_unavailable'
        host=urlparse(rec['official_url']).netloc.lower()
        rec['source_authority']='official_government_domain' if host.endswith(('.gov','.us')) else 'official_government_published_registry' if rec['category']=='official_domain_registry_download' and 'cisagov' in rec['requested_url'] else 'official_court_site_linked_from_government' if any(d in host for d in ['oscn.net','vermontjudiciary.org','cockecircuit.com','cookcountyclerkofcourt.org']) else 'institutional_reference_not_government_verified'
        sources.append(rec)
    jsonl('manifests/sources.jsonl',sources)
    # Persist corrected extraction metadata for reruns without another network request.
    for rec in sources:
        old=originals[rec['requested_url']]
        if old['verification_status']!=rec['verification_status'] or rec.get('extraction_repaired_offline'):
            rec['event_type']='offline_quality_assessment';append_jsonl(MANIFEST,rec)
    return sources

def spreadsheets(sources):
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    result=[]
    for rec in sources:
        if not rec.get('raw_path','').endswith('.xlsx') or rec['verification_status']!='retrieved':continue
        with zipfile.ZipFile(ROOT/rec['raw_path']) as z:
            shared=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                shared=[''.join(el.itertext()) for el in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)]
            for name in z.namelist():
                if not re.match(r'xl/worksheets/sheet\d+\.xml$',name):continue
                for row in ET.fromstring(z.read(name)).findall('.//m:sheetData/m:row',ns):
                    cells={}
                    for c in row.findall('m:c',ns):
                        v=c.find('m:v',ns);value=v.text if v is not None else ''
                        if c.get('t')=='s' and value:value=shared[int(value)]
                        elif c.get('t')=='inlineStr':value=''.join(c.find('m:is',ns).itertext())
                        if value:cells[c.get('r','')]=value
                    if cells:result.append(dict(jurisdiction=rec['jurisdiction'],source_url=rec['official_url'],source_id=rec['source_id'],evidence_path=rec['raw_path'],worksheet_xml=name,row_number=row.get('r'),cells=cells))
    jsonl('datasets/spreadsheet_rows.jsonl',result)
    texas_counties={r['name'].removesuffix(' County').casefold():r['geoid'] for r in json.loads((ROOT/'datasets/counties_50_plus_dc.json').read_text()) if r['usps']=='TX'}
    grouped=defaultdict(list)
    for row in result:grouped[(row['source_id'],row['worksheet_xml'])].append(row)
    assignments=[]
    for group in grouped.values():
        first=group[0];headers={re.sub(r'\d','',cell):value for cell,value in first['cells'].items()}
        if not {'First','Last','County','Court'}.issubset(set(headers.values())):continue
        for row in group[1:]:
            fields={headers.get(re.sub(r'\d','',cell),cell):value for cell,value in row['cells'].items()}
            if not fields.get('County') or not fields.get('Last'):continue
            assignments.append(dict(state='Texas',usps='TX',county_name=fields['County'],county_geoid=texas_counties.get(fields['County'].casefold(),''),court=fields.get('Court',''),name=' '.join(fields[k] for k in ['First','Middle','Last','Suffix'] if fields.get(k)),first=fields.get('First',''),middle=fields.get('Middle',''),last=fields.get('Last',''),suffix=fields.get('Suffix',''),source_fields=fields,source_url=row['source_url'],evidence_path=row['evidence_path'],worksheet_xml=row['worksheet_xml'],row_number=row['row_number'],record_type='local_administrative_judge_county_assignment'))
    jsonl('datasets/texas_local_administrative_judge_assignments.jsonl',assignments)
    write_json(ROOT/'reports/texas_judge_extraction_summary.json',dict(assignments=len(assignments),distinct_name_strings=len(set(a['name'] for a in assignments)),assignments_with_county_geoid=sum(bool(a['county_geoid']) for a in assignments),note='Assignments can repeat one judge across multiple counties. Names are extracted from source First/Middle/Last/Suffix columns.'))
    return len(result)

def source_map(sources):
    fetched={r['requested_url']:r for r in sources};out={}
    for rec in sources:
        if rec['verification_status']!='retrieved' or not rec.get('links_path'):continue
        if rec['category'].startswith(('census','doj')) or rec['jurisdiction']=='USA':continue
        for a in json.loads((ROOT/rec['links_path']).read_text(encoding='utf-8')):
            u=a['url'];label=a['label'];q=label+' '+urlparse(u).path
            if not re.search(r'court|judg|justice|case|docket|county|counties|clerk|rule|motion|record|statut|code|constitution',q,re.I):continue
            if re.search(r'facebook|twitter|instagram|linkedin|youtube|vimeo|google\.com/maps',u,re.I):continue
            key=(rec['jurisdiction'],u)
            if key in out:continue
            category='document' if re.search(r'\.(pdf|xlsx?|docx?|csv|zip)(?:$|\?)',u,re.I) else 'judge_profile_or_directory' if re.search(r'judge|justice|judicial directory',q,re.I) else 'court_or_county_directory' if re.search(r'county|counties|director|locations|district.court|trial.court',q,re.I) else 'docket_or_case_access' if re.search(r'case|docket|record',q,re.I) else 'court_rules' if re.search(r'rule',q,re.I) else 'court_related'
            target=fetched.get(u,{})
            out[key]=dict(jurisdiction=rec['jurisdiction'],category=category,official_url=u,label=label,discovered_from=rec['official_url'],discovery_evidence_path=rec['raw_path'],verification_status=target.get('verification_status','discovered_not_fetched'),evidence_path=target.get('raw_path',''),official_status='linked_source; external destination requires independent classification')
    rows=list(out.values());jsonl('manifests/source_map.jsonl',rows)
    frontier=[r for r in rows if r['verification_status']=='discovered_not_fetched']
    jsonl('manifests/remaining_frontier.jsonl',frontier)
    return rows

def reports(sources,links_count,sheet_count):
    p=json.loads((ROOT/'datasets/state_judiciary_portals.json').read_text(encoding='utf-8'))
    fetched={r['requested_url']:r for r in sources};corrections={r['jurisdiction']:r for r in sources if r['category']=='corrected_state_judiciary_portal' and r['verification_status']=='retrieved'}
    portals=[];coverage=[]
    counties=json.loads((ROOT/'datasets/counties_50_plus_dc.json').read_text(encoding='utf-8'))
    county_count=Counter(r['state'] for r in counties)
    for row in p:
        rec=corrections.get(row['state']) or fetched.get(row['official_url'],{})
        portals.append(dict(**{k:v for k,v in row.items() if k not in ['verification_status','official_url']},official_url=rec.get('official_url',row['official_url']),original_directory_url=row['official_url'],verification_status=rec.get('verification_status','not_fetched'),http_status=rec.get('http_status'),evidence_path=rec.get('raw_path'),text_path=rec.get('text_path'),title=rec.get('title'),fetched_at_utc=rec.get('fetched_at_utc')))
        local=[r for r in sources if r['jurisdiction'].casefold()==row['state'].casefold()]
        coverage.append(dict(state=row['state'],usps=row['usps'],county_equivalents=county_count[row['state']],portal_status=rec.get('verification_status','not_fetched'),retrieved_sources=sum(r['verification_status']=='retrieved' for r in local),downloaded_pdfs=sum(r['verification_status']=='retrieved' and r.get('raw_path','').endswith('.pdf') for r in local),blocked_or_failed_sources=sum(r['verification_status']!='retrieved' for r in local)))
    export('state_judiciary_portals_verified',portals);export('jurisdiction_coverage',coverage)
    gaps=[r for r in sources if r['verification_status']!='retrieved'];jsonl('manifests/access_and_extraction_gaps.jsonl',gaps)
    docs=[r for r in sources if r['verification_status']=='retrieved' and r.get('raw_path','').endswith(('.pdf','.xlsx'))]
    ocr_rows=read_jsonl(ROOT/'ocr/pdf_ocr_manifest.jsonl') if (ROOT/'ocr/pdf_ocr_manifest.jsonl').exists() else []
    ocr_by_hash={r['source_pdf_sha256']:r for r in ocr_rows}
    for rec in docs:
        ocr=ocr_by_hash.get(rec['sha256'])
        if ocr:
            rec.update(ocr_status=ocr['ocr_status'],ocr_pages_completed=ocr['ocr_pages_completed'],ocr_text_path=ocr['ocr_text_path'],ocr_text_sha256=ocr['ocr_text_sha256'])
    jsonl('manifests/downloaded_documents.jsonl',docs)
    registry=json.loads((ROOT/'reports/registry_summary.json').read_text())
    summary=dict(generated_at_utc=now(),corpus_complete=False,completed_scope='Nationwide county baseline and state judiciary entry-point inventory, plus bounded public court-directory acquisition',states_plus_dc=len(portals),county_equivalents=len(counties),state_portal_status=dict(Counter(r['verification_status'] for r in portals)),distinct_urls_attempted=len(sources),source_status=dict(Counter(r['verification_status'] for r in sources)),retrieved_sources=sum(r['verification_status']=='retrieved' for r in sources),raw_bytes=sum(r.get('bytes',0) for r in sources),raw_files=len(list((ROOT/'raw').iterdir())),pdfs=len([r for r in docs if r['raw_path'].endswith('.pdf')]),xlsx=len([r for r in docs if r['raw_path'].endswith('.xlsx')]),pdf_pages=sum(r.get('pdf_pages',0) for r in docs),pdfs_needing_ocr=sum(r.get('pdf_text_status')=='needs_ocr_or_text_unavailable' for r in docs),spreadsheet_rows=sheet_count,source_map_links=links_count,sha256_failures=sum(r.get('sha256_verified') is False for r in sources),registry=registry,limitations=['The full all-cases/case-files corpus is not downloaded or exhausted.','County-equivalent baseline is Census geography, not proof of court jurisdiction; Connecticut uses planning regions.','The source-map frontier retains discovered but unfetched URLs.','JavaScript pages, access barriers, stale links, and transport failures are explicit gaps.','County-domain name matches are unreviewed candidates; a registered domain need not host a website.','Scanned PDFs with insufficient embedded text need OCR. No OCR was performed in this collection.','Historic documents retain their source dates; no current-law conclusion is inferred from their availability.'])
    summary['pdfs_with_sparse_embedded_text']=summary['pdfs_needing_ocr']
    summary['pdfs_needing_ocr']=sum(r.get('pdf_text_status')=='needs_ocr_or_text_unavailable' and r.get('ocr_status')!='complete' for r in docs)
    if (ROOT/'ocr/status.json').exists():summary['ocr']=json.loads((ROOT/'ocr/status.json').read_text())
    summary['limitations']=[item for item in summary['limitations'] if not item.startswith('Scanned PDFs with insufficient embedded text')]
    summary['limitations'].append('OCR output and page confidence are recorded separately from embedded text. OCR completion does not guarantee transcription accuracy or detect every partially scanned document.')
    if (ROOT/'reports/table_extraction_summary.json').exists():
        summary['table_extraction']=json.loads((ROOT/'reports/table_extraction_summary.json').read_text())
    summary['texas_judge_assignments']=json.loads((ROOT/'reports/texas_judge_extraction_summary.json').read_text())
    write_json(ROOT/'reports/collection_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    md=f'''# Official court source collection

This collection contains a complete 2026 Census geography baseline for the 50 states and DC, an evidence-backed state judiciary entry-point inventory, and saved public court directories and documents. **It is not the complete state/county case-file corpus.**

Captured {summary['retrieved_sources']} retrieved sources from {summary['distinct_urls_attempted']} distinct URLs attempted, including {summary['pdfs']} PDFs ({summary['pdf_pages']} pages) and {summary['xlsx']} XLSX workbooks. Original responses total {summary['raw_bytes']:,} bytes. All retained raw-file SHA-256 checks were checked; failures: {summary['sha256_failures']}.

- `datasets/counties_50_plus_dc.csv` / `.json`: {len(counties):,} counties and county equivalents; 2026 Census source ZIP and 2025 comparison ZIP retained.
- `datasets/state_judiciary_portals_verified.csv` / `.json`: 51 portals with request outcomes and evidence paths.
- `datasets/cisa_county_government_domains.csv`: {registry['county_domains']:,} official county .gov registrations; county-name matches explicitly remain unreviewed candidates.
- `datasets/directory_tables.jsonl` and `directory_table_rows.jsonl`: verbatim table structures and rows, not deduplicated judge or court entities.
- `datasets/spreadsheet_rows.jsonl`: {sheet_count:,} source XLSX rows with cell coordinates.
- `datasets/texas_local_administrative_judge_assignments.jsonl`: structured county/court assignments from the Texas local administrative judge workbooks; repeated judges across counties remain separate assignments.
- `manifests/sources.jsonl`: latest assessed response metadata, source URLs, hashes, and raw/text paths.
- `manifests/source_map.jsonl` and `remaining_frontier.jsonl`: observed links and unfetched work.
- `manifests/downloaded_documents.jsonl`: original PDF/XLSX document inventory.
- `manifests/access_and_extraction_gaps.jsonl`: barriers, JavaScript shells, stale URLs, and failures.
- `reports/collection_summary.json` and `datasets/jurisdiction_coverage.csv`: measured scope and per-state coverage.

Cocke County government pages and accessible clerk-linked dockets/local-rules PDFs are retained with `jurisdiction: Tennessee/Cocke County`. Current judge-name assertions should refer to the saved government page and retrieval timestamp. Source files can be historic and do not establish that a rule remains in force.

## Provenance and limits

The bootstrap path is USAGov → DOJ state resources → court or government website. Third-party destinations linked by DOJ are labeled institutional references; they are not treated as verified government sources. HTTP 200 is assessed separately from JavaScript challenges and empty application shells. No private authentication, CAPTCHA bypass, paid record purchase, or TLS verification disable is implemented in the collector. URLs and text inside sources are data, not operating instructions.

The CISA domain registry confirms government registration, not live website availability. Census geography does not always match court organization: county equivalents include independent cities, boroughs, and Connecticut planning regions. The county-domain mappings use name matching and require review before treating them as court jurisdiction assignments.

Scanned PDFs with fewer than 100 extracted characters are flagged for OCR. The initial OCR batch completed {summary.get('ocr',{}).get('completed_pages',0)} pages across {summary.get('ocr',{}).get('documents_complete',0)} PDFs; page text, images, model provenance and confidence are under `ocr/`. {summary['pdfs_needing_ocr']} initially flagged documents still need OCR. A small amount of extractable text can coexist with scanned pages, so the absence of an OCR flag does not prove every PDF page was text-extracted. New continuation PDFs are audited separately under `corpus/official_courts/ocr`.

## Reproduction

Run `python collect.py bootstrap`, `python discover.py doj`, then `python collect.py manifests/stage2_seeds.json`. Review the observed-link source map before scheduling further acquisition. `collect.py` resumes from recorded responses and does not automatically retry access barriers. `finalize.py` validates retained files and generates the measured manifests; `registry_and_tables.py` performs offline structure extraction. Scripts use public requests, a per-host interval, and the Windows system certificate store.
'''
    (ROOT/'README.md').write_text(md,encoding='utf-8')

if __name__=='__main__':
    sources=audit();sheets=spreadsheets(sources);mapped=source_map(sources);reports(sources,len(mapped),sheets)
