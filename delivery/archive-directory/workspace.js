/* Additive research workspace. Same-origin GETs only; legacy readers remain available. */
(function(){
'use strict';
const M=window.CorpusWorkspaceModel;
if(!M||typeof HUBS==='undefined'||!window.ARCHIVE_AREAS)return;
const cache=new Map(),NS='http://www.w3.org/2000/svg';
const labels={corpus:'Corpus',atlas:'Jurisdiction atlas',matters:'Matter library'};
const node=(tag,cls,text)=>el(tag,cls,text);
const put=(p,...children)=>append(p,...children);
const humanLabel=s=>String(s??'').replace(/_/g,' ').replace(/\bmdl\b/gi,'MDL').replace(/\bfda\b/gi,'FDA').replace(/\bcpsc\b/gi,'CPSC');
const collectionTitles={mdls:'MDL registry',open_us_law:'Open U.S. Law snapshots',seeger:'Seeger law collection',court_spine:'Court registry',judge_entities:'Consolidated judge profiles',judge_enrichment:'Judge source observations',judge_vendor:'Publisher judge analysis',people:'Historical biographies',county_enrichment_20260928:'County rules and orders — Sep 28',court_forms_expansion_20260912:'Court forms — Sep 12 expansion',federal_register_history:'Federal Register history',sd_statutes:'South Dakota statutes',uscourts_pages:'U.S. Courts publications',gap_enrichment_20260927:'Dated source additions',docsupload_coverage:'Court document associations'};
const collectionTitle=id=>collectionTitles[id]||humanLabel(id).replace(/\b[a-z]/g,c=>c.toUpperCase());
const countValue=v=>M.numeric(v)===null?'—':count(M.numeric(v));
const note=t=>node('p','ws-note',t);
const dead=signal=>signal?.aborted;
const hash=(view,params={})=>makeHash(view,params);
function go(view,params){navigate(view,params);}
function routeAnchor(text,view,params={},cls='ws-link'){return routeLink(text,view,params,cls);}
function button(text,fn,cls='button'){return action(text,fn,cls);}
function card(title){const c=node('section','ws-card');if(title)c.append(node('h2','ws-card-title',title));return c;}
function facts(pairs){const d=node('dl','ws-facts');for(const [k,v] of pairs)put(d,node('dt','',k),node('dd','',v==null||v===''?'Not recorded':readable(v)));return d;}
function table(headers,rows){const wrap=node('div','ws-table-wrap'),t=node('table','ws-table'),hr=node('tr');for(const h of headers){const th=node('th','',h);th.scope='col';hr.append(th);}t.append(put(node('thead'),hr));const body=node('tbody');for(const row of rows){const tr=node('tr');for(const cell of row){const td=node('td');if(cell instanceof Node)td.append(cell);else td.textContent=cell==null?'—':String(cell);tr.append(td);}body.append(tr);}t.append(body);wrap.append(t);return wrap;}
function status(text,kind=''){return node('span','ws-status '+kind,text);}
function pageHeader(title,description,trail){document.title=title+' · Seeger Weiss';const h=node('header','page-heading ws-heading');const text=node('div');put(text,node('div','eyebrow',trail||'Research corpus'),node('h1','',title),description?note(description):null);h.append(text);main.append(h);return h;}
function metrics(items){const g=node('div','ws-metrics');for(const [name,value,detail] of items){const c=node('div','ws-metric');put(c,node('span','ws-metric-label',name),node('strong','ws-metric-value',value),detail?node('span','ws-metric-note',detail):null);g.append(c);}return g;}
function tabs(view,current,items,base){const nav=node('nav','ws-tabs');nav.setAttribute('aria-label','Views');for(const [id,title] of items){const a=routeAnchor(title,view,{...base,tab:id,page:''},'ws-tab'+(id===current?' is-active':''));if(id===current)a.setAttribute('aria-current','page');nav.append(a);}return nav;}
function sourceLink(text,url){if(typeof url!=='string'||!url)return null;if(url.startsWith('#')){const a=node('a','ws-link',text);a.href=url;a.addEventListener('click',()=>{if(dialog.open)dialog.close();});return a;}return link(text,url,/^https?:/.test(url),'ws-link');}
async function read(path,signal,{ttl=15000}={}){
 if(!path.startsWith('/api/')||path.startsWith('//'))throw new Error('Unsupported research API path');
 if(dead(signal))throw new DOMException('Navigation cancelled','AbortError');
 const hit=cache.get(path);if(hit&&Date.now()-hit.at<ttl)return structuredClone(hit.data);
 const timeout=AbortSignal.timeout(20000),joined=signal?AbortSignal.any([signal,timeout]):timeout;
 const response=await fetch(path,{method:'GET',credentials:'same-origin',headers:{Accept:'application/json'},signal:joined,cache:'no-store'});
 const data=await response.json().catch(()=>null);
 if(!response.ok||!data){const e=new Error(data?.error||'This source could not be loaded.');e.status=response.status;throw e;}
 if(!dead(signal)&&ttl){if(cache.size>=64)cache.delete(cache.keys().next().value);cache.set(path,{at:Date.now(),data});}
 return data;
}
async function region(target,signal,load){
 try{await load();}catch(e){if(dead(signal)||e.name==='AbortError')return;target.replaceChildren(node('h3','','This section is unavailable'),note(e.message),note('Other published sections remain available. No empty result or zero count is inferred.'));}
}
function searchForm(label,value,onSubmit){const f=node('form','ws-search'),l=node('label','ws-grow',label),input=node('input');input.type='search';input.value=value||'';input.placeholder=label;input.setAttribute('aria-label',label);l.append(input);const b=node('button','button button-primary','Search');b.type='submit';put(f,l,b);f.addEventListener('submit',e=>{e.preventDefault();onSubmit(input.value.trim());});return f;}
function selectField(label,value,options,onChange){const l=node('label','ws-select',label),s=node('select');s.setAttribute('aria-label',label);for(const [v,t] of options){const o=node('option','',t);o.value=v;s.append(o);}s.value=value;s.addEventListener('change',()=>onChange(s.value));l.append(s);return l;}
function pager(target,total,page,size,onPage){const pages=Math.max(1,Math.ceil(total/size)),bar=node('div','ws-pager');const prev=button('Previous',()=>onPage(page-1)),next=button('Next',()=>onPage(page+1));prev.disabled=page<=1;next.disabled=page>=pages;put(bar,note(`Page ${page} of ${count(pages)} · ${count(total)} recorded rows`),put(node('div','button-row'),prev,next));target.append(bar);}

async function openDetail(view,id,trigger){
 if(!/^[a-z0-9-]+$/.test(view))return;
 lastTrigger=trigger;recordController?.abort();recordController=new AbortController();const signal=recordController.signal;
 const body=document.querySelector('#record-body');body.replaceChildren(node('h2','','Opening source record…'));body.firstChild.id='record-title';if(!dialog.open)dialog.showModal();document.querySelector('#close-record').focus();
 await region(body,signal,async()=>{
  const d=await read('/api/area/'+view+'/item?'+new URLSearchParams({id:String(id)}),signal,{ttl:0});if(dead(signal))return;
  body.replaceChildren();const h=node('h2','',cleanTitle(d.title,d.url)||'Source record');h.id='record-title';body.append(h);
  if(d.subtitle)body.append(note(d.subtitle));
  if(d.qualification)body.append(dataNote(d.qualification,'Source scope and restrictions'));
  if(Array.isArray(d.facts))body.append(facts(d.facts.map(f=>Array.isArray(f)?f:[f.label,f.value])));
  for(const section of d.sections||[]){const c=card(section.heading||'Saved evidence');if(section.text)c.append(node('p','ws-reader-text',section.text));
   if(Array.isArray(section.rows)){c.append(table(section.header||[],section.rows.slice(0,100)));if(section.rows.length>100)c.append(note('Showing 100 rows from this detail section. Open the collection for the remaining source records.'));}
   for(const it of (section.items||[]).slice(0,50)){const b=node('div','ws-related-item');put(b,node('strong','',it.title||'Source item'),it.subtitle?note(it.subtitle):null);for(const l of it.links||[])put(b,sourceLink(l.label||'Open source',l.url));c.append(b);}body.append(c);
  }
  if(d.text)body.append(node('div','ws-reader-text',d.text));
  const links=node('div','ws-link-row');for(const l of d.links||[])put(links,sourceLink(l.label||'Open original source',l.url));body.append(links);
  body.append(routeAnchor('Open this source collection',view,{},'button'));
 });
 if(!body.querySelector('#record-title')&&body.firstElementChild)body.firstElementChild.id='record-title';
}
function rowTitle(r,view){const c=node('div'),b=button(cleanTitle(r.title,r.url)||r.id,()=>openDetail(view,r.id,b),'ws-record-button');c.append(b);if(r.subtitle)c.append(node('p','ws-excerpt',r.subtitle));return c;}
async function genericListing(target,view,params,signal,{limit=25,pagination=true}={}){
 const current=M.state(new URLSearchParams(params)),page=current.page;
 const data=await read('/api/area/'+view+'?'+new URLSearchParams({...params,limit:String(limit),page:String(page)}),signal);if(dead(signal))return;
 if(!data.available){target.append(note(data.reason||'This collection is not published.'));return;}
 const head=node('div','ws-section-head');put(head,node('h2','',`${countValue(data.total)}${data.total_capped?'+':''} source records`),routeAnchor('Open collection',view,params));target.append(head);
 const cols=(data.columns||[]).filter(c=>!['mdl'].includes(c.key));const sample=data.results?.[0];
 const actualCols=cols.filter((c,i)=>!(i===0&&sample&&(sample.cells?.[c.key]===sample.title||sample.cells?.[c.key]===undefined)));
 if(!data.results?.length)target.append(note('No records returned for this exact selection. This does not establish that the underlying docket or court has no records.'));
 else target.append(table(['Record',...actualCols.map(c=>view==='mdl-activity'&&c.key==='published_at'?'Entered date (parsed)':c.label)],data.results.map(r=>[rowTitle(r,view),...actualCols.map(c=>r.cells?.[c.key])])));
 if(data.qualification)target.append(dataNote(data.qualification,'Source scope, dates and restrictions'));
 if(pagination&&M.numeric(data.total)!==null&&data.total>limit)pager(target,data.total,page,limit,p=>go('matters',{...Object.fromEntries(route.params),page:p}));
}

async function corpus(signal){
 const h=pageHeader('Corpus','Explore the published collections. Source records, document bodies and research locators remain distinguishable.');h.append(status('Source-linked research'));
 const root=node('div','ws-root');main.append(root);
 root.append(searchForm('Search the core document reader','',q=>go('documents',{q})),note('The core reader does not federate every supplemental collection. Use the collection-specific search for docket, agency and scientific data.'));
 if(window.ARCHIVE_PUBLICATION_PROMISE)await window.ARCHIVE_PUBLICATION_PROMISE;
 const health=window.ARCHIVE_PUBLICATION?.datasets?window.ARCHIVE_PUBLICATION:await read('/api/health',signal);if(dead(signal))return;
 const p=M.publication(health),rows=health.datasets||[];
 root.append(metrics([['Published collections',`${p.publishedCollections} / ${p.totalCollections}`,'Individual publication gates retained'],['Published catalog rows',countValue(p.publishedRecords),'Not a count of unique documents'],['Held catalog rows',countValue(p.heldRecords),'Not added to searchable coverage']]));
 if(p.unknownCounts)root.append(note(`${p.unknownCounts} collections lack a numeric count. Totals sum only reported counts and are not complete inventory totals.`));
 const layout=node('div','ws-corpus-layout'),left=node('section'),right=node('aside','ws-inspector');put(layout,left,right);root.append(layout);
 const head=node('div','ws-section-head');put(head,node('h2','','Collection explorer'),status(health.release_scope==='full'?'Published release':'Partial release','is-warning'));left.append(head);
 const familyOptions=[...new Set(rows.map(r=>M.family(r.id)))].sort();const toolbar=node('div','ws-toolbar');let selected='',query='';
 const results=node('div');
 function paint(){results.replaceChildren();const found=rows.filter(r=>(!selected||M.family(r.id)===selected)&&(!query||[collectionTitle(r.id),r.id].join(' ').toLowerCase().includes(query)));for(const family of familyOptions){const group=found.filter(r=>M.family(r.id)===family);if(!group.length)continue;const detail=node('details','ws-collection-group');detail.open=Boolean(selected||query)||family==='Law & regulation';const summary=node('summary');put(summary,node('strong','',family),node('span','',`${group.length} collections`));detail.append(summary);
  const list=group.sort((a,b)=>Number(b.ready)-Number(a.ready)||a.id.localeCompare(b.id)).map(r=>{const routeName=M.destination(r.id),title=collectionTitle(r.id);const a=r.ready&&routeName?routeAnchor(title,routeName,{},'ws-collection-link'):node('span','',title);return [a,countValue(r.records),status(r.ready?'Published':'Held',r.ready?'':'is-warning')];});detail.append(table(['Collection','Catalog rows','Availability'],list));results.append(detail);}if(!found.length)results.append(note('No collections match these filters.'));}
 toolbar.append(selectField('Collection family','',[['','All families'],...familyOptions.map(x=>[x,x])],v=>{selected=v;paint();}));const filter=node('label','ws-grow','Filter collection names'),input=node('input');input.type='search';input.placeholder='Filter by collection name';input.setAttribute('aria-label','Filter collection names');input.addEventListener('input',()=>{query=input.value.trim().toLowerCase();paint();});filter.append(input);toolbar.append(filter);put(left,toolbar,results);paint();
 const coverage=card('Coverage, not just volume');put(coverage,note('Registered → acquired → extracted → anchored → reviewed → published'),note('A published catalog record can still be a URL, metadata row or historical observation. Review the source detail before relying on its content.'),routeAnchor('Inspect jurisdiction coverage','atlas'),routeAnchor('Inspect source references','sources'),routeAnchor('Explore recorded connections','connections'));right.append(coverage);
 const pending=card('Publication gaps');const held=rows.filter(r=>!r.ready);for(const r of held.slice(0,9))put(pending,node('div','ws-gap-row',`${collectionTitle(r.id)} · ${countValue(r.records)} rows held`));pending.append(note('These gates are not changed by the interface. Counts alone do not authorize publication.'));right.append(pending);
 const lanes=card('Research paths');for(const [title,view] of [['Court rules & forms','court-documents'],['State coordinated proceedings','state-proceedings'],['Agency & science documents','agency-documents'],['Recall and approval data','agencies'],['Dated source additions','additions']])lanes.append(routeAnchor(title,view));right.append(lanes);
}

function svg(tag,attrs={},text){const x=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))x.setAttribute(k,String(v));if(text!==undefined)x.textContent=String(text);return x;}
function focusable(x,label,fn){x.setAttribute('tabindex','0');x.setAttribute('role','link');x.setAttribute('aria-label',label);x.addEventListener('click',fn);x.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();fn();}});}
async function mapPanel(target,rows,metric,scope,signal,countyRows){
 if(!window.UsMap){target.append(note('Map geometry is unavailable. Use the jurisdiction selector.'));return;}
 const g=await window.UsMap.load();if(dead(signal))return;
 const stateShape=scope.state&&Object.values(g.states).find(r=>r.usps===scope.state);const zoom=route.params.get('zoom')==='county'&&stateShape;
 let box='0 0 975 610';if(zoom){const [x0,y0,x1,y1]=stateShape.box,pad=Math.max(x1-x0,y1-y0)*.06;box=[x0-pad,y0-pad,x1-x0+2*pad,y1-y0+2*pad].join(' ');}
 const s=svg('svg',{viewBox:box,role:'img','aria-label':zoom?`Counties in ${stateShape.name}`:`United States: ${metric.label}`});s.classList.add('ws-map');
 const defs=svg('defs'),pattern=svg('pattern',{id:'ws-unknown',width:8,height:8,patternUnits:'userSpaceOnUse'});put(pattern,svg('rect',{width:8,height:8,fill:'#f1f3f6'}),svg('path',{d:'M-2 2L2-2M0 8L8 0M6 10L10 6',stroke:'#c3cad5','stroke-width':1}));defs.append(pattern);s.append(defs);
 const ramp=['#eef2f7','#dce5ef','#b8cada','#8ca7c1','#54779c','#172e4c'];const max=metric.unit==='%'?100:Math.max(1,...Object.values(metric.values).map(x=>M.numeric(x)||0));
 if(zoom){const info=new Map((countyRows?.counties||[]).map(r=>[r.fips,r]));const levels={rules_and_filing:5,statewide_rules_and_filing:3,rules_only:2,filing_only:1,none:0};for(const [id,c] of Object.entries(g.counties)){if(c.state!==window.UsMap.USPS_FIPS[scope.state])continue;const r=info.get(id),i=r?levels[r.level]:null;const p=svg('path',{d:c.d,fill:i==null?'url(#ws-unknown)':ramp[i],class:'ws-map-area','stroke-width':.5});const label=`${r?.name||c.name}: ${r?humanLabel(r.level):'coverage not recorded'}`;p.append(svg('title',{},label));focusable(p,label,()=>go('county/'+id,{state:stateShape.name,county:id,from:location.hash}));s.append(p);}}
 else for(const st of Object.values(g.states)){if(!st.usps)continue;const value=M.numeric(metric.values[st.usps]);const i=value===null?null:value===0?0:Math.max(1,Math.min(5,Math.ceil(value/max*5)));const p=svg('path',{d:st.d,fill:i===null?'url(#ws-unknown)':ramp[i],class:'ws-map-area'+(st.usps===scope.state?' is-selected':'')});const label=`${st.name}: ${value===null?'not recorded':metric.unit==='%'?value.toFixed(1)+'%':count(value)+' '+metric.unit}`;p.append(svg('title',{},label));focusable(p,label,()=>go('atlas',{state:st.usps,metric:metric.key,tab:'overview'}));s.append(p);}
 target.append(s);const legend=node('div','ws-legend');put(legend,node('span','ws-legend-unknown','Hatched: not recorded'),node('span','',zoom?'Shade: filing-source coverage only':metric.unit==='%'?'Scale: 0–100%, fixed intervals':`Scale: 0–${count(max)} ${metric.unit}, equal intervals`));target.append(legend);target.append(note(zoom?'County geometry is an access path, not court jurisdiction. A source listing is not a downloaded rulebook.':metric.note));
}
async function atlas(signal){
 const scope=M.state(route.params);pageHeader('Jurisdiction atlas','Navigate geography, courts and source coverage without losing jurisdiction context.');const root=node('div','ws-root');main.append(root);
 const matrix=await read('/api/coverage/matrix',signal);if(dead(signal))return;if(matrix.available===false){root.append(note('Jurisdiction coverage has not been published.'));return;}
 let filing=null;try{filing=await read('/api/county-filing/coverage',signal);}catch(e){if(dead(signal))return;root.append(note('Filing-source coverage could not be loaded; its map values remain unknown.'));}
 const rows=matrix.rows||[],selected=rows.find(r=>r.abbr===scope.state),metricsList=M.atlasMetrics(rows,filing),metric=metricsList.find(m=>m.key===scope.metric)||metricsList[0];
 const controls=node('div','ws-toolbar');controls.append(selectField('Jurisdiction',scope.state,[['','United States'],...rows.map(r=>[r.abbr,r.name])],v=>go('atlas',{state:v,metric:metric.key,tab:'overview'})));controls.append(selectField('Map layer',metric.key,metricsList.map(m=>[m.key,m.label]),v=>go('atlas',{state:scope.state,metric:v,tab:scope.tab})));if(selected)controls.append(routeAnchor(route.params.get('zoom')==='county'?'National map':'Zoom to counties','atlas',{state:scope.state,metric:metric.key,tab:scope.tab,zoom:route.params.get('zoom')==='county'?'':'county'},'button'));root.append(controls);
 const layout=node('div','ws-atlas-layout'),map=card(selected?selected.name:'United States'),inspector=node('aside','ws-inspector');put(layout,map,inspector);root.append(layout);
 let countyRows=null;if(selected&&route.params.get('zoom')==='county')try{countyRows=await read('/api/county-filing/state-counties?'+new URLSearchParams({state:selected.abbr}),signal);}catch(e){if(dead(signal))return;map.append(note('County coverage failed to load; no coverage is inferred.'));}
 await region(map,signal,()=>mapPanel(map,rows,metric,scope,signal,countyRows));if(dead(signal))return;
 if(!selected){const box=card('Choose a jurisdiction');put(box,note('Select a state on the map or use the selector. The inspector connects its court registry, statutes, local practice resources and recorded coverage gaps.'),note('Territories without geometry remain available in the selector. No federal district boundaries are fabricated.'),routeAnchor('Federal court registry','courts',{system:'federal'}));inspector.append(box);}
 else{
  const box=card(selected.name);inspector.append(box);const active=['overview','courts','resources','quality'].includes(scope.tab)?scope.tab:'overview';box.append(tabs('atlas',active,[['overview','Overview'],['courts','Courts'],['resources','Resources'],['quality','Gaps']],{state:selected.abbr,metric:metric.key,zoom:route.params.get('zoom')||''}));
  if(active==='overview'){box.append(facts([['Listed county-equivalents',countValue(selected.county_layer?.counties_total)],['Counties with published resources',countValue(selected.county_layer?.with_published_local_resource)],['Metric value',metric.values[selected.abbr]===null?'Not recorded':metric.unit==='%'?metric.values[selected.abbr].toFixed(1)+'%':countValue(metric.values[selected.abbr])],['Coverage generated',matrix.generated_at?date(matrix.generated_at):'Not recorded']]));box.append(table(['Family','Body','Review'],Object.entries(selected.families||{}).map(([name,f])=>[humanLabel(name),countValue(f.official_capture?.body),countValue(f.official_capture?.unreviewed)])));box.append(note('Body = classified official bodies. Review = unreviewed official captures. Other source tiers remain in the detailed state view.'));}
  if(active==='courts')await region(box,signal,()=>genericListing(box,'courts',{state:selected.abbr},signal,{limit:15,pagination:false}));
  if(active==='resources'){for(const [title,view,params] of [['Statutes, rules & constitutions','laws',{state:selected.name}],['Judges','judges',{state:selected.name}],['County resources','counties',{state:selected.name}],['Court rules and forms','court-documents',{state:selected.abbr}],['Source directory','sources',{jurisdiction:selected.abbr.toLowerCase()}],['Official resource hierarchy','resources',{state:selected.abbr}],['Source evidence connections','connections',{entity:'state:'+selected.abbr}]])box.append(routeAnchor(title,view,params));box.append(note('Scope is carried in these links. A geographic link does not establish that a rule governs a matter.'));}
  if(active==='quality'){for(const gap of selected.gaps||[])box.append(node('div','ws-gap-row',humanLabel(gap)));if(!(selected.gaps||[]).length)box.append(note('No gap flags were recorded in this snapshot; this is not a completeness finding.'));if(selected.pending_inventory?.open_us_law)box.append(dataNote(JSON.stringify(selected.pending_inventory.open_us_law,null,2),'Held law inventory'));box.append(note('Saved gap flags may predate later additions. Validate against the latest accepted artifacts before scheduling new acquisition.'));}
  box.append(routeAnchor('Open full jurisdiction dossier','state/'+selected.abbr,{},'button'));
 }
 root.append(dataNote(matrix.qualification,'Coverage methodology and limitations'));
}

