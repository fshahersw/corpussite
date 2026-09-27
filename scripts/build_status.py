"""Publish a measured, offline status snapshot across independently resumable collections."""
import datetime,json,pathlib,shutil,sqlite3
ROOT=pathlib.Path(__file__).resolve().parents[1]
def read(relative,default=None):
    try:return json.loads((ROOT/relative).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {} if default is None else default

def document_derivative_snapshot(directory):
    records=[]
    manifest=ROOT/directory/'manifest.jsonl'
    if manifest.exists():
        for line in manifest.read_text(encoding='utf-8-sig').splitlines():
            if line.strip():records.append(json.loads(line))
    completed=[item for item in records if item.get('status')=='extracted']
    return {'summary':read(str(pathlib.Path(directory)/'summary.json')),
            'manifest_records':len(records),'extracted_records':len(completed),
            'extracted_text_bytes':sum(item.get('text_bytes',0) for item in completed),
            'manifest':str(manifest)}

def browser_law_snapshot():
    manifest=ROOT/'corpus/trellis_browser_laws/manifest.jsonl'
    records=[json.loads(line) for line in manifest.read_text(encoding='utf-8').splitlines() if line.strip()] if manifest.exists() else []
    saved={r['source_url'] for r in records if r.get('status')=='captured'}
    browser_known=saved | {link['url'] for r in records for link in r.get('observed_law_links',[])}
    frontier=ROOT/'sources/trellis/worker/frontier.sqlite3'
    with sqlite3.connect(frontier.resolve().as_uri()+'?mode=ro',uri=True) as db:
        queued={url:status for url,status in db.execute('SELECT url,status FROM frontier WHERE in_scope=1')}
    provider_saved={url for url,status in queued.items() if status=='downloaded'}
    combined_saved=provider_saved | saved
    combined_known=set(queued) | browser_known
    browser_access=read('corpus/trellis_browser_laws/browser_access.json')
    latest_reconciliation=browser_access.get('latest_reconciliation_path')
    return {'capture_records':len(records),'captured_urls':len(saved),
            'law_text_urls':len({r['source_url'] for r in records if r.get('content_kind')=='law_text'}),
            'law_directory_urls':len({r['source_url'] for r in records if r.get('content_kind')=='law_directory'}),
            'text_bytes_per_capture':sum(r.get('text_bytes',0) for r in records),
            'provider_overlap_urls':len(provider_saved & saved),
            'combined_scoped_known_urls':len(combined_known),'combined_saved_urls':len(combined_saved),
            'combined_scoped_percent':round(100*len(combined_saved)/len(combined_known),2) if combined_known else None,
            'browser_access':browser_access,
            'article_2_reconciliation':read('corpus/trellis_browser_laws/article_2_reconciliation.json'),
            'latest_batch_reconciliation':read('corpus/trellis_browser_laws/'+latest_reconciliation) if latest_reconciliation else {},
            'provider_queue_reconciliation':read('corpus/trellis_browser_laws/provider_queue_reconciliation/latest_report.json'),
            'manifest':str(manifest),'full_corpus_complete':False}

def generic_snapshot(folder):
    path=folder/'corpus.sqlite3'
    if not path.exists():return {}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        statuses=dict(db.execute('SELECT status,count(*) FROM resources GROUP BY status'))
        counts=db.execute("SELECT count(*),coalesce(sum(r.byte_count),0),count(DISTINCT r.sha256) FROM resources r WHERE r.status='downloaded'").fetchone()
        pdf=db.execute("SELECT count(*),coalesce(sum(json_extract(f.response_json,'$.page_count')),0) FROM resources r JOIN fetches f ON r.last_fetch_id=f.id WHERE r.status='downloaded' AND json_extract(f.response_json,'$.detected_type')='pdf'").fetchone()
        snapshot={'snapshot_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'statuses':statuses,'downloaded_resources':counts[0],'downloaded_bytes_per_url':counts[1],'distinct_downloaded_payloads':counts[2],'pdfs':pdf[0],'pdf_pages':pdf[1],
                  'fetch_attempts':db.execute('SELECT count(*) FROM fetches').fetchone()[0],
                  'link_observations':db.execute('SELECT count(*) FROM links').fetchone()[0],
                  'extraction_statuses':dict(db.execute('SELECT extraction_status,count(*) FROM resources GROUP BY extraction_status')),
                  'host_pauses':list(db.execute("SELECT host,pause_reason FROM hosts WHERE pause_reason IS NOT NULL AND pause_reason<>''")),
                  'full_requested_corpus_complete':False}
    return snapshot

def trellis_progress(browser_laws=None):
    initial={row['url'].rstrip('/') for row in read('sources/trellis/discovery/county_urls.json',[]) if row.get('url')}
    saved=set()
    for path in (ROOT/'sources/trellis/counties').glob('*.firecrawl.json'):
        item=read(path)
        data=item.get('data',item)
        metadata=data.get('metadata',{})
        url=item.get('url') or data.get('url') or metadata.get('sourceURL') or metadata.get('url')
        if url:saved.add(url.rstrip('/'))
    worker=read('sources/trellis/worker/status.json')
    counts=worker.get('counts',{})
    total=sum(counts.values())
    captured=len(initial & saved)
    combined=browser_laws or {}
    return {'snapshot_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'initial_county_batch':{'saved':captured,'total':len(initial),'percent':round(100*captured/len(initial),2) if initial else None},
            'currently_discovered_public_urls':{'saved':combined.get('combined_saved_urls',counts.get('downloaded',0)),'total':combined.get('combined_scoped_known_urls',total),'percent':combined.get('combined_scoped_percent',round(100*counts.get('downloaded',0)/total,2) if total else None),'worker_snapshot_at':worker.get('updated_at'),'includes_separate_browser_law_captures':bool(combined)},
            'provider_queue_snapshot':{'saved':counts.get('downloaded',0),'total':total,'snapshot_at':worker.get('updated_at')},
            'scope_version':worker.get('scope_version'),
            'active_phase':worker.get('active_phase'),
            'excluded_urls_preserved':sum(worker.get('archived_out_of_scope_counts',{}).values()),
            'full_trellis_corpus_percent':None,
            'denominator_note':'The initial county batch is finite. The scoped law/judge/county URL queue grows as relevant links are found. Neither denominator proves complete nationwide coverage. Case/file/general URL queues are excluded by the latest user instruction.'}
def credit_observation(record,source,kind):
    value=record.get('response',{}).get('data',{}).get('remainingCredits') if record.get('api_status')==200 else None
    if isinstance(value,bool) or not isinstance(value,(int,float)):value=None
    return {'remaining_credits_at_observation':value,'checked_at':record.get('checked_at'),
            'api_status':record.get('api_status'),'source':source,'observation_kind':kind,'is_live_balance':False}

def observation_time(record):
    try:
        value=datetime.datetime.fromisoformat(record['checked_at'].replace('Z','+00:00'))
        return value.replace(tzinfo=datetime.timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
    except (KeyError,TypeError,ValueError,AttributeError):return None

def apply_county_law_resume(status,active,progress,credit,final_credit=None):
    """A newer resume report takes precedence over the old delivery cutoff."""
    state=active.get('status','resume_status_unavailable')
    status['collection_state']=state
    status['active_scope']=['selected county profiles and bounded official county website entries','selected remaining state-law URLs']
    status['excluded_from_active_scope']=list(dict.fromkeys([*status['excluded_from_active_scope'],'judges']))
    status['judge_acquisition']=False
    status['saved_judges_role']='prior saved package; no new judge acquisition'
    status['automation']={'active':False,'recorded_context':active.get('automation','none')}
    status['focused_package_role']='refreshed_delivery' if state=='delivered' else 'previous_completed_delivery_snapshot'
    status['focused_package_scope_superseded_by']='reports/active_county_law_resume.json'
    status['focused_package_credit_checkpoint_role']='historical_previous_delivery_only'
    status['previous_completed_delivery_snapshot']={
        'path':active.get('previous_completed_snapshot','delivery/focused_legal_corpus'),
        'package_summary_generated_at':active.get('previous_completed_snapshot_generated_at') or (status['focused_package'].get('generated_at') if state!='delivered' else None),
        'completion_applies_to_saved_package_only':True}
    status['focused_package_complete']=state=='delivered' and status['focused_package'].get('focused_package_complete',False)
    status['delivery_refresh_state']='delivered' if state=='delivered' else 'awaiting_refresh_after_checkpoint' if state=='collecting' else 'awaiting_delivery_refresh' if state=='checkpointed' else 'see_resume_report'
    preflight=credit_observation(credit,'sources/trellis/worker/batch_credit_preflight.json','preflight')
    latest=preflight
    final=credit_observation(final_credit or {},'sources/trellis/resume_20260913/credit_checkpoint.json','final_credit_observation')
    final_time,preflight_time=observation_time(final),observation_time(preflight)
    if final['remaining_credits_at_observation'] is not None and final_time is not None and (preflight_time is None or final_time>=preflight_time):latest=final
    status['county_law_resume']={'control_report':active,'progress_snapshot':progress,
        'control_report_path':'reports/active_county_law_resume.json','progress_report_path':'reports/county_law_resume_progress.json',
        'credit_snapshot':latest,'credit_preflight_snapshot':preflight}
    status['trellis_progress']['denominator_note']='Historical broad discovery inventory, including prior judge URLs. Current acquisition progress uses only the fixed county/law selection in reports/county_law_resume_progress.json; neither denominator establishes nationwide completeness.'
    status['limitations']=[
        'The active acquisition includes only the selected counties and state-law URLs; judges remain prior saved material.',
        'Progress uses the fixed selected URL denominator and its own timestamp; terminal outcomes include failed captures.',
        'The previous completed delivery does not include this expansion until its package and index are refreshed.',
        'The full underlying nationwide corpus remains incomplete. County inventory coverage is not complete county-document coverage.',
        'Credit values are timestamped saved provider observations, not a live balance. Older preflight observations can precede subsequent batch charges.']
    if state=='delivered':status['limitations'][2]='Delivery completion applies to the refreshed selected package only, with remaining gaps documented.'

def county_law_resume_markdown(status):
    resume=status['county_law_resume'];active=resume['control_report'];progress=resume['progress_snapshot'];credit=resume['credit_snapshot']
    state=status['collection_state'];package=status['focused_package'];worker=status['firecrawl_batch'] or status['firecrawl_worker']
    number=lambda value: f'{value:,}' if isinstance(value,(int,float)) and not isinstance(value,bool) else 'unknown'
    selected=progress.get('selected_urls',active.get('paid_page_selection_budget'))
    successful=progress.get('successful_selected_urls');terminal=progress.get('terminal_selected_urls')
    terminal_percent=progress.get('selected_progress_percent')
    terminal_display=f'{terminal_percent:.2f}%' if isinstance(terminal_percent,(int,float)) else 'unknown percent'
    state_text={'collecting':'County and remaining state-law acquisition is **collecting**. Delivery is being refreshed after this bounded selection checkpoints.',
        'checkpointed':'County and remaining state-law acquisition is **checkpointed**. The refreshed delivery is pending; the earlier package remains available.',
        'delivered':'The county and state-law expansion is **delivered** in the refreshed selected package.'}.get(state,f'The resume report records **{state}**. See that report for the current delivery state.')
    category_rows=[]
    for key,label in (('county','County profiles'),('rules','State-law URLs')):
        counts=progress.get('by_category',{}).get(key,{})
        outcomes=', '.join(f'{number(value)} {kind}' for kind,value in sorted(counts.items())) or 'not reported'
        category_rows.append(f'| {label} | {number(sum(counts.values())) if counts else "unknown"} | {outcomes} |')
    direct=progress.get('direct_official_county_entries',{});checkpoint=direct.get('run_checkpoint',{})
    direct_time=progress.get('updated_at','not reported')
    body_counts=progress.get('law_body_status_counts',{})
    body_text=', '.join(f'{number(value)} {kind}' for kind,value in sorted(body_counts.items())) or 'not reported'
    credit_label='final credit observation' if credit['observation_kind']=='final_credit_observation' else 'credit preflight'
    credit_note='This is the saved final credit checkpoint; the offline generator does not query a live balance.' if credit['observation_kind']=='final_credit_observation' else 'That preflight can precede subsequent batch charges; the offline generator does not query a live balance.'
    if state=='delivered':
        delivery=f"[Open the refreshed delivery](../delivery/focused_legal_corpus/README.md). Its summary was generated at {package.get('generated_at','not reported')}; it contains {number(package.get('selected_capture_records'))} capture records and {number(package.get('distinct_searchable_texts'))} distinct searchable texts."
    else:
        delivery=f"[The previous completed delivery snapshot](../delivery/focused_legal_corpus/README.md) remains available. Its summary was generated at {package.get('generated_at','not reported')} and records {number(package.get('selected_capture_records'))} capture records and {number(package.get('distinct_searchable_texts'))} distinct searchable texts. Those are previous delivery counts, not totals for the current expansion; its package and index are awaiting refresh."
    return f'''# County and state-law expansion status

Status generated {status['updated_at']}. {state_text}

Scope: **selected counties and remaining state-law URLs only**. Judge rosters and profiles are prior saved package material; no judge acquisition is active. **No recurring automation is active.**

The fixed **{number(selected)}-URL paid selection** had **{number(successful)} successful captures** and **{number(terminal)} terminal outcomes ({terminal_display})** at **{progress.get('updated_at','not reported')}**. Terminal progress includes failed outcomes and does not mean all selected pages were saved. These counts come from the [resume progress snapshot](county_law_resume_progress.json), independently of the worker's later status timestamp.

| Selected category | URL count | Recorded outcomes |
|---|---|---|
{chr(10).join(category_rows)}

Recorded law-body classifications: {body_text}. A saved directory is not counted as proof that its substantive law body was captured.

The separate direct official-county entry collection recorded **{number(direct.get('downloaded_resources'))} downloaded resources** in the progress snapshot at {direct_time}; these may include allowed redirect targets and are outside the paid selection denominator. Its last saved run checkpoint recorded **{checkpoint.get('stop_reason','not reported')}** at {checkpoint.get('generated_at','not reported')}; that earlier checkpoint may precede additional collection. Access barriers and failed requests remain in the evidence.

The latest saved worker snapshot reports **{worker.get('status',progress.get('worker_status','not reported'))}**, phase **{worker.get('active_phase',progress.get('active_phase','not reported'))}**, at **{worker.get('updated_at',progress.get('worker_updated_at','not reported'))}**. It is a recorded snapshot, not a process-liveness check. The saved provider **{credit_label}** recorded **{number(credit['remaining_credits_at_observation'])} credits** at **{credit.get('checked_at') or 'not reported'}** (HTTP {credit.get('api_status') or 'not reported'}; [source checkpoint](../{credit['source']})). {credit_note} The selection reserve is {number(active.get('credit_reserve'))} credit(s).

The worker snapshot reports **{number(worker.get('run_credits_used'))} credits observed in this process run**, which resets after a restart. The progress report separately records **{number(progress.get('provider_credits_used_selected_pass'))} credits observed across the selected pass** at {progress.get('updated_at','not reported')}, from matching batch manifests with each remote job counted once. That pass observation can lag in-flight provider charges; it is not a live remaining balance.

{delivery}

**The full underlying nationwide corpus remains incomplete.** The fixed selection and delivery have explicit limits; neither is an enumeration of every law, county document, judge profile or court record. Original captures, editions, missing-law-body classifications and access evidence remain preserved.

[Active resume control](active_county_law_resume.json) · [Fixed-selection progress](county_law_resume_progress.json) · [Detailed measured status](status.json). Run `python scripts/build_status.py` to refresh this status from saved reports; it does not collect sources or rebuild the package/index.
'''

def main():
    laws=read('sources/official_laws/indexes/collection_summary.json')
    lawdocs=read('sources/official_laws/indexes/document_manifest.json',[])
    lawmeta=[]
    for f in (ROOT/'sources/official_laws/metadata').glob('*.json'):
        try:lawmeta.append(json.loads(f.read_text(encoding='utf-8')))
        except (OSError,ValueError):pass
    good=[m for m in lawmeta if m.get('verification_status')=='retrieved']
    pdf=[m for m in good if m.get('format')=='pdf']
    court=read('sources/official_courts/reports/collection_summary.json')
    trellis=read('sources/trellis/catalog/summary.json')
    browser=read('sources/trellis/browser/downloads_manifest.json',[])
    browser_laws=browser_law_snapshot()
    status={'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'full_requested_corpus_complete':False,
       'workspace':str(ROOT),'free_disk_bytes':shutil.disk_usage(ROOT).free,
       'trellis_public':{'county_responses_saved':len(list((ROOT/'sources/trellis/counties').glob('*.firecrawl.json'))),'initial_county_urls':len(read('sources/trellis/discovery/county_urls.json',[])),'catalog':trellis},
       'trellis_paid':{'downloaded_pdfs':sum(x.get('status')=='downloaded' for x in browser),'pdf_pages':sum(x.get('pages',0) for x in browser),'bytes':sum(x.get('bytes',0) for x in browser),'access':read('sources/trellis/browser/access_observations.json')},
       'official_laws':{'retrieved_sources':len(good),'pdfs':len(pdf),'pdf_pages':sum(x.get('pdf_pages',0) for x in pdf),'raw_bytes':sum(x.get('bytes',0) for x in good),'document_manifest_records':len(lawdocs),'collection_summary':laws,'active_run':read('sources/official_laws/active_run.json')},
       'official_courts':court,
       'official_courts_continuation':{'live_snapshot':generic_snapshot(ROOT/'corpus/official_courts'),'checkpoint':read('corpus/official_courts/checkpoint.json'),'active_run':read('corpus/official_courts/active_run.json')},
       'public_http_collections':{folder.name:generic_snapshot(folder) for folder in (ROOT/'corpus').iterdir() if folder.is_dir() and (folder/'corpus.sqlite3').exists()},
       'firecrawl_worker':read('sources/trellis/worker/status.json'),
       'firecrawl_batch':read('sources/trellis/worker/batch_status.json'),
       'trellis_progress':trellis_progress(browser_laws),
       'trellis_browser_laws':browser_laws,
       'ocr':read('sources/official_courts/ocr/status.json'),
       'continuation_ocr':read('corpus/official_courts/ocr/status.json'),
       'law_ocr':{'queue':read('sources/official_laws/ocr/queue_summary.json'),'status':read('sources/official_laws/ocr/status.json'),'active_run':read('sources/official_laws/ocr/active_run.json')},
       'law_recovery_ocr':{'queue':read('corpus/official_law_recovery_pass_1/ocr/queue_summary.json'),'status':read('corpus/official_law_recovery_pass_1/ocr/status.json'),'active_run':read('corpus/official_law_recovery_pass_1/ocr/active_run.json')},
       'law_page_ocr':{'queue':read('corpus/official_law_pages/ocr/queue_summary.json'),'status':read('corpus/official_law_pages/ocr/status.json'),'active_run':read('corpus/official_law_pages/ocr/active_run.json')},
       'law_document_derivatives':read('corpus/official_law_pages/document_derivatives/summary.json'),
       'law_xml_derivatives':document_derivative_snapshot('corpus/official_law_pages/xml_document_derivatives'),
       'law_epub_derivatives':document_derivative_snapshot('corpus/official_law_pages/epub_document_derivatives'),
       'law_archive_reconciliation':read('corpus/official_law_pages/document_derivatives/archive_member_reconciliation.summary.json'),
       'law_queue_status':read('reports/law_queue_status.json'),
       'geography_reconciliation':read('reports/geography/summary.json'),
       'search_index':read('catalog/summary.json'),
       'active_scope':['state rules, codes, statutes, constitutions and laws','judges','counties'],
       'excluded_from_active_scope':['cases','filings','dockets','motions','dated coverage archives','general public-URL expansion'],
       'limitations':['Complete law, judge and county inventories are not yet reconciled; discovered URL totals are not proof of full coverage.','Firecrawl batches permit two starts per minute and two concurrent browsers; remaining credits are finite.','Unfetched relevant URLs, actual access barriers, failed requests, OCR and source-edition gaps remain explicit work items. Paid case downloads are outside active scope.']}
    focused_scope=read('reports/focused_package_scope.json')
    focused_package=read('delivery/focused_legal_corpus/summary.json')
    focused_credit=read('sources/trellis/worker/focused_package_credit_checkpoint.json')
    resume_exists=(ROOT/'reports/active_county_law_resume.json').exists()
    status['focused_package_scope']=focused_scope
    status['focused_package']=focused_package
    status['focused_package_complete']=focused_package.get('focused_package_complete',False)
    status['focused_package_credit_checkpoint']=focused_credit
    if focused_scope and not resume_exists:
        status['active_scope']=['saved laws with explicit body/edition/access gaps','available official judge rosters','complete Census county-equivalent inventory with saved profile/site evidence']
        status['collection_state']='checkpointed_by_user_focused_package_choice'
        status['limitations']=['The user selected a focused package; the full underlying national document and judge-profile corpus remains incomplete.','County inventory coverage does not mean every county profile or website was downloaded.','Saved law representations include directories and some pages with missing law-body storage placeholders; classifications remain explicit.']
    if resume_exists:
        apply_county_law_resume(status,read('reports/active_county_law_resume.json'),read('reports/county_law_resume_progress.json'),read('sources/trellis/worker/batch_credit_preflight.json'),read('sources/trellis/resume_20260913/credit_checkpoint.json'))
    reports=ROOT/'reports';reports.mkdir(exist_ok=True)
    (reports/'status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    law_ocr_status=status['law_ocr']['status']
    law_ocr_display=(f"{law_ocr_status.get('completed_pages',0):,} / {law_ocr_status.get('total_pages',status['law_ocr']['queue'].get('pages',0)):,} selected scan pages completed"
                     if law_ocr_status or status['law_ocr']['queue'] else 'Screening saved PDFs; OCR queue not yet finalized')
    law_page_ocr_display=(f"{status['law_page_ocr']['status'].get('completed_pages',0):,} / {status['law_page_ocr']['queue'].get('pages',0):,} selected scan pages completed"
                          if status['law_page_ocr']['queue'] else 'Screening downloaded law PDFs; queue not yet finalized')
    text=f'''# Legal corpus collection status

Updated {status['updated_at']}. **The focused law/judge/county corpus is incomplete.**

Active order: **state rules/codes/laws → judges → counties**. Current Trellis phase: **{status['trellis_progress']['active_phase'] or 'not reported'}**. {status['trellis_progress']['excluded_urls_preserved']:,} previously discovered unrelated URLs are preserved outside the active queue. Broad court/county crawlers are paused during the law/judge phases.

Trellis progress: **{status['trellis_progress']['initial_county_batch']['percent']}%** of the initial county-page batch ({status['trellis_progress']['initial_county_batch']['saved']:,}/{status['trellis_progress']['initial_county_batch']['total']:,}) and **{status['trellis_progress']['currently_discovered_public_urls']['percent']}%** of currently discovered URLs in the narrowed scope ({status['trellis_progress']['currently_discovered_public_urls']['saved']:,}/{status['trellis_progress']['currently_discovered_public_urls']['total']:,}) at their recorded snapshots. The complete focused-corpus denominator is unknown; the growing URL queue is not a complete inventory. Percentages against the earlier all-URL queue are not directly comparable.

| Collection | Saved scope |
|---|---|
| Trellis public county pages | {trellis.get('county_profiles',status['trellis_public']['county_responses_saved']):,} parsed county profiles; initial batch contains {status['trellis_public']['initial_county_urls']:,} URLs |
| Trellis browser law captures | {browser_laws['law_text_urls']:,} full-text section captures and {browser_laws['law_directory_urls']:,} directory captures; browser DOM provenance retained separately |
| Trellis authenticated PDFs | {status['trellis_paid']['downloaded_pdfs']:,} files, {status['trellis_paid']['pdf_pages']:,} pages |
| Official state-law PDFs | {len(pdf):,} files, {status['official_laws']['pdf_pages']:,} pages |
| Official court sources | {court.get('retrieved_sources',0):,} retrieved sources; {court.get('pdfs',0):,} PDFs and {court.get('xlsx',0):,} spreadsheets |
| Official court continuation | {status['official_courts_continuation']['live_snapshot'].get('downloaded_resources',0):,} additional retrieved resources; {status['official_courts_continuation']['live_snapshot'].get('pdfs',0):,} PDFs ({status['official_courts_continuation']['live_snapshot'].get('pdf_pages',0):,} pages) |
| Official-law page continuation | {status['public_http_collections'].get('official_law_pages',{}).get('downloaded_resources',0):,} resources retrieved from its 6,671-URL batch |
| Official-law recovery pass | {status['public_http_collections'].get('official_law_recovery_pass_1',{}).get('downloaded_resources',0):,} resources retrieved across 58 earlier failures and 53 observed canonical PDF targets; original failures retained |
| Nebraska full statutory chapters | {status['public_http_collections'].get('official_law_nebraska_chapters',{}).get('downloaded_resources',0):,} / 90 observed full-chapter targets saved |
| Prepared law queues | {status['law_queue_status'].get('direct_law_network_work_remaining','unknown')} direct-source URLs still require network work; {sum(status['law_queue_status'].get('direct_law_queue_counts',{}).get(k,0) for k in ('saved_host_pause','saved_robots_denial')):,} pending URLs have saved access barriers |
| County website entries | {status['public_http_collections'].get('county_sites',{}).get('downloaded_resources',0):,} resources retrieved from its 2,986-URL batch |
| Completed initial OCR | {status['ocr'].get('documents_complete',0)} scanned PDFs; {status['ocr'].get('completed_pages',0):,} pages |
| Continuation OCR | {status['continuation_ocr'].get('completed_pages',0):,} / {status['continuation_ocr'].get('total_pages',0):,} selected scan pages completed |
| Official-law OCR | {law_ocr_display} |
| Law recovery OCR | {status['law_recovery_ocr']['status'].get('completed_pages',0):,} / {status['law_recovery_ocr']['queue'].get('pages',0):,} selected scan pages completed |
| Additional law-page OCR | {law_page_ocr_display} |
| Saved Word-format lawbooks | {status['law_document_derivatives'].get('statuses',{}).get('extracted',0):,} DOCX and {status['law_xml_derivatives']['extracted_records']:,} Word XML text derivatives extracted |
| Iowa code/acts e-book | {status['law_epub_derivatives']['extracted_records']:,} verified EPUB text derivative; original book edition metadata retained |
| Nationwide baseline | {court.get('states_plus_dc',0)} states/DC; {court.get('county_equivalents',0):,} Census county equivalents |
| Unified search index | {status['search_index'].get('source_records',0):,} source records; {status['search_index'].get('unique_searchable_texts',0):,} distinct searchable texts at its last build |

The public Trellis worker last reported **{status['firecrawl_worker'].get('remaining_credits_last_observed','unknown')} Firecrawl credits remaining** at {status['firecrawl_worker'].get('updated_at','an unavailable time')}. Its reported state is **{status['firecrawl_worker'].get('status','unknown')}**, using batches of up to {status['firecrawl_batch'].get('batch_size','unknown')} pages and {status['firecrawl_batch'].get('max_concurrency','unknown')} concurrent browsers. The current request cache-age ceiling is {status['firecrawl_batch'].get('cache_max_age_ms',0):,} milliseconds; returned cache metadata and source editions are retained. It checkpoints before credit exhaustion. Hidden local collectors and the 30-minute continuation heartbeat maintain the accessible queues; current process details are in each collection's `active_run.json` and logs.

The browser-law path is separate from Firecrawl. Its latest account observation is **{browser_laws['browser_access'].get('content_views_used','unknown')} / {browser_laws['browser_access'].get('content_views_limit','unknown')} content views used**, recorded at {browser_laws['browser_access'].get('observed_at','an unavailable time')}. Browser visits can consume this allowance. Completed section batches establish coverage of their observed Trellis directory links only. Browser captures remain separate from provider downloads and their exact URLs are excluded from duplicate paid acquisition through the saved queue reconciliation.

Historical paid-browser observation: Account Overview showed **CA and IL**, **Lawyer Entry Monthly**, and **{status['trellis_paid']['access'].get('content_views_used','?')}/{status['trellis_paid']['access'].get('content_views_limit','?')} content views**. The earlier case-PDF attempt was blocked by Chrome. Case acquisition and its manual download check are superseded by the latest scope; they are not blocking the current law/judge/county work. See `sources/trellis/browser/access_observations.json` for the original evidence.

Original downloads, extracted text, response metadata and source URLs are retained in the source folders. Public Firecrawl HTML is a provider-rendered representation; direct public HTTP and authenticated PDF downloads retain original response/file bytes. Discovered URL counts are separate from successfully downloaded resources. Law-source capture counts include navigation pages and ancillary documents; they do not establish complete substantive law text. Court listings are not assumed to enumerate every case or filing.

Use `python scripts/build_law_queue_status.py`, `python scripts/build_catalog.py` and `python scripts/build_status.py` to refresh these snapshots. See `RUNBOOK.md` for continuation and `reports/status.json` for detailed measured counts. File counts are per collection and are not a globally deduplicated total.
'''
    if focused_scope and not resume_exists:
        package_state='complete' if status['focused_package_complete'] else 'being assembled'
        credit_value=focused_credit.get('response',{}).get('data',{}).get('remainingCredits','unknown')
        text=f'''# Focused legal corpus package

Updated {status['updated_at']}. The user-approved focused package is **{package_state}**. [Open the package](../delivery/focused_legal_corpus/README.md).

The final law batch finished at {focused_scope.get('law_batch_drained_at','the recorded cutoff')}. New Trellis batches are disabled. The last verified balance was **{credit_value} Firecrawl credits** at {focused_credit.get('checked_at','an unavailable time')}. Browser collection is checkpointed. The continuation automation was paused for package assembly and is removed when the package is complete.

| Component | Saved evidence |
|---|---|
| Official law source URLs | {len(good)+status['public_http_collections'].get('official_law_pages',{}).get('downloaded_resources',0)+status['public_http_collections'].get('official_law_recovery_pass_1',{}).get('downloaded_resources',0)+status['public_http_collections'].get('official_law_nebraska_chapters',{}).get('downloaded_resources',0):,} across law collections, including directories and ancillary material |
| Trellis law representations | {trellis.get('downloaded_pages_by_category',{}).get('state_rule',0):,} provider captures and {browser_laws['captured_urls']:,} browser captures; body availability classified in the package |
| County inventory | 3,144 Census county-equivalent rows; saved profiles and official website evidence linked where available |
| Search selection | {focused_package.get('selected_capture_records',0):,} capture records; {focused_package.get('distinct_searchable_texts',0):,} distinct searchable texts |

See each package component for judge roster coverage, exact county matches, source editions and missing documents/profiles. **The full underlying nationwide corpus remains incomplete.** The accepted deliverable is the focused package with those gaps documented. Earlier downloads and discovery evidence remain intact outside the selection. `status.json` retains the detailed collection snapshots.
'''
    if resume_exists:text=county_law_resume_markdown(status)
    (reports/'STATUS.md').write_text(text,encoding='utf-8')
    print(json.dumps({k:v for k,v in status.items() if k in ('updated_at','full_requested_corpus_complete','free_disk_bytes')}))
if __name__=='__main__':main()
