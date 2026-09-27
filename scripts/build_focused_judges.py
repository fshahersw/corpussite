"""Build the focused judge delivery from verified saved sources and one bounded batch.

Offline only. Does not ingest, run collectors, rebuild shared indexes, or alter sources.
"""
from __future__ import annotations
import collections,csv,datetime,hashlib,json,sqlite3,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PREP=ROOT/'sources/official_judges/phase2_preparation'
OUT=ROOT/'delivery/focused_legal_corpus/judges'
LIVE=ROOT/'corpus/official_judges'
COURTS=ROOT/'sources/official_courts'

def loadl(p):return [json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def j(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def pathref(p):
    p=p.resolve()
    return {'workspace_relative_path':str(p.relative_to(ROOT)).replace('\\','/'),'absolute_path':str(p)}
def outj(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def outl(name,rows):(OUT/name).write_text(''.join(j(r)+'\n' for r in rows),encoding='utf-8')
def outcsv(name,rows,fields):
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for row in rows:
            safe={}
            for key in fields:
                value=row.get(key)
                if isinstance(value,(list,dict)):value=j(value)
                if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):value="'"+value
                safe[key]=value
            w.writerow(safe)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    reviewed=loadl(PREP/'reviewed_sources.jsonl')
    saved=[r for r in reviewed if r['decision'] in ('already_saved_roster','already_saved_profile')]
    if len(saved)!=99:raise ValueError('Expected 91 saved rosters and eight saved profiles')
    seeds=loadl(PREP/'official_judges.seeds.jsonl')
    config=json.loads((PREP/'official_judges.config.json').read_text())
    assert len(seeds)==46 and not config['follow_links'] and config['max_depth']==0 and config['max_retries']==0
    by_seed={s['url']:s for s in seeds}
    artifacts={};verified={};issues=[];sources=[];outcomes=[]
    def artifact(path,expected,kind,source_url,edition=None):
        p=ROOT/path
        if path not in verified:verified[path]=sha(p) if p.is_file() else None
        if verified[path]!=expected:raise ValueError('Artifact hash mismatch or missing: '+path)
        key=(path,expected)
        if key not in artifacts:artifacts[key]={**pathref(p),'sha256':expected,'bytes':p.stat().st_size,'artifact_kind':kind,'source_urls':[]}
        if source_url not in artifacts[key]['source_urls']:artifacts[key]['source_urls'].append(source_url)
        return {**pathref(p),'sha256':expected,'bytes':p.stat().st_size,'artifact_kind':kind}
    for r in saved:
        raws={};texts={};times=[];originals=[]
        for capture in r['saved_captures']:
            raw=artifact(capture['raw_path'],capture['raw_sha256'],'original',r['url'])
            raws[(raw['workspace_relative_path'],raw['sha256'])]=raw
            if capture.get('text_path'):
                kind='ocr_text' if '/ocr/' in capture['text_path'] else 'extracted_text'
                text=artifact(capture['text_path'],capture['text_file_sha256'],kind,r['url'])
                texts[(text['workspace_relative_path'],text['sha256'])]=text
            times.append(capture.get('retrieved_at'))
            originals.append({k:capture.get(k) for k in ('collection','source_url','final_url','capture_kind','retrieved_at','metadata_path','source_record_locator','extraction_status')})
        row={'source_id':hashlib.sha256(r['url'].encode()).hexdigest()[:24],'state':r['state'],'state_code':r['state_code'],
             'source_url':r['url'],'source_type':'individual_judge_profile' if r['decision']=='already_saved_profile' else 'judge_roster_or_directory',
             'title':r['title'],'status':'saved','edition_note':r.get('edition_note'),'capture_times':sorted({t for t in times if t}),
             'raw_artifacts':list(raws.values()),'text_artifacts':list(texts.values()),'original_capture_records':originals,
             'authority':r['authority'],'discovery_evidence':r.get('discovery_evidence'),'review_manifest':pathref(PREP/'reviewed_sources.jsonl'),
             'coverage_note':'An available source entry, not a complete judge or statewide inventory.'}
        sources.append(row);outcomes.append({'source_url':r['url'],'state':r['state'],'status':'saved_prior_to_focused_run','source_type':row['source_type']})
    resources={};run_records=[]
    if (LIVE/'corpus.sqlite3').exists():
        db=sqlite3.connect((LIVE/'corpus.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        resources={r['url']:dict(r) for r in db.execute('SELECT * FROM resources')}
        run_records=[dict(r) for r in db.execute('SELECT id,started_at,ended_at,stop_reason,processed FROM runs ORDER BY rowid')]
        db.close()
    for url,seed in by_seed.items():
        r=resources.get(url);state=seed['jurisdiction']['state'];status=r['status'] if r else 'not_attempted'
        outcome={'source_url':url,'state':state,'status':status,'source_type':'judge_roster_or_directory','attempts':r.get('attempts',0) if r else 0,
                 'http_status':r.get('last_http_status') if r else None,'error':r.get('error') if r else None,
                 'metadata_reference':pathref(LIVE/r['metadata_path']) if r and r.get('metadata_path') else None,
                 'discovery_evidence_path':seed['discovery_evidence_path'],'discovery_evidence_sha256':seed['discovery_evidence_sha256']}
        outcomes.append(outcome)
        if not r or status!='downloaded' or not r['raw_complete']:
            issues.append({**outcome,'gap_type':'bounded_acquisition_not_downloaded','resolution':'Retained as a gap at the focused-package cutoff. No retry or wider discovery requested.'});continue
        raw=artifact(str((LIVE/r['raw_path']).relative_to(ROOT)).replace('\\','/'),r['sha256'],'original',url)
        mdpath=LIVE/r['metadata_path'];md=json.loads(mdpath.read_text(encoding='utf-8'))
        texts=[]
        if r.get('text_path'):
            p=LIVE/r['text_path'];text=artifact(str(p.relative_to(ROOT)).replace('\\','/'),sha(p),'extracted_text',url);texts.append(text)
        source={'source_id':hashlib.sha256(url.encode()).hexdigest()[:24],'state':state,'state_code':seed['jurisdiction']['state_code'],
            'source_url':url,'source_type':'judge_roster_or_directory','title':r.get('title') or seed.get('label'),'status':'saved',
            'edition_note':None,'capture_times':[md.get('fetched_at')],'raw_artifacts':[raw],'text_artifacts':texts,
            'original_capture_records':[{'collection':'corpus/official_judges','source_url':url,'final_url':md.get('final_url') or url,
              'capture_kind':'direct_public_capture','retrieved_at':md.get('fetched_at'),'metadata_path':str(mdpath.relative_to(ROOT)).replace('\\','/'),
              'source_record_locator':'corpus/official_judges/corpus.sqlite3#resources/'+str(r['id']),'extraction_status':r.get('extraction_status')}],
            'authority':{'basis':'Exact URL reviewed from saved official judiciary links in phase2 preparation; no CISA inference.'},
            'discovery_evidence':{'path':seed['discovery_evidence_path'],'sha256':seed['discovery_evidence_sha256']},
            'review_manifest':pathref(PREP/'reviewed_sources.jsonl'),'coverage_note':'One focused acquisition of an observed roster entry; no profile or site expansion.'}
        if url=='https://judicial.alabama.gov/Library/Judges':
            source['edition_note']='The retrieved Alabama appellate membership tables mix service history from 1820 onward with current entries marked Present; this is not a current-only roster.'
            source['coverage_note']='Mixed historical/current membership register. No historical profile expansion was performed.'
        sources.append(source)
        if not any(t['bytes']>0 for t in texts) or r.get('extraction_status') not in ('extracted','extracted_html','extracted_pdf'):
            issues.append({'state':state,'source_url':url,'gap_type':'extraction_review','extraction_status':r.get('extraction_status'),'note':'Original preserved; extraction status retained without new OCR or profile expansion.'})
        if url=='https://www.supremecourt.ohio.gov/JudgeSearch/' and not any(t['bytes']>0 for t in texts):
            source['coverage_note']='The judge-search interface was saved, but its extracted text is empty; no judge roster contents are claimed from this capture.'
    for r in reviewed:
        if r['decision'] not in ('already_saved_roster','already_saved_profile','eligible_unfetched','observed_redirect','duplicate_candidate_alias'):
            issues.append({'state':r['state'],'source_url':r['url'],'gap_type':'preexisting_source_constraint','status':r['decision'],'reason':r['decision_reason'],
              'edition_note':r.get('edition_note'),'discovery_evidence':r.get('discovery_evidence'),
              'saved_attempts':r['saved_attempts'],'host_block':r.get('host_block'),'robots_constraints':r.get('robots_constraints')})
    for source in sources:
        if source.get('edition_note'):
            issues.append({'state':source['state'],'source_url':source['source_url'],'gap_type':'edition_currency_not_verified','note':source['edition_note']})
    selected_urls={s['source_url'] for s in sources}
    raw_urls={c['source_url'] for s in sources for c in s['original_capture_records']}
    accepted_urls=selected_urls|raw_urls
    # Preserve existing deterministic structured outputs. No NER or unique-person claims.
    tables=[r for r in loadl(COURTS/'datasets/directory_tables.jsonl') if r['source_url'] in accepted_urls]
    table_rows=[r for r in loadl(COURTS/'datasets/directory_table_rows.jsonl') if r['source_url'] in accepted_urls]
    sheet_rows=[r for r in loadl(COURTS/'datasets/spreadsheet_rows.jsonl') if r['source_url'] in accepted_urls]
    assignments=[r for r in loadl(COURTS/'datasets/texas_local_administrative_judge_assignments.jsonl') if r['source_url'] in accepted_urls]
    for family,rows,source_file in [('table',tables,'directory_tables.jsonl'),('table_row',table_rows,'directory_table_rows.jsonl'),('spreadsheet_row',sheet_rows,'spreadsheet_rows.jsonl'),('judge_assignment',assignments,'texas_local_administrative_judge_assignments.jsonl')]:
        dataset_ref={**pathref(COURTS/'datasets'/source_file),'sha256':sha(COURTS/'datasets'/source_file)}
        for r in rows:
            r['package_record_type']=family;r['original_dataset_reference']=dataset_ref
            r['original_evidence_reference']=pathref(COURTS/r['evidence_path'])
    name_groups=collections.defaultdict(list)
    for r in assignments:name_groups[(r['state'],r['name'])].append(r)
    names=[{'state':state,'name':name,'record_type':'observed_name_string','assignment_count':len(rows),
       'source_urls':sorted({r['source_url'] for r in rows}),'assignments':[{'county_name':r['county_name'],'county_geoid':r['county_geoid'],'court':r['court'],'source_url':r['source_url'],'worksheet_xml':r['worksheet_xml'],'row_number':r['row_number']} for r in rows],
       'identity_note':'Grouped existing name strings; not verified unique people.'} for (state,name),rows in sorted(name_groups.items())]
    state_rows=[]
    for state_row in json.loads((PREP/'jurisdiction_coverage.json').read_text()):
        state=state_row['state'];saved_state=[s for s in sources if s['state']==state];pending=[o for o in outcomes if o['state']==state and o['status'] not in ('saved_prior_to_focused_run','downloaded')]
        row={'state':state,'state_code':state_row['state_code'],'saved_roster_sources':sum(s['source_type']=='judge_roster_or_directory' for s in saved_state),
          'saved_profile_sources':sum(s['source_type']=='individual_judge_profile' for s in saved_state),
          'sources_with_text':sum(any(t['bytes']>0 for t in s['text_artifacts']) for s in saved_state),'focused_batch_gaps':len(pending),
          'recorded_gaps':sum(i['state']==state for i in issues),'existing_structured_assignments':sum(r['state']==state for r in assignments),
          'distinct_existing_name_strings':sum(r['state']==state for r in names),
          'state_portal_url':state_row['state_portal_url'],'portal_status_from_saved_evidence':state_row['saved_portal_status'],
          'judge_inventory_complete':False,'note':'Source-level coverage only. Named rows are not an exhaustive or entity-deduplicated judge roster.'}
        if not row['saved_roster_sources']:
            issues.append({'state':state,'source_url':state_row['state_portal_url'],'gap_type':'state_roster_not_established','note':'No saved roster entry established in this focused package; saved individual profiles, if any, are partial.'})
            row['recorded_gaps']+=1
        state_rows.append(row)
    # Derivative schemas are readable CSV plus lossless JSONL; source files stay intact.
    for s in sources:
        representative=s['raw_artifacts'][0]
        s['raw_path']=representative['workspace_relative_path'];s['raw_sha256']=representative['sha256']
        s['text_path']=s['text_artifacts'][0]['workspace_relative_path'] if s['text_artifacts'] else None
        s['text_sha256']=s['text_artifacts'][0]['sha256'] if s['text_artifacts'] else None
    outl('sources.jsonl',sources)
    flat=[]
    for s in sources:
        flat.append({**s,'raw_path':s['raw_artifacts'][0]['absolute_path'],'raw_sha256':s['raw_artifacts'][0]['sha256'],
            'text_paths':[a['absolute_path'] for a in s['text_artifacts']],'text_sha256':[a['sha256'] for a in s['text_artifacts']]})
    outcsv('sources.csv',flat,['source_id','state','state_code','source_type','source_url','title','status','edition_note','capture_times','raw_path','raw_sha256','text_paths','text_sha256','raw_artifacts','text_artifacts'])
    outl('artifacts.jsonl',list(artifacts.values()));outcsv('artifacts.csv',list(artifacts.values()),['workspace_relative_path','absolute_path','sha256','bytes','artifact_kind','source_urls'])
    outl('source_outcomes.jsonl',outcomes);outcsv('source_outcomes.csv',outcomes,['state','source_url','source_type','status','attempts','http_status','error','metadata_reference'])
    outl('gaps.jsonl',issues);outcsv('gaps.csv',issues,['state','source_url','gap_type','status','reason','note','error','http_status','extraction_status'])
    outl('tables.jsonl',tables);outl('table_rows.jsonl',table_rows);outcsv('table_rows.csv',table_rows,['jurisdiction','source_url','table_index','row_index','headers','values','original_evidence_reference','original_dataset_reference'])
    outl('spreadsheet_rows.jsonl',sheet_rows);outcsv('spreadsheet_rows.csv',sheet_rows,['jurisdiction','source_url','worksheet_xml','row_number','cells','original_evidence_reference'])
    outl('judge_assignments.jsonl',assignments);outcsv('judge_assignments.csv',assignments,['state','usps','county_name','county_geoid','court','name','first','middle','last','suffix','source_url','worksheet_xml','row_number','source_fields','original_evidence_reference'])
    outl('names.jsonl',names);outcsv('names.csv',names,['state','name','record_type','assignment_count','source_urls','assignments','identity_note'])
    outj('state_coverage.json',state_rows);outcsv('state_coverage.csv',state_rows,list(state_rows[0]))
    summary={'built_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'package_schema_version':'1.0.0',
        'source_records':len(sources),'saved_roster_sources':sum(s['source_type']=='judge_roster_or_directory' for s in sources),
        'saved_profile_sources':sum(s['source_type']=='individual_judge_profile' for s in sources),'original_99_preserved':len(saved),
        'new_focused_downloads':sum(r['status']=='downloaded' for r in outcomes if r['source_url'] in by_seed),
        'focused_seed_urls':len(seeds),'focused_outcomes':dict(collections.Counter(o['status'] for o in outcomes if o['source_url'] in by_seed)),
        'focused_runs':run_records,'unique_original_payloads':len({a['sha256'] for a in artifacts.values() if a['artifact_kind']=='original'}),
        'artifact_files':len(artifacts),'verified_artifact_hashes':len(verified),'hash_failures':0,
        'preserved_tables':len(tables),'preserved_table_rows':len(table_rows),'preserved_spreadsheet_rows':len(sheet_rows),
        'existing_structured_judge_assignments':len(assignments),'distinct_existing_name_strings':len(names),
        'states_and_dc':len(state_rows),'jurisdictions_with_roster_sources':sum(r['saved_roster_sources']>0 for r in state_rows),
        'gap_records':len(issues),'full_national_judge_inventory_complete':False,'original_content_modified':False,
        'network_requests_by_package_builder':0,'notes':['Raw and text artifacts are referenced at original workspace paths; no source payloads are silently copied or rewritten.',
          'Structured names/assignments preserve the existing Texas extraction. Other roster names remain in source text and verbatim tables.',
          'Focused acquisition runs once for at most 46 resource attempts and 300 seconds; unprocessed or unavailable sources remain explicit gaps.']}
    outj('summary.json',summary)
    recipe={'source_family':'official_judges_phase2','collection_root':str(LIVE),'seed_count':46,
       'seeds':{**pathref(PREP/'official_judges.seeds.jsonl'),'sha256':sha(PREP/'official_judges.seeds.jsonl')},
       'config':{**pathref(PREP/'official_judges.config.json'),'sha256':sha(PREP/'official_judges.config.json')},
       'max_pages':46,'max_seconds':300,'follow_links':False,'max_depth':0,'max_retries':0,
       'ingest_command':'python pipeline/corpus_crawler.py --root corpus/official_judges ingest --seeds sources/official_judges/phase2_preparation/official_judges.seeds.jsonl --config sources/official_judges/phase2_preparation/official_judges.config.json',
       'run_command':'python pipeline/corpus_crawler.py --root corpus/official_judges run --max-pages 46 --max-seconds 300',
       'repeat_run_authorized':False,'authorization':'Root confirmed law batch drained at 2026-09-13T19:55:42Z; one bounded judge pass authorized.'}
    outj('acquisition_recipe.json',recipe)
    schema={'sources':'One source URL with state/type, capture and edition provenance, verified original/text artifact arrays and source metadata references.',
       'artifacts':'Unique local artifact paths, SHA-256, byte sizes, original/extracted/OCR kind, and source associations.',
       'source_outcomes':'Original 99 saved sources plus the exact 46 focused candidates and their actual queue outcomes.',
       'gaps':'Preexisting source restrictions, focused non-downloads, extraction/edition limitations and states without a saved roster.',
       'tables_and_table_rows':'Existing verbatim HTML table structures and rows; no inferred roles or entity joins.',
       'spreadsheet_rows':'Existing worksheet cell coordinates and values with original XLSX provenance.',
       'judge_assignments':'Existing deterministic Texas local administrative judge/county/court assignments.',
       'names':'Distinct existing state/name strings with assignment references; not verified unique-person identities.',
       'state_coverage':'All 50 states plus DC; available source counts and explicit completeness=false.',
       'csv':'UTF-8 BOM, nested fields serialized as JSON, leading formula characters escaped. JSONL preserves original values.'}
    outj('schema.json',schema)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
