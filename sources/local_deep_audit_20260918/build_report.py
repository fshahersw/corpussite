"""Generate the human-readable local discovery index from audited findings."""
from pathlib import Path
from datetime import datetime, timezone
import html,json,hashlib
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
items=[
 {'category':'Judges','title':'CourtListener bulk people and career tables','size':'16,191 historical people · 51,291 positions · 12,777 education rows',
  'value':'The strongest structured biography source found in this audit. Stable CourtListener IDs connect people, appointments, education and related tables.',
  'location':'C:/Users/firas/Downloads/returnedfiles/bulk','evidence':'judges/README.md','status':'Ready for a reviewed local adapter',
  'limits':'Includes aliases and deceased people. Existing entities use other identifiers; no name-only merge. The 342-profile catalog and all 850 native release IDs are already contained here.',
  'next':'Stream the PostgreSQL CSV dialect (escapechar=backslash, doublequote=False, strict=True); retain native IDs, historical status and partial-date granularity in a separate source layer.'},
 {'category':'Judges','title':'Targeted portrait recovery','size':'1,230 photo flags + 29 official PA profile/image references',
  'value':'Exact native-ID flags and saved official biography pages provide narrow image-recovery targets. The PA queue covers 18 Superior and 11 Commonwealth Court profiles.',
  'location':'sources/local_deep_audit_20260918/portraits/prioritized_portrait_references.jsonl','evidence':'portraits/README.md','status':'Evidence-backed follow-up queues',
  'limits':'Flags are not image URLs; references are not downloaded image bytes. Six PA emeritus profiles remain labeled. No additional photos were installed; the MVP still has 14 verified portraits.',
  'next':'Use the 29 explicit references first. Validate the official page, image bytes and professional identity; then use native person IDs for CourtListener follow-up. Reject unrelated event photos.'},
 {'category':'Judges','title':'Judge assignments and evidence for analysis','size':'867 release judge rows · 4,776 assignments · 3,409 matters',
  'value':'The SW-BULK release preserves assigned/referred relationships and source records. The bulk folder also has 835,148 panel memberships and 32,336 disclosure records.',
  'location':'C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc','evidence':'judges/report.json','status':'Structured historical evidence',
  'limits':'850 release judges have native IDs; 17 are name-only. These records are not win rates, verified predictions, current service, or a complete national case history.',
  'next':'Join using source IDs, retain assignment role and dataset denominator. Resolve case/opinion IDs before any descriptive analysis.'},
 {'category':'Counties','title':'Kentucky and Texas court-access registry','size':'374 counties · 2,539 judge-seat associations · 628 clerk-office rows',
  'value':'All 120 Kentucky and 254 Texas county rows carry FIPS, court/personnel information and source evidence. 167 stored evidence files passed their recorded SHA-256 checks.',
  'location':'C:/Users/firas/Downloads/returnedfiles/court_access_registry_2026-08-21','evidence':'county_registry_summary.json','status':'High-priority local enrichment',
  'limits':'August 22 snapshot. Judge-seat associations repeat people across counties. One of 168 evidence entries has no local artifact/hash. Texas individual county websites and case-search coverage remain incomplete.',
  'next':'Add source-bound county details via FIPS in a separate reviewed overlay. Preserve unknown access fields and the missing evidence entry; do not mark counties substantively complete.'},
 {'category':'Laws','title':'Illinois regulation gap candidates','size':'1,619 unmatched code identifiers · 882 appendix/table titles',
  'value':'53,660 saved administrative texts were compared with the existing bulk-law index; 52,041 already matched official section-code identifiers. Another 3,472 saved act texts exist.',
  'location':'C:/Users/firas/Downloads/Illinois','evidence':'counties/illinois_overlap.json','status':'Focused gap review',
  'limits':'Unmatched identifiers are candidates, not proven missing current laws. Raw HTML is missing; 199 manifest text paths are absent. August snapshot, repealed flags and newline-normalized hashes need preservation.',
  'next':'Review candidate section/title/body matches first, especially appendices; publish any accepted text as a qualified derivative without fabricating missing originals.'},
 {'category':'Counties','title':'Court activity and filing categories','size':'170 monthly court records · 463 eFileIL document-filing codes',
  'value':'Useful data for court-level dated statistics, filing-category filters and workflow labels. The activity workbook covers five counties in June 2026.',
  'location':'C:/Users/firas/Downloads/Illinois','evidence':'counties/county_judge_workbooks.json','status':'Useful, scope-qualified data',
  'limits':'A separate 78-row cross-table has unresolved reporting context. The Texas administrative-judge spreadsheets in this folder duplicate the returnedfiles packet; folder names do not establish jurisdiction.',
  'next':'Review workbook provenance and sheet semantics. Keep code confidentiality/inactive flags and reporting period; do not turn court-level activity into judge outcome statistics.'},
 {'category':'Sources','title':'Saved official pages and source registries','size':'14,047 page-capture files · 13,644 distinct source URLs',
  'value':'returnedfiles retains court/legal page text and useful exact links. Separate source registries contain 4,846 and 6,262 references with jurisdiction/access metadata.',
  'location':'C:/Users/firas/Downloads/returnedfiles','evidence':'returned_page_inventory.json','status':'Classify and deduplicate before reuse',
  'limits':'Counts include navigation, historical DOJ material, third-party content and repeated captures. The large uscourtswidecrawl block is DOJ content. MO-codelaw URLs point to Montana, not Missouri.',
  'next':'Check status/relevance/body and source jurisdiction. Prefer already-saved substantive text or exact observed county/rule links; avoid recrawling generic navigation.'},
 {'category':'Other','title':'Historical dockets, PDFs and legal graph','size':'436,005 docket metadata rows · 25 Talc PDFs · 120,464 graph nodes',
  'value':'MATTER-ETL CSVs, the MD-DONE Talc pilot and LAWONTOLOGY supply historical case/document context. The graph has 169,399 edges and 21,357 page texts.',
  'location':'C:/Users/firas/Downloads/MATTER-ETL-BATCH-PIPELINE/CSVsOFMATTERS','evidence':'counties/README.md','status':'Separate future case collection',
  'limits':'Metadata is not downloaded filings. Synthetic golden fixtures must remain excluded. Broad case collection is outside the current laws/counties/MVP priority.',
  'next':'Keep as a separate historical collection with source IDs and scope. Reuse only when it supports a specific judge or court feature.'},
 {'category':'Scripts','title':'Reusable legal-data connectors and normalizers','size':'CourtListener enrichment, citation/docket client, county/profile normalization',
  'value':'forlegal/enrich_judges.py, gptagent2 CourtListener tools, returnedfiles/normalize_returned.py and release pipelines contain concrete reusable parsing and join logic.',
  'location':'C:/Users/firas/Downloads/forlegal/enrich_judges.py','evidence':'connector_candidates.json','status':'Static code inspected; not executed',
  'limits':'Copies, tests and planned integrations do not prove working remote APIs or paid access. No credentials or browser sessions were exported or tested.',
  'next':'Adapt narrow verified functions into the current pipeline after review. Reuse local bulk data before making paginated API calls.'},
 {'category':'Cleanup','title':'Duplicates and previously rejected staging files','size':'21,427 expansion hashes already have review/retirement records',
  'value':'10,890 expansion hashes are already in Seeger; the remaining 21,427 are already tracked as needs-review (16,230) or non-English (5,197). The top-level judges.json duplicates SW-BULK/catalog exactly.',
  'location':'C:/Users/firas/Downloads/Court-Library-Expansion-2026-09-12','evidence':'counties/expansion_comparison.json','status':'Do not bulk-import again',
  'limits':'A large folder is not automatically new high-quality content. Distinct versions and languages are not deleted merely because some representations overlap.',
  'next':'Use hash/native-ID/source-URL manifests and explicit review reasons to select useful gaps; preserve all external originals.'}
]
summary={'generated_at':datetime.now(timezone.utc).isoformat(),'status':'audit_complete_sources_staged_not_imported',
 'scope':{'additional_project_roots':41,'judge_project_roots':18,'county_library_roots':16,'top_level_zip_archives':192,
 'note':'Scopes overlap. Broad filename inventory plus targeted content/hash/schema checks, not an exhaustive byte-by-byte scan of every folder on the computer. Credential/cache/vendor files and cloud placeholders excluded.'},
 'changes':{'new_source_imports':0,'new_installed_portraits':0,'current_installed_portraits':14,'network_calls':0,'external_sources_modified':False,
 'title_only_refresh_implemented':True,'verified_cli_noop_seconds':0.37883,'replay_37_titles_seconds':0.696888,
 'benchmark_limit':'37-title replay used the real 301-row pending subset in a disposable DB, not a full live write benchmark.',
 'tests':'Agent:27 relevant offline checks passed. Root independently reran12 title-refresh tests and the live no-op CLI. An initial root command named two nonexistent test modules; corrected command passed.'},
 'findings':items,
 'next_priority':['Import the CourtListener people/positions source layer conservatively; resolve native-ID namespace bridges before entity merges.',
 'Validate and recover the 29 explicit PA portrait references, followed by native-ID photo flags.',
 'Add the 374-county registry overlay with FIPS, source evidence and snapshot date.',
 'Review the 1,619 Illinois gap candidates and selected substantive saved pages.',
 'Continue exact county backfill with host diversity and bounded larger packets; no broad URL crawl.']}