async function matterList(root,signal,scope){
 const response=await read('/api/mdls?'+new URLSearchParams({status:scope.status,limit:'200',sort:'mdl_number'}),signal);if(dead(signal))return;
 root.append(metrics([['Registered MDLs',countValue(response.total),response.counts_label||'As listed in the source report'],['Registry date',response.as_of?date(response.as_of):'Not recorded','Not a live docket refresh'],['Coverage model','Snapshot','Matter, docket and document layers are separate']]));
 const controls=node('div','ws-toolbar');controls.append(selectField('Registry status',scope.status,[['pending','Pending as reported'],['terminated','Terminated as reported'],['all','All source rows']],v=>go('matters',{status:v})));controls.append(routeAnchor('State coordinated proceedings','state-proceedings',{},'button'));root.append(controls);
 root.append(searchForm('Search this MDL directory',scope.q,q=>go('matters',{q,status:scope.status})));
 const all=(response.results||[]).filter(r=>!scope.q||[r.title,r.master_docket,r.mdl_number,r.court_name,r.litigation_type].join(' ').toLowerCase().includes(scope.q.toLowerCase()));
 const page=Math.min(scope.page,Math.max(1,Math.ceil(all.length/25))),selected=all.slice((page-1)*25,page*25);
 if(!selected.length)root.append(note('No registry rows match this search. This is not a search of every docket or filing.'));
 else root.append(table(['Matter','Court / master docket','Reported pending','Source status'],selected.map(r=>{const title=node('div');put(title,routeAnchor(cleanTitle(r.title)||'MDL '+r.mdl_number,'matters',{mdl:r.mdl_number},'ws-record-button'),node('p','ws-excerpt',`MDL ${r.mdl_number} · ${r.litigation_type||'Type not recorded'}`));const court=node('div');put(court,node('strong','',r.court_name||'Court not recorded'),node('p','ws-excerpt',r.master_docket||'Master docket not recorded'));return [title,court,countValue(r.actions_pending),humanLabel(r.status)];})));
 if(all.length>25)pager(root,all.length,page,25,p=>go('matters',{status:scope.status,q:scope.q,page:p}));
 if(response.total>(response.results||[]).length)root.append(note(`The API returned ${response.results.length} of ${response.total} registry rows. Open the full registry to browse remaining rows.`));
 root.append(dataNote(response.qualification,'Registry provenance and limitations'));
}
function timeline(target,detail){
 const rows=M.snapshots(detail);if(!rows.length){target.append(note('No dated snapshots are recorded for this matter. A trend is not inferred.'));return;}
 target.append(node('h2','ws-card-title','Observed pending-action snapshots'));
 const points=rows.filter(r=>r.pending!==null),chart=svg('svg',{viewBox:'0 0 760 235',role:'img','aria-label':'Reported pending actions at observed report dates'});chart.classList.add('ws-trend');
 if(points.length){const times=points.map(r=>Date.parse(r.asOf)),lo=Math.min(...times),hi=Math.max(...times),max=Math.max(1,...points.map(r=>r.pending));const X=t=>75+610*(hi===lo ? 0.5 : (t-lo)/(hi-lo)),Y=n=>175-130*n/max;
  for(let i=0;i<=4;i++){const n=max*i/4;put(chart,svg('line',{x1:75,x2:710,y1:Y(n),y2:Y(n),class:'ws-grid-line'}),svg('text',{x:65,y:Y(n)+4,'text-anchor':'end',class:'ws-svg-label'},Math.round(n).toLocaleString()));}
  for(const r of points){const x=X(Date.parse(r.asOf)),c=svg('circle',{cx:x,cy:Y(r.pending),r:5,class:'ws-chart-point'});c.append(svg('title',{},`${r.asOf}: ${count(r.pending)} reported pending actions`));put(chart,c,svg('text',{x,y:202,'text-anchor':'middle',class:'ws-svg-label'},r.asOf));}
  target.append(chart);
 }
 target.append(note('Dots are reported observations, not an interpolated monthly series. Changes in pending actions are not counts of new filings and do not establish causation.'));
 target.append(table(['Report date','Pending','Total actions','Source'],rows.map(r=>[r.asOf,countValue(r.pending),countValue(r.total),r.sourceId?sourceLink(r.sourceId,'/mdl-files/'+encodeURIComponent(r.sourceId)):r.reportKind||'Source in registry provenance'])));
}
async function connections(target,detail,signal){
 const data=await read('/api/enrichment/graph?'+new URLSearchParams({entity:'mdl:'+detail.mdl_number,limit:'60',related:'1'}),signal);
 if(dead(signal))return;const local=detail.edges||[],edges=[...local,...(data.available===false?[]:data.edges||[])];
 const id=v=>typeof v==='object'?v?.id||'':String(v||'');const normalized=edges.map(e=>({from:id(e.source||e.from),to:id(e.target||e.to),relation:e.relation||'Recorded association',evidence:e.evidence||e.provenance||{},basis:e.basis||'Source-reported connection'}));
 const seen=new Set(),unique=normalized.filter(e=>{const k=JSON.stringify(e);if(seen.has(k))return false;seen.add(k);return true;});
 if(!unique.length){target.append(note('No recorded edge paths were returned for this matter. Similarity matches are not substituted.'));return;}
 target.append(node('h2','ws-card-title','Recorded evidence connections'));const proof=card('Connection evidence'),center='mdl:'+detail.mdl_number,visible=unique.filter(e=>e.from===center||e.to===center).slice(0,8);
 const chart=svg('svg',{viewBox:`0 0 760 ${Math.max(180,visible.length*55+30)}`,role:'img','aria-label':'Recorded matter relationships'});chart.classList.add('ws-graph');const cy=Math.max(90,visible.length*55/2);
 put(chart,svg('circle',{cx:92,cy,r:42,class:'ws-graph-center'}),svg('text',{x:92,y:cy+4,'text-anchor':'middle',class:'ws-graph-center-label'},'MDL '+detail.mdl_number));
 visible.forEach((e,i)=>{const y=35+i*55,other=e.from===center?e.to:e.from;chart.append(svg('path',{d:`M134 ${cy} C250 ${cy} 310 ${y} 432 ${y}`,class:'ws-graph-edge'}));const group=svg('g',{class:'ws-graph-node'});put(group,svg('rect',{x:432,y:y-18,width:305,height:38,rx:5}),svg('text',{x:444,y:y-3,class:'ws-svg-label'},humanLabel(e.relation).slice(0,45)),svg('text',{x:444,y:y+11,class:'ws-svg-label'},other.length>47?other.slice(0,44)+'…':other));group.append(svg('title',{},`${e.from} → ${e.relation} → ${e.to}`));focusable(group,'Inspect '+e.relation,()=>{proof.replaceChildren(node('h2','ws-card-title','Connection evidence'),facts([['From',e.from],['Relation',e.relation],['To',e.to],['Basis',e.basis]]),node('pre','ws-proof',JSON.stringify(e.evidence,null,2)));});chart.append(group);});
 if(visible.length)target.append(chart);proof.append(note('Select a graph node to inspect its exact relationship and supporting evidence.'));target.append(proof);
 target.append(note(`Showing ${unique.length} returned relationship observations; the graph displays up to eight direct paths. Recorded associations do not establish legal applicability or complete MDL membership.`));
 target.append(table(['From','Relationship','To','Basis'],unique.map(e=>[e.from,humanLabel(e.relation),e.to,e.basis])));target.append(routeAnchor('Open full evidence explorer','connections',{entity:center}));if(data.qualification)target.append(dataNote(data.qualification));
}
async function dossier(root,signal,scope){
 const d=await read('/api/mdl?'+new URLSearchParams({number:scope.mdl}),signal);if(dead(signal))return;
 const c=M.dossierCoverage(d),s=d.summary||d;const header=node('div','ws-dossier-heading');put(header,routeAnchor('← Matter library','matters'),node('h2','ws-matter-title',cleanTitle(d.title)||'MDL '+scope.mdl),note(`MDL ${scope.mdl} · ${d.court_name||'Court not recorded'} · ${d.master_docket||'Master docket not recorded'}`));root.append(header);
 root.append(metrics([['Reported pending actions',countValue(d.actions_pending),s.as_of?'JPML report '+s.as_of:'Report date in source detail'],['Captured docket entries',countValue(c.entries),c.last?'Last parsed entry date: '+c.last:'Date range not recorded'],['Document references',countValue(c.documents),'Not necessarily downloaded originals']]));
 const current=['overview','docket','documents','participants','connections','trends'].includes(scope.tab)?scope.tab:'overview';root.append(tabs('matters',current,[['overview','Overview'],['docket','Docket'],['documents','Documents'],['participants','Participants'],['connections','Connections'],['trends','Trends']],{mdl:scope.mdl}));
 root.append(put(node('div','ws-coverage-banner'),status(c.status==='not_recorded'?'Coverage not recorded':'Partial captured coverage','is-warning'),node('span','',c.note)));
 const body=card();root.append(body);
 if(current==='overview'){
  const grid=node('div','ws-two-col'),a=card('Matter identity'),b=card('Source and acquisition status');
  a.append(facts([['Master docket',d.master_docket],['Transferee court',d.court_name],['Judge as reported',d.transferee_judge?.name_as_printed||s.judge_name_as_printed],['Litigation type',d.litigation_type],['Registry status',d.status],['Report as of',s.as_of||d.temporal?.source_as_of]]));
  for(const j of d.judge_links||[])if(j.entity_id)a.append(routeAnchor(j.display_name||j.fjc_name||'Judge profile','judge/'+encodeURIComponent(j.entity_id)));
  b.append(facts([['Captured entries',countValue(c.entries)],['First parsed entry date',c.first],['Last parsed entry date',c.last],['Source-reported capture cap',c.capped?'Cap or gap flagged':'No cap flag; completeness still unverified'],['Original-file coverage','Must be reconciled per document'],['Currentness','Check the current authoritative docket']]));put(grid,a,b);body.append(grid);
  const links=node('div','ws-link-row');put(links,routeAnchor('Legacy MDL source dossier','mdl/'+scope.mdl),routeAnchor('Member-case sample','mdl-cases',{mdl:scope.mdl}),routeAnchor('Appearance records','mdl-appearances',{mdl:scope.mdl}),routeAnchor('Saved matter additions','additions',{mdl:scope.mdl}));body.append(links);
  for(const [label,block] of [['Docket activity source',d.docket_activity],['Document references source',d.docket_documents],['Member-case source',d.cases]])if(block?.qualification)body.append(dataNote(block.qualification,label));
 }
 if(current==='docket'||current==='documents'||current==='participants'){
  const view={docket:'mdl-activity',documents:'mdl-documents',participants:'counsel-directory'}[current];body.append(searchForm(current==='docket'?'Search captured docket text':'Search this matter’s '+current,scope.q,q=>go('matters',{mdl:scope.mdl,tab:current,q})));
  if(current==='docket')body.append(note('The source date may be parsed from an “Entered” description; it is not silently relabeled as the filing or effective date. Use the source reader for its basis.'));
  await region(body,signal,()=>genericListing(body,view,{mdl:scope.mdl,q:scope.q,page:String(scope.page)},signal));
 }
 if(current==='connections')await region(body,signal,()=>connections(body,d,signal));
 if(current==='trends')timeline(body,d);
}
async function matters(signal){const scope=M.state(route.params);pageHeader('Matter library','Matter context, captured dockets, participants and evidence — without conflating source inventories.');const root=node('div','ws-root');main.append(root);if(route.params.has('mdl')&&!scope.mdl){root.append(note('Invalid matter identifier. Select an exact MDL registry entry.'),routeAnchor('Browse matters','matters'));return;}if(scope.mdl)await dossier(root,signal,scope);else await matterList(root,signal,scope);}

