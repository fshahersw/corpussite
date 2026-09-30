/* Pure presentation projections. Never mutate source records or infer legal effect. */
(function(root){
'use strict';
const jurisdictions=new Set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI'.split(' '));
const tabs=new Set(['overview','courts','resources','quality','docket','documents','participants','connections','trends']);
function numeric(value){
 if(value==null||typeof value==='boolean'||typeof value==='object'||String(value).trim()==='')return null;
 const n=Number(value);return Number.isFinite(n)&&n>=0?n:null;
}
function state(params){
 const usps=String(params.get('state')||'').toUpperCase(),mdl=String(params.get('mdl')||'');
 return {state:jurisdictions.has(usps)?usps:'',mdl:/^[1-9]\d{0,5}$/.test(mdl)?mdl:'',tab:tabs.has(params.get('tab'))?params.get('tab'):'overview',
 page:Math.max(1,Math.min(400,Math.floor(Number(params.get('page'))||1))),q:String(params.get('q')||'').slice(0,300),metric:String(params.get('metric')||'filing'),
 status:['pending','terminated','all'].includes(params.get('status'))?params.get('status'):'pending'};
}
function navigation(legacy){
 const known=new Map();
 for(const h of legacy)for(const t of h.views||[])for(const s of t.segments||[t])known.set(s.view,{...s});
 const newItems={corpus:{view:'corpus',label:'Collection explorer'},atlas:{view:'atlas',label:'Map & jurisdiction'},matters:{view:'matters',label:'Matter library'},additions:{view:'additions',label:'Source additions'},connections:{view:'connections',label:'Evidence connections'}};
 const definitions=[['corpus',[
  ['Explore',['corpus']],['Documents',['documents','federal','saved-pages','source-documents']],
  ['Law & science',['regulations','federal-register','public-laws','citation-index','agencies','agency-documents','cpsc-injury-data']],
  ['Sources & connections',['sources','urls','additions','connections']]
 ]],['atlas',[
  ['Map',['atlas']],['Courts & practice',['courts','court-documents','court-coverage','federal-opinions','uscourts','statistics','citation-guide']],
  ['Judges',['judges','people','judge-disclosures']],['State resources',['laws','limitation-periods','counties','coverage','resources','overview']]
 ]],['matters',[
  ['Matters',['matters']],['Dockets',['mdls','state-proceedings','mdl-activity','mdl-documents','mdl-cases']],
  ['Participants',['counsel-directory','counsel','mdl-appearances']],['Insights & outcomes',['insights','expert-rulings','settlements','verdict-reports']]
 ]]];
 const used=new Set();
 const hubs=definitions.map(([id,groups])=>({id,views:groups.map(([label,names])=>{
  const segments=names.map(n=>newItems[n]||known.get(n)).filter(Boolean).filter(s=>{if(used.has(s.view))return false;used.add(s.view);return true;});
  if(!segments.length)return null;
  return segments.length===1?{...segments[0],label}:{view:segments[0].view,label,segments};
 }).filter(Boolean)}));
 const remainder=[...known.values()].filter(s=>!used.has(s.view));
 if(remainder.length){const tab=hubs[0].views[hubs[0].views.length-1];tab.segments=[...(tab.segments||[{...tab}]),...remainder];}
 return hubs;
}
function publication(health){
 const rows=Array.isArray(health?.datasets)?health.datasets:[];
 const p={totalCollections:rows.length,publishedCollections:0,publishedRecords:0,heldRecords:0,unknownCounts:0};
 for(const row of rows){const n=numeric(row.records);if(row.ready===true)p.publishedCollections++;if(n===null)p.unknownCounts++;else p[row.ready===true?'publishedRecords':'heldRecords']+=n;}
 return p;
}
function sumKnown(values){const a=values.map(numeric).filter(n=>n!==null);return a.length?a.reduce((n,v)=>n+v,0):null;}
function atlasMetrics(rows,filing){
 const by=new Map((filing?.states||[]).map(r=>[r.state,r]));
 const m=[
 {key:'filing',label:'Counties with rules or filing sources',unit:'%',note:'Listed source coverage, not downloaded rulebooks or complete legal coverage.',value:r=>{const f=by.get(r.abbr),d=numeric(f?.counties_total),n=numeric(f?.with_any);return d&&n!==null&&n<=d?100*n/d:null;}},
 {key:'bodies',label:'Classified official bodies',unit:'records',note:'Body classifications recorded in the published coverage matrix. Excludes navigation pages and unreviewed captures.',value:r=>sumKnown(Object.values(r.families||{}).map(f=>f.official_capture?.body))},
 {key:'unreviewed',label:'Official captures awaiting review',unit:'records',note:'Recorded review workload, not a measure of authoritative legal coverage.',value:r=>sumKnown(Object.values(r.families||{}).map(f=>f.official_capture?.unreviewed))},
 {key:'local',label:'Counties with published local resources',unit:'counties',note:'Distinct counties in the saved coverage record; not a current court census.',value:r=>numeric(r.county_layer?.with_published_local_resource)}];
 return m.map(x=>({...x,values:Object.fromEntries(rows.map(r=>[r.abbr,x.value(r)]))}));
}
function dossierCoverage(d){const a=d?.docket_activity,b=d?.docket_documents,entries=numeric(a?.total),documents=numeric(b?.total);return {entries,documents,status:entries===null&&documents===null?'not_recorded':'partial_snapshot',first:a?.date_first||null,last:a?.date_last||null,capped:a?.capped===true,note:'Captured entries and document references are separate inventories. Completeness against an authoritative docket has not been established.'};}
function snapshots(d){
 const a=[...(d?.snapshots||[])],summary=d?.summary;
 if(summary?.as_of&&!a.some(r=>r.as_of===summary.as_of&&numeric(r.actions_pending)===numeric(summary.actions_pending)&&numeric(r.total_actions)===numeric(summary.total_actions)&&(!summary.document_id||r.document_id===summary.document_id)))a.push({...summary,report_kind:'latest_registry'});
 const seen=new Set();
 return a.filter(r=>/^\d{4}-\d{2}-\d{2}$/.test(r.as_of||'')&&Number.isFinite(Date.parse(r.as_of))).map(r=>({asOf:r.as_of,pending:numeric(r.actions_pending),total:numeric(r.total_actions),sourceId:r.document_id||null,reportKind:r.report_kind||null})).filter(r=>{const k=JSON.stringify([r.asOf,r.pending,r.total,r.sourceId]);if(seen.has(k))return false;seen.add(k);return true;}).sort((x,y)=>x.asOf.localeCompare(y.asOf));
}
function family(id){
 if(/^(mdl|mdls$|counsel|expert_rulings|state_proceedings|settlements|verdict_reports)/.test(id))return 'Matters & dockets';
 if(/^(agency|cpsc)/.test(id))return 'Science & safety';
 if(/^(court|judge|people$|counties$|county|trellis|uscourts)/.test(id))return 'Courts & practice';
 if(/^(federal_register|federal_regulations|public_laws|open_us_law|seeger$|indiana_code|sd_statutes|state_codes|provider_laws|limitation_periods)/.test(id))return 'Law & regulation';
 if(/^(sources$|source_documents|url_directory|saved_pages|citation|gap_enrichment|focused$|docsupload|coverage|federal$)/.test(id))return 'Sources & reference';
 return 'Other collections';
}
const routes={sources:'sources',url_directory:'urls',saved_pages:'saved-pages',source_documents:'source-documents',court_documents:'court-documents',court_forms_expansion_20260912:'court-documents',court_spine:'courts',judges:'judges',judge_entities:'judges',judge_enrichment:'judges',judge_disclosures:'judge-disclosures',people:'people',counties:'counties',county_litigation:'counties',county_enrichment_20260928:'additions',mdls:'mdls',mdl_docket_activity:'mdl-activity',mdl_docket_documents:'mdl-documents',mdl_case_inventory:'mdl-cases',mdl_appearances:'mdl-appearances',mdl_counsel:'counsel',counsel_directory:'counsel-directory',state_proceedings:'state-proceedings',settlements:'settlements',verdict_reports:'verdict-reports',expert_rulings:'expert-rulings',federal_register_history:'federal-register',public_laws:'public-laws',indiana_code:'indiana-code',sd_statutes:'sd-statutes',state_codes:'state-codes',seeger:'laws',federal_regulations_documents:'regulations',federal_regulations_parts:'regulations',federal_regulations_sections:'regulations',limitation_periods:'limitation-periods',agency_science_documents:'agency-documents',cpsc_injury_data:'cpsc-injury-data',citation_index:'citation-index',citation_reference:'citation-guide',gap_enrichment_20260927:'additions',docsupload_coverage:'court-coverage',court_statistics:'statistics',uscourts_pages:'uscourts',federal:'federal',focused:'documents'};
function destination(id){return routes[id]||(id.startsWith('agency_safety_')?'agencies':null);}
root.CorpusWorkspaceModel=Object.freeze({numeric,state,navigation,publication,atlasMetrics,dossierCoverage,snapshots,family,destination});
})(globalThis);