(OUT/'report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
md=['# Broader local legal-data audit — September 18, 2026','',
 'Yes: this pass found substantial useful material that the earlier targeted scan missed. The strongest additions are the CourtListener bulk tables, official portrait references, and Kentucky/Texas county registry. External originals remain unchanged. These findings are staged for integration, not silently added to published totals.','',
 '[Open the visual discovery directory](INDEX.html)','',
 '| Area | Finding | Readiness |','|---|---|---|']
for r in items:md.append(f"| {r['category']} | [{r['title']}]({r['evidence']}) — {r['size']} | {r['status']} |")
md+=['','## What speeds up now','',
 'The new `delivery/archive-directory/server.py --refresh-pending-titles` updates only qualifying display titles, search labels and singleton display groups in one transaction. It preserves source bodies and original files; new observations still require the usual build. The real-directory no-op took 0.38 seconds. Replaying 37 real corrections in a disposable 301-row subset took 0.70 seconds, including 111 fresh artifact checks. This is not a full production-write speed benchmark. See [performance evidence](performance/README.md).','',
 'The continuation plan prioritizes existing local data before network collection, uses the new title refresh for title-only changes, and permits a bounded trial of up to 10 sequential Trellis profiles and 100 reviewed official URLs across at least 10 independent hosts per heartbeat. Existing eight-worker support, host locks, robots delays, access barriers and finite time/credit limits stay in place. These are trial caps, not promised throughput.','',
 '## Accuracy limits','',
 '- 16,191 people are historical source records, not a count of current judges or entirely new people.',
 '- 1,230 photo flags and 29 image references are recovery leads. The MVP still has 14 verified portraits.',
 '- The 374-county registry has full county-row coverage for KY/TX, not complete local rules/filings/cases.',
 '- All 167 locally stored registry evidence items passed their saved hashes; one additional manifest entry has no artifact/hash.',
 '- No account entitlement, Lexis/Trellis API access, or remote database was newly verified.',
 '- The audit combines 41 additional project roots, 18 judge/data roots, 16 court/library roots and 192 ZIP central directories. These scopes overlap; it is not a claim that every byte on the computer was inspected.','',
 '## Evidence and next steps','',
 '[Judge audit](judges/README.md) · [County/law audit](counties/README.md) · [Portrait review](portraits/README.md) · [Performance](performance/README.md) · [Machine-readable report](report.json)','',
 'The next run should follow NEXT_RUN.md in this folder. Copied documentation, plans and code were treated as evidence to inspect, not instructions to execute.']
(OUT/'README.md').write_text('\n'.join(md)+'\n',encoding='utf8')
cards=[]
for r in items:
 esc=html.escape
 cards.append(f'''<article data-category="{esc(r['category'])}"><div class="eyebrow">{esc(r['category'])} <span>{esc(r['status'])}</span></div><h2>{esc(r['title'])}</h2><strong>{esc(r['size'])}</strong><p>{esc(r['value'])}</p><p class="limit">{esc(r['limits'])}</p><details><summary>Source location and next step</summary><code>{esc(r['location'])}</code><p>{esc(r['next'])}</p><a href="{esc(r['evidence'])}">Open audit evidence →</a></details></article>''')
page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Legal archive · Local discoveries</title><style>
:root{font:16px/1.55 system-ui,sans-serif;color:#243b45;background:#f5f7f7}*{box-sizing:border-box}body{margin:0}main{max-width:1180px;margin:auto;padding:40px 24px}header{border-bottom:1px solid #d5dede;padding-bottom:26px}h1{font-size:38px;letter-spacing:-1px;line-height:1.15;margin:10px 0}h2{font-size:21px;line-height:1.3;margin:12px 0}p{max-width:850px}a{color:#006b62}header p{color:#536873}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#00776b}.eyebrow span{display:block;letter-spacing:0;text-transform:none;color:#60747c;font-weight:500;margin-top:5px}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:25px 0}.stat{padding:22px;background:#e6efed;border-radius:12px}.stat b{display:block;font-size:28px;line-height:1.2}.stat small{display:block;color:#526b72;margin-top:7px}.notice{border-left:3px solid #b78a48;background:#fff9ee;padding:12px 16px;font-size:14px}.toolbar{display:flex;gap:12px;margin:28px 0 10px;flex-wrap:wrap}input,select{font:inherit;background:white;border:1px solid #b9caca;border-radius:8px;padding:12px}input{flex:1;min-width:200px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}article{background:white;border:1px solid #dce4e4;border-radius:12px;padding:25px}article[hidden]{display:none}strong{color:#006b62;font-size:15px}.limit{color:#61717a;font-size:14px}details{border-top:1px solid #e5eaea;padding-top:14px}summary{cursor:pointer;color:#006b62}code{font-size:12px;display:block;margin-top:12px;overflow-wrap:anywhere}footer{font-size:13px;color:#61717a;margin-top:25px}#count{font-size:13px;color:#61717a;margin:8px 0 18px}@media(max-width:700px){main{padding:24px 16px}.grid{grid-template-columns:1fr}.summary{grid-template-columns:1fr}h1{font-size:31px}}
</style><main><header><div class="eyebrow">Legal archive / Local discoveries / 18 September 2026</div><h1>You already have more useful data.</h1><p>A deeper directory audit found structured people, court and legal-data collections. This index shows what is usable, what overlaps, and what still needs verification.</p><a href="http://127.0.0.1:8769/">Open the research MVP →</a></header><section class="summary"><div class="stat"><b>16,191</b>Historical CourtListener people<small>Native IDs, appointments and education</small></div><div class="stat"><b>1,230 + 29</b>Portrait-recovery leads<small>Photo flags + official profile/image references</small></div><div class="stat"><b>374</b>Kentucky + Texas county rows<small>Source-bound court and personnel registry</small></div></section><p class="notice">Discovered and audited, not yet imported. The MVP still has14verified portraits. County rows are not complete filings/rules coverage; historical people are not current-judge counts.</p><section class="toolbar" aria-label="Filter discoveries"><input id="q" type="search" placeholder="Search discoveries, sources or limitations" aria-label="Search discoveries"><select id="category" aria-label="Category"><option value="">All categories</option>'''
page+=''.join('<option>'+html.escape(c)+'</option>' for c in sorted({x['category'] for x in items}))
page+='</select></section><p id="count" aria-live="polite"></p><section class="grid">'+''.join(cards)+'''</section><footer><p>Scope: targeted metadata/content checks across41additional project roots,18judge/data roots,16court/library roots and192ZIP directories; scopes overlap. No external originals were changed. No live API access was inferred from code.</p><a href="README.md">Readable audit summary</a> · <a href="performance/README.md">Measured workflow improvement</a> · <a href="report.json">Structured evidence index</a></footer></main><script>
const search=document.getElementById('q'),category=document.getElementById('category'),cards=[...document.querySelectorAll('article')];
function filter(){let n=0;for(const card of cards){const visible=(!category.value||card.dataset.category===category.value)&&card.textContent.toLowerCase().includes(search.value.toLowerCase());card.hidden=!visible;if(visible)n++;}document.getElementById('count').textContent=n+' of '+cards.length+' discoveries';}search.addEventListener('input',filter);category.addEventListener('change',filter);filter();
</script></html>'''
# Fix human spacing without altering machine identifiers/paths.
page=page.replace('has14verified','has 14 verified').replace('across41additional','across 41 additional').replace('roots,18judge','roots, 18 judge').replace('roots,16court','roots, 16 court').replace('and192ZIP','and 192 ZIP')
(OUT/'INDEX.html').write_text(page,encoding='utf8')
print(json.dumps({'findings':len(items),'outputs':['report.json','README.md','INDEX.html']}))