// Keep source readers and public API contracts intact; replace only the navigation projection.
HUBS.splice(0,HUBS.length,...M.navigation(HUBS));
const nativeHubTabs=renderHubTabs;
renderHubTabs=function(active){nativeHubTabs(active);for(const id of Object.keys(labels)){const a=document.querySelector('#hub-tabs a[href="#'+id+'"]');a?.querySelector('.publication-pending-label')?.remove();if(a)a.removeAttribute('title');}};
for(const [view,renderPage] of Object.entries({corpus,atlas,matters}))window.ARCHIVE_AREAS[view]={title:labels[view],nav:labels[view],render:renderPage};
document.body.classList.add('sw-workspace');
const mainNav=document.querySelector('.hub-nav');mainNav.replaceChildren();
const icons={corpus:'M4 4h16v16H4z M8 4v16 M11 8h6 M11 12h6',atlas:'M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2z M9 3v16 M15 5v16',matters:'M3 7h18v13H3z M8 7V4h8v3 M3 12h18'};
for(const [id,label] of Object.entries(labels)){const a=routeAnchor(label,id,{},'');a.dataset.hub=id;const icon=svg('svg',{viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor','stroke-width':1.6,'aria-hidden':'true'});icon.append(svg('path',{d:icons[id],'stroke-linejoin':'round','stroke-linecap':'round'}));a.prepend(icon);mainNav.append(a);}
const brand=document.querySelector('.brand');if(brand){brand.href='#corpus';brand.setAttribute('aria-label','Seeger Weiss corpus home');brand.replaceChildren(node('span','brand-mark','SW'),put(node('span','','Seeger Weiss'),node('small','','CORPUS & RESEARCH')));}
const navLabel=document.querySelector('.nav-label');if(navLabel)navLabel.textContent='Research infrastructure';
const bottom=document.querySelector('.sidebar-bottom');if(bottom)bottom.replaceChildren(node('span','local-label','Source-linked research'),note('Published content, source dates and research locators remain distinct.'));
if(!location.hash)history.replaceState(null,'',location.pathname+location.search+'#corpus');
if(new URLSearchParams(location.search).get('embedded')==='1')document.body.classList.add('ws-embedded');
// Collapse the redundant release navigation, not its warning or availability counts.
const release=document.querySelector('#publication-notice');if(release){const compact=()=>{const nav=release.querySelector(':scope > .publication-links');if(!nav)return;const d=node('details','ws-release-details');d.append(node('summary','','Published views'),nav);release.append(d);};new MutationObserver(compact).observe(release,{childList:true});compact();}
window.CorpusWorkspace=Object.freeze({read,openDetail,version:'2026-09-30',clearCache:()=>cache.clear()});
})();
