'use strict';

// The directory renders saved data only. Source titles, text and metadata are
// always inserted with textContent, never interpreted as markup or instructions.
const views = {
  overview: {title: 'Your research starts here', nav: 'Home'},
  laws: {title: 'State law', group: 'laws', description: 'Find a provision, explore a state, or browse the rules that matter to your research.'},
  counties: {title: 'Counties', description: 'Explore local courts, rules, forms and county resources by state.'},
  county: {title: 'County resources', nav: 'Counties', group: 'all'},
  judges: {title: 'Judges', group: 'judges', description: 'Find a judge and explore their biography, career and available analysis.'},
  judge: {title: 'Judge profile', nav: 'Judges'},
  people: {title: 'Historical biographies', nav: 'Historical biographies'},
  person: {title: 'Historical biography', nav: 'Historical biography'},
  federal: {title: 'Federal sources', group: 'federal', description: 'Explore collected federal court and government resources, with saved evidence and links to the original publishers.'},
  documents: {title: 'All documents', group: 'all', description: 'Search your library and open a document to read.'},
  sources: {title: 'Source directory', description: 'Find legal publishers, court websites, public datasets and access references.'},
  source: {title: 'Source reference', nav: 'Source directory'},
  collections: {title: 'Curated collections', description:'Explore saved case material, reference collections and court directories.'},
  collection: {title:'Collection',nav:'Curated collections'},
  record: {title: 'Saved record', nav: 'All documents', group: 'all', description: 'Search your library and open a document to read.'}
};
const main = document.querySelector('#main');
const dialog = document.querySelector('#record-dialog');
const number = new Intl.NumberFormat('en-US');
let route = readRoute();
let summary = null;
let pageController;
let summaryController;
let recordController;
let filterTimer;
let lastTrigger;
let pageSequence = 0;
let sourceDirectoryFacets = {};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function append(parent, ...children) { for (const child of children) if (child) parent.append(child); return parent; }
function count(value) { return value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : number.format(Number(value)); }
function human(value) { return String(value ?? '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim(); }
function readable(value, depth = 0) {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value !== 'object') return String(value);
  if (depth > 5) return '';
  if (Array.isArray(value)) return value.map(v=>readable(v,depth+1)).filter(Boolean).join('; ');
  for (const key of ['literal_text','text','as_reported']) if (value[key]) return readable(value[key],depth+1);
  return Object.entries(value).filter(([,v])=>v !== null && v !== '').map(([k,v])=>`${human(k)}: ${readable(v,depth+1)}`).filter(v=>!v.endsWith(': ')).join(' · ');
}
function datasetName(value) { return ({open_us_law:'Open US Law',seeger:'Seeger law collection',judge_entities:'Consolidated judge profiles',judge_enrichment:'Judge source observations',judge_vendor:'Publisher analysis',provider_laws:'Provider law collection',pending_publication:'Awaiting publication',trellis_browser_counties:'County court profile snapshots'})[value] || human(value); }
function dateValue(value) {
  if(value&&typeof value==='object'&&!(value instanceof Date))value=value.value;
  return typeof value==='string'||typeof value==='number'||value instanceof Date?value:null;
}
function date(value, withTime = false) {
  value=dateValue(value);
  if (!value) return 'Not recorded';
  if(/^\d{4}$/.test(String(value)))return String(value);
  if(/^\d{4}-\d{2}$/.test(String(value))){const partial=new Date(`${value}-01T12:00:00Z`);return Number.isNaN(partial.getTime())?String(value):partial.toLocaleDateString('en-US',{month:'short',year:'numeric',timeZone:'UTC'});}
  if(/^\d{4}-\d{2}-\d{2}$/.test(String(value))){const calendar=new Date(`${value}T12:00:00Z`);return Number.isNaN(calendar.getTime())?String(value):calendar.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});}
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString('en-US', withTime ? {month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'} : {month:'short',day:'numeric',year:'numeric'});
}
function evidenceDates(sourceAsOf,savedAt) {
  const row=el('dl','evidence-dates');
  append(row,el('dt','','Source as of'),el('dd','',sourceAsOf?date(sourceAsOf):'Unknown'),el('dt','','Saved'),el('dd','',savedAt?date(savedAt):'Unknown'));
  return row;
}
function announce(text) { document.querySelector('#announcement').textContent = text; }
function badge(text, style = '') { return el('span', `badge ${style}`, text); }
// ---- Display-only text helpers. They never change stored data and never add words. ----
// Trim a qualification to one short muted line, cut at a sentence end where possible.
function sentenceClip(value, max = 110) {
  const s = String(value ?? '').replace(/\s+/g, ' ').trim(); if (!s || s.length <= max) return s;
  const cut = s.slice(0, max), stop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('; '), cut.lastIndexOf(' — '));
  return stop > 40 ? cut.slice(0, stop + 1) : cut.replace(/\s+\S*$/, '') + '…';
}
// One collapsed "About this data" line; the full text is shown only when opened.
function dataNote(text, label = 'About this data') {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim(); if (!t) return null;
  const box = el('details', 'data-note'); append(box, el('summary', '', label), el('p', '', t)); return box;
}
// A coverage note: one short muted line, the rest behind the same disclosure.
function footnote(text) {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim(); if (!t) return null;
  const short = sentenceClip(t, 110);
  if (short === t) { const p = el('p', 'muted-note', t); return p; }
  const box = el('details', 'data-note inline-note'); append(box, el('summary', '', short), el('p', '', t)); box.title = t; return box;
}
// File-name-like text to readable words: drop the extension, undo URL-encoding, open up underscore and hyphen runs.
function fileWords(name) {
  let s = String(name ?? '').trim(); if (!s) return '';
  if (/%[0-9A-Fa-f]{2}/.test(s)) { try { s = decodeURIComponent(s); } catch { /* keep the raw name */ } }
  s = s.replace(/\.(pdf|docx?|rtf|xlsx?|pptx?|txt|html?|csv|xml|json|zip)$/i, '');
  if (/_/.test(s) || !/\s/.test(s)) s = s.replace(/[_\-]+/g, ' '); else s = s.replace(/_+/g, ' ').replace(/-{2,}/g, ' ');
  return s.replace(/\s+/g, ' ').trim();
}
// A bare URL shown as host + its last path segment.
function urlDisplay(value) {
  try { const u = new URL(String(value)); const host = u.hostname.replace(/^www\./i, ''); const tail = fileWords(u.pathname.split('/').filter(Boolean).pop() || '');
    return tail && !/^[0-9a-f]{16,}$/i.test(tail) ? `${host} — ${tail}` : host; } catch { return ''; }
}
const TITLE_PLACEHOLDER = /^\(?\s*title not (?:yet )?extracted\s*\)?$/i;
// Display-only title cleaner used by listings, related blocks and detail headers.
function cleanTitle(value, sourceUrl) {
  let t = String(value ?? '').replace(/\s+/g, ' ').trim();
  t = t.replace(/\s*[·|–—-]\s*HTTP\s*\d{3}\s*$/i, '').trim();
  t = t.replace(/^Microsoft Word\s*-\s*/i, '').replace(/\s*\[OCR\]$/i, '').trim();
  // "Name.pdf (title not yet extracted)" keeps the file name and drops the placeholder.
  t = t.replace(/\s*[([]?\s*title not (?:yet )?extracted\s*[)\]]?\s*$/i, '').trim();
  if (!t || TITLE_PLACEHOLDER.test(t)) return sourceUrl ? urlDisplay(sourceUrl) : '';
  if (/^https?:\/\//i.test(t)) return urlDisplay(t) || t;
  const pipe = t.lastIndexOf(' | ');
  if (pipe >= 12 && t.length - pipe - 3 <= 60) t = t.slice(0, pipe).trim();
  const dash = t.lastIndexOf(' - ');
  if (dash >= 12 && /(\.(gov|com|org|net|edu|us|law)\b)|^(Google Docs|Home|PDF)$/i.test(t.slice(dash + 3).trim())) t = t.slice(0, dash).trim();
  if (/_/.test(t) || /%[0-9A-Fa-f]{2}/.test(t) || /\.(pdf|docx?|rtf|xlsx?|pptx?|txt|html?)$/i.test(t)) t = fileWords(t);
  return t.replace(/\s*[·|–—-]\s*$/, '').trim();
}
// Long titles are clamped to two lines; the untouched title stays in the tooltip.
function titleText(node, raw, sourceUrl) { const clean = cleanTitle(raw, sourceUrl); node.textContent = clean || String(raw ?? ''); const full = String(raw ?? '').replace(/\s+/g, ' ').trim(); if (full && full !== node.textContent) node.title = full; node.classList.add('clamp-2'); return node; }
function action(text, fn, style = '') { const b = el('button', `button ${style}`, text); b.type = 'button'; b.addEventListener('click', fn); return b; }
function safeURL(value, external = false) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value, location.href);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    if (url.username || url.password) return null;
    if (!external && url.origin !== location.origin) return null;
    return url.href;
  } catch { return null; }
}
function link(text, value, external = false, className = 'button') {
  const url = safeURL(value, external);
  if (!url) return null;
  const a = el('a', className, text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
  return a;
}
function routeLink(text, target, params = {}, className = 'button-link') {
  const a = el('a', className, text); a.href = makeHash(target, params); return a;
}
function makeHash(view, params = {}) {
  const query = new URLSearchParams();
  for (const [key,value] of Object.entries(params)) if (value !== '' && value !== undefined && value !== null) query.set(key, value);
  return `#${view}${query.size ? '?' + query.toString() : ''}`;
}
function readRoute() {
  const [path, query = ''] = location.hash.slice(1).split('?');
  const [name,id] = path.split('/');let decoded=null;try{decoded=id?decodeURIComponent(id):null;}catch{decoded=id;}
  return {view: (views[name] || extensionArea(name)) ? name : 'overview', id:decoded, params: new URLSearchParams(query)};
}
async function api(path, signal) {
  if (!['http:', 'https:'].includes(location.protocol)) throw new Error('Open this directory through the local archive server, rather than opening index.html directly.');
  const response = await fetch(path, {signal, headers: {'Accept':'application/json'}, cache: 'no-store'});
  if (!response.ok) {
    const failure = await response.json().catch(() => null);
    const error = new Error(typeof failure?.error === 'string' ? failure.error : `The archive server returned HTTP ${response.status}. Please try again or refresh this page.`);
    error.status = response.status;
    if (typeof failure?.code === 'string') error.code = failure.code;
    throw error;
  }
  const data = await response.json();
  if (data.error) {const error=new Error(typeof data.error==='string'?data.error:'The archive server could not complete this request.');if(typeof data.code==='string')error.code=data.code;throw error;}
  return data;
}
function partialHostedRelease() {
  const health=window.ARCHIVE_PUBLICATION;
  return health?.service==='legal-archive-supabase'&&health.release_scope==='partial'&&health.usable===true;
}
function hostedDatasetPending(id) {
  const health=window.ARCHIVE_PUBLICATION;
  if(health?.service!=='legal-archive-supabase'||health.ready===true)return false;
  const dataset=Array.isArray(health.datasets)?health.datasets.find(row=>row.id===id):null;
  return dataset?.ready===false||(partialHostedRelease()&&dataset?.ready!==true);
}
function publicationLinks(target) {
  const health=window.ARCHIVE_PUBLICATION;
  if(!partialHostedRelease()||!Array.isArray(health.available_views))return;
  const links=el('nav','publication-links');links.setAttribute('aria-label','Available in this release');
  for(const view of new Set(health.available_views))if(views[view]||extensionArea(view))links.append(routeLink(navName(view),view,{},'button-link'));
  if(links.children.length)target.append(links);
}
async function checkPublicationStatus() {
  const target = document.querySelector('#publication-notice');
  if (!target) return;
  target.hidden = true;
  target.replaceChildren();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const health = await api('/api/health', controller.signal);
    window.ARCHIVE_PUBLICATION=health;
    // The local archive has its own publication process and needs no hosted notice.
    if (health?.service !== 'legal-archive-supabase') return;
    if (typeof health.ready !== 'boolean') throw new Error('Missing publication status');
    if (health.ready) return;
    if(partialHostedRelease()){
      const ready=(health.datasets||[]).filter(dataset=>dataset.ready===true);
      const total=health.expected_dataset_count;
      const collectionCount=Number.isInteger(total)&&total>=ready.length?`${count(ready.length)} of ${count(total)} collections`:`${count(ready.length)} collections`;
      const hasCounts=ready.every(dataset=>Number.isSafeInteger(dataset.records)&&dataset.records>=0);
      const records=hasCounts?` · ${count(ready.reduce((sum,dataset)=>sum+dataset.records,0))} catalog records`:'';
      target.className='publication-notice';
      append(target,el('strong','','Early release · Published collections are available'),
        el('p','',`${collectionCount}${records}. Remaining collections are not yet published. Catalog counts can include multiple source versions and do not establish complete legal coverage.`));
      publicationLinks(target);target.hidden=false;
      if(typeof renderHubTabs==='function')renderHubTabs();
      return;
    }
    const hasPublished = Array.isArray(health.datasets) && health.datasets.some(dataset => dataset.ready === true);
    target.className = 'publication-notice';
    append(target, el('strong', '', 'Hosted archive connected · Publication pending'),
      el('p', '', hasPublished
        ? 'Full publication is not complete. Some collections or pages may be unavailable. Use Refresh to check their availability.'
        : 'Hosted collections have not been published yet, so pages may appear empty. Use Refresh to check their availability.'));
    target.hidden = false;
  } catch {
    if (['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) || !['http:', 'https:'].includes(location.protocol)) return;
    target.className = 'publication-notice is-unavailable';
    append(target, el('strong', '', 'Connection check unavailable'),
      el('p', '', 'The hosted archive status check did not complete. Use Refresh to try again.'));
    target.hidden = false;
  } finally {
    clearTimeout(timeout);
  }
}
function loading(target, text = 'Reading the saved collection…') {
  target.replaceChildren(); target.setAttribute('aria-busy', 'true');
  const box = el('div', 'state-message'); const spinner = el('div', 'spinner'); spinner.setAttribute('aria-hidden','true');
  append(box, spinner, el('p','',text)); target.append(box);
}
function errorState(target, error, retry) {
  target.removeAttribute('aria-busy'); target.replaceChildren();
  const box = el('div', 'state-message');
  if(error.code==='publication_pending'){
    append(box,el('h2','','This collection is not published yet'),el('p','',error.message||'This collection is awaiting publication.'),el('p','','You can continue exploring the published collections.'),routeLink('Browse available documents','documents'));
    publicationLinks(box);target.append(box);announce('This collection is awaiting publication.');return;
  }
  append(box, el('h2','','Unable to load the directory'), el('p','',error.message || 'The local archive server is not responding.'));
  const local = ['localhost','127.0.0.1','[::1]'].includes(location.hostname);
  append(box, el('p','',local?'If the server is stopped, use the archive-directory launch instructions, then refresh this page. Your saved files are unchanged.':'The collection may be temporarily unavailable. Retry in a moment or return to the library.'), action('Try again', retry, 'button-primary'));
  target.append(box); announce('Unable to load collection data.');
}
function emptyState(target, title, description, reset) {
  const box = el('div','state-message'); append(box,el('h2','',title),el('p','',description));
  if (reset) box.append(action('Clear filters',reset)); target.append(box);
}
function heading(name, description, label = 'The collected archive') {
  const box=el('div','page-heading'); const content=el('div');
  append(content,el('div','eyebrow',label),el('h1','',name),description ? el('p','',description) : null);
  append(box, content); main.append(box);
}
function metric(label, value, note) { return append(el('article','metric'),el('div','metric-label',label),el('strong','metric-value',count(value)),el('div','metric-note',note)); }
function notice(text) { return el('div','notice',text); }
function freshStamp() {
  const value = summary?.published?.completed_at;
  const target = document.querySelector('#snapshot-label');
  target.textContent = value ? `Main publication ${date(value)}` : 'Publication date not recorded';
  target.title = value ? date(value,true) : '';
}
async function loadSummary() {
  summaryController?.abort(); summaryController = new AbortController();
  const data = await api('/api/summary',summaryController.signal);
  summary = data; freshStamp(); return data;
}
// Extension areas (areas.js) register {title, nav?, parent?, render(signal, route)} on window.ARCHIVE_AREAS
// before this script runs. They use the helpers defined here and never replace a built-in view.
function extensionArea(view) { const areas = window.ARCHIVE_AREAS; return areas && !views[view] && Object.prototype.hasOwnProperty.call(areas, view) ? areas[view] : null; }
function navName(view) { const spec = views[view] || extensionArea(view) || views.overview; return spec.nav || spec.title; }
function renderOverview(data) {
  const hero=el('section','home-hero');append(hero,el('p','eyebrow','States & counties'),el('h1','','United States'),el('p','hero-description','Select a state to open its counties, saved law, judges and sources, or search the whole library.'));
  const search=el('form','home-search');search.setAttribute('aria-label','Search your library');
  const query=filterField('Search','home-query','search','','A judge, rule, statute or topic');query.label.classList.add('home-query');
  const scope=filterField('Search in','home-scope','select','documents');setOptions(scope.input,[{value:'documents',label:'All documents'},{value:'laws',label:'State law'},{value:'judges',label:'Judges'},{value:'counties',label:'Counties'}],'Choose collection','documents');scope.input.querySelector('option[value=""]')?.remove();
  const submit=el('button','button button-primary','Search library');submit.type='submit';append(search,query.label,scope.label,submit);search.addEventListener('submit',event=>{event.preventDefault();navigate(scope.input.value,{q:query.input.value.trim()});});hero.append(search);main.append(hero);
  // Clickable state grid (areas.js): state -> counties, laws, judges and sources for that state.
  if(typeof renderUsMap==='function'){const mapSection=el('section','us-map-section');main.append(mapSection);renderUsMap(mapSection,pageController.signal);}
}
function filterField(labelText,id,type,value,placeholder) {
  const label=el('label','',labelText); label.htmlFor=id;
  const input=el(type==='select'?'select':'input'); input.id=id; input.name=id;input.setAttribute('aria-label',labelText);
  if (type!=='select') {input.type=type==='date'?'date':'search';input.placeholder=placeholder || '';input.autocomplete='off';}
  input.value=value || '';label.append(input);return {label,input};
}
function setOptions(select,values,first,current) {
  const normalized=(values || []).map(v=>typeof v==='object' ? {value:String(v.value ?? v.id ?? v.code ?? v.name ?? v.label ?? ''),label:String(v.label ?? v.name ?? v.value ?? v.id ?? v.code ?? '')} : {value:String(v),label:String(v)}).filter(v=>v.value);
  if (current && !normalized.some(v=>v.value===current)) normalized.unshift({value:current,label:current});
  select.replaceChildren(); const any=el('option','',first);any.value='';select.append(any);
  for(const option of normalized) { const node=el('option','',human(option.label));node.value=option.value;select.append(node); }
  select.value=current || '';
}
function readFilters(form) {
  const params={};for(const input of form.querySelectorAll('input,select')) if(input.value.trim())params[input.name]=input.value.trim();
  for(const key of ['group','dataset','view','county','kind','from','tab'])if(route.params.get(key)&&!form.querySelector(`[name="${key}"]`))params[key]=route.params.get(key);return params;
}
function routePath(){return route.id?`${route.view}/${encodeURIComponent(route.id)}`:route.view;}
function resetFilters(){navigate(routePath(),route.view==='county'?{state:route.params.get('state'),county:route.id,from:route.params.get('from'),tab:route.params.get('tab')}:{});}
function activeFilters(form,target){
  target.replaceChildren();const names={q:'Search',status:'FJC status',role:'Role',president:'Appointed by',subtype:'Sub-category',file_type:'File type',review:'Review state',jur_level:'Jurisdiction level',record_type:'Record type',date_type:'Date type',dfrom:'From',dto:'To',validity:'Capture validity',state:'State',jurisdiction:'Jurisdiction',task_family:'Research task',layer:'Source layer',content_kind:'File type',source_type:'Publisher type',access_requirements:'Access requirement',access_method:'Access',verification_status:'Status',api_bulk:'Reference layer',system:'System',court:'Court',category:'Category',kind:'Type',dataset:'Collection',availability:'Availability',has:route.view==='sources'?'Saved content':'Profiles',sort:'Sort',view:'View'};
  const labels={details:'Detailed profiles',all:'All directory entries',biography:'With biographies',analysis:'With analysis',reports:'With report links',photo:'With photos',sources:'Source versions',name:'Name A–Z',local_resources:'With local resources',saved_information:'With saved information',no_local_resources:'No local resources yet'};
  const filters=[...route.params].filter(([key,value])=>names[key]&&value&&!(route.view==='county'&&key==='state')&&!(key==='has'&&value==='details')&&!(key==='sort'&&value!=='name'));
  target.hidden=!filters.length;if(!filters.length)return;
  target.append(el('span','active-filter-label',`${filters.length} active filter${filters.length===1?'':'s'}`));
  for(const [key,value] of filters){const field=form.querySelector(`[name="${key}"]`),option=field?.tagName==='SELECT'?[...field.options].find(o=>o.value===value):null;const label=key==='api_bulk'&&value==='1'?'API & bulk references':key==='dataset'?datasetName(value):key==='q'?value:labels[value]||option?.textContent?.replace(/ \([\d,]+\)$/,'')||human(value);const button=action(`${names[key]}: ${label} ×`,()=>{const params=Object.fromEntries(route.params);delete params[key];delete params.page;if(key==='state'||key==='system')delete params.court;navigate(routePath(),params);},'active-filter');button.setAttribute('aria-label',`Remove ${names[key].toLowerCase()} filter: ${label}`);button.title=`Remove ${names[key].toLowerCase()} filter`;target.append(button);}
}
function filterSummary(){const box=el('div','active-filters');box.setAttribute('aria-label','Active filters');box.hidden=true;return box;}
function renderFilters(countyMode) {
  const form=el('form',`filters ${countyMode?'county-filters':''}`); form.setAttribute('aria-label','Filter saved records');
  const query=filterField('Search','q','search',route.params.get('q'),countyMode?'County name or GEOID':'Search titles and saved text');
  const state=filterField('State / jurisdiction','state','select',route.params.get('state')); setOptions(state.input,[],'All jurisdictions',route.params.get('state'));
  append(form,query.label,state.label);
  if(countyMode){const availability=filterField('Available material','availability','select',route.params.get('availability'));setOptions(availability.input,[{value:'local_resources',label:'With local resources'},{value:'saved_information',label:'With saved information'},{value:'no_local_resources',label:'No local resources yet'}],'All counties',route.params.get('availability'));form.append(availability.label);}
  if(!countyMode) {
    const category=filterField('Category','category','select',route.params.get('category'));setOptions(category.input,[],'All categories',route.params.get('category'));form.append(category.label);
    if(route.params.get('kind')){const kind=filterField('Document type','kind','select',route.params.get('kind'));setOptions(kind.input,[],'All document types',route.params.get('kind'));form.append(kind.label);}
  }
  if(!countyMode){const availability=filterField('Availability','availability','select',route.params.get('availability'));setOptions(availability.input,[{value:'text',label:'Saved text'},{value:'original',label:'Original file'},{value:'link_only',label:'Link only'}],'Any availability',route.params.get('availability'));form.append(availability.label);form.classList.add('document-filters');
    // Derived-facet filters (file type, review state, jurisdiction level, record type, dates, capture validity) load their options with the results.
    const more=el('details','source-more-filters document-more-filters'),grid=el('div','source-more-grid');append(more,el('summary','','More filters'),grid);
    for(const [key,label,first,type] of [['subtype','Sub-category','Any sub-category','select'],['file_type','File type','Any file type','select'],['review','Review state','Any review state','select'],['jur_level','Jurisdiction level','Any level','select'],['record_type','Record type','Any record type','select'],['date_type','Date type','Choose a date','select'],['dfrom','From','','date'],['dto','To','','date'],['validity','Capture validity','Sound captures only','select']]){const field=filterField(label,key,type,route.params.get(key));if(type==='select')setOptions(field.input,[],first,route.params.get(key));grid.append(field.label);if(route.params.get(key))more.open=true;}
    form.append(more);
    // One-click saved-date ranges; they set the explicit date type so the meaning stays visible in the active filters.
    const presets=el('div','date-presets');presets.append(el('span','','Saved:'));const iso=d=>d.toISOString().slice(0,10),today=new Date();
    for(const [label,days] of [['Last 7 days',7],['Last 30 days',30],['Last 90 days',90],['This year',null]]){presets.append(action(label,()=>{const params=readFilters(form);const from=days?new Date(today.getTime()-days*864e5):new Date(today.getFullYear(),0,1);params.date_type='saved';params.dfrom=iso(from);params.dto=iso(today);delete params.page;navigate(routePath(),params);},'chip'));}
    form.append(presets);}
  if(route.view==='county'){state.label.hidden=true;state.input.disabled=true;}
  const clear=action('Clear filters',resetFilters);form.append(clear);
  const submit=()=>{clearTimeout(filterTimer);if(!form.reportValidity())return;const params=readFilters(form);history.replaceState(null,'',makeHash(route.id?`${route.view}/${encodeURIComponent(route.id)}`:route.view,params));route=readRoute();loadList(countyMode,form);};
  form.addEventListener('submit',e=>{e.preventDefault();submit();});
  form.addEventListener('keydown',e=>{if(e.key==='Enter' && e.target.tagName==='INPUT'){e.preventDefault();submit();}});
  form.addEventListener('change',e=>{if(e.target.tagName==='SELECT')submit();});
  form.addEventListener('input',e=>{if(e.target.tagName!=='INPUT')return;clearTimeout(filterTimer);filterTimer=setTimeout(()=>{if(form.checkValidity())submit();},350);});
  return form;
}
function table(headers) {
  const wrapper=el('div','table-scroll'), tbl=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');
  for(const text of headers){const th=el('th','',text);th.scope='col';hr.append(th);}head.append(hr);append(tbl,head,body);wrapper.append(tbl);return {wrapper,body};
}
function documentRows(items,body) {
  for(const item of items) {
    const row=el('tr'),titleCell=el('td');const title=el('button','document-title',displayTitle(item));title.type='button';title.addEventListener('click',()=>item.dataset==='judge_entities'?navigate(`judge/${encodeURIComponent(item.id)}`):openRecord(item,title));
    titleCell.append(title);
    if(route.params.get('view')==='sources')titleCell.append(el('div','record-subline',datasetName(item.dataset)));
    const f=item.facets||null;
    if(f&&f.display_title&&cleanTitle(f.display_title,item.source_url)!==title.textContent)titleText(title,f.display_title,item.source_url);
    else title.title=String(item.title||'').replace(/\s+/g,' ').trim()!==title.textContent?String(item.title||'').replace(/\s+/g,' ').trim():'';
    title.classList.add('clamp-2');
    const jurisdiction=el('td','jurisdiction');append(jurisdiction,el('div','',item.state || (f&&f.jurisdiction_level==='federal'?'Federal':'Unassigned')),item.county?el('div','record-subline',item.county):(f&&f.court_label_as_published?el('div','record-subline',f.court_label_as_published):null));
    const type=el('td'),kindText=item.category_label||kindLabel(item.kind);type.append(el('span','document-kind',kindText));
    // One qualifier at most: an open review state outranks the subtype, and a subtype that repeats the kind is dropped.
    if(f&&f.review_state&&f.review_state!=='not_applicable'&&f.review_state!=='reviewed')type.append(el('div','record-subline',f.review_label||human(f.review_state)));
    else if(f&&f.doc_subtype&&human(f.doc_subtype).toLowerCase()!==String(kindText).toLowerCase())type.append(el('div','record-subline',human(f.doc_subtype)));
    const availability=el('td'),format=item.has_text?'Saved text':item.has_original?'Original file':'Link only';
    availability.append(el('span','document-format',f&&f.file_type&&f.file_type!=='none'&&item.has_original?`${format} · ${f.file_type.toUpperCase()}`:format));
    const sourceDate=dateValue(f?.dates?.source_as_of),savedDate=dateValue(f?.dates?.saved_at);
    const stamp=sourceDate?`Source as of ${date(sourceDate)}`:savedDate?`Saved ${date(savedDate)}`:'';
    if(stamp){const note=el('div','record-subline',stamp),basis=(sourceDate?f.dates.source_as_of:f.dates.saved_at)?.basis;if(typeof basis==='string')note.title=basis;availability.append(note);}
    append(row,titleCell,jurisdiction,type,availability);body.append(row);
  }
}
function countyRows(items,body) {
  for(const item of items) {
    const row=el('tr');const nameCell=el('td');const name=routeLink(item.name || 'Unnamed county','documents',{group:'all',county:item.geoid,state:item.state},'document-title');append(nameCell,name,el('div','record-subline',`GEOID ${item.geoid || 'not recorded'}`));
    const status=el('td');status.append(badge(human(item.profile_status)||'Not recorded','neutral wrap'));
    append(row,nameCell,el('td','jurisdiction',item.state || 'Unassigned'),status,el('td','small-number',count(item.saved_profiles)),el('td','small-number',count(item.saved_sites)),el('td','small-number',count(item.local_resources)));body.append(row);
  }
}
function kindLabel(value){return ({statutory_provision:'Statute',statutes:'Statutes',regulatory_provision:'Regulation',regulations:'Regulations',rule_provision:'Rule',court_rule_or_order:'Rule or order',local_rules:'Local rules',constitutions:'Constitution',observed_comprehensive_constitution:'Constitution',court_form_or_other_document:'Form or document',judge_profile:'Judge profile',law_directory:'Law directory',court_directory:'Court directory',coverage_county:'County profile'})[value]||human(value)||'Document';}
function displayTitle(item){
  const raw=String(item.title||'').trim();
  if(!raw||/^https?:\/\//i.test(raw)||/^Microsoft Word\s*-/i.test(raw)||TITLE_PLACEHOLDER.test(raw)){
    let filename='';try{const url=new URL(item.source_url||raw.replace(/\s*\[OCR\]$/i,''));filename=fileWords(url.pathname.split('/').filter(Boolean).pop()||'');}catch{}
    if(filename&&!/^[0-9a-f ]{24,}$/i.test(filename))return filename;
    const fromUrl=cleanTitle(raw,item.source_url);if(fromUrl)return fromUrl;
    return [item.county||item.state,item.category_label||kindLabel((item.kind||'').split(';')[0].trim())||'Saved document'].filter(Boolean).join(' · ');
  }
  return cleanTitle(raw,item.source_url)||raw;
}
function stateGrid(states,target){
  const section=el('section','state-browser');append(section,el('h2','','Choose a state or jurisdiction'));const grid=el('div','state-grid');
  for(const state of states||[]){const value=typeof state==='string'?state:state.name||state.label||state.value;if(value)grid.append(routeLink(value,route.view,{...Object.fromEntries(route.params),state:value,page:1},'state-link'));}section.append(grid);target.append(section);
}
function countyCards(items,target){const grid=el('div','county-grid');for(const item of items){const card=el('article','county-card'),resources=Number(item.local_resources)||0,pending=Number(item.pending_local_resources)||0;append(card,el('p','card-location',item.state),routeLink(item.name,`county/${encodeURIComponent(item.geoid)}`,{state:item.state,county:item.geoid,from:location.hash},'county-title'),el('p','',resources?`${count(resources)} local resource${resources===1?'':'s'}`:(Number(item.saved_profiles)||Number(item.saved_sites))?'County information available':'No local documents saved yet'),item.has_court_registry?el('p','publication-note','Court & clerk registry available'):null,pending?el('p','publication-note',`${count(pending)} awaiting publication`):null,routeLink('Explore county →',`county/${encodeURIComponent(item.geoid)}`,{state:item.state,county:item.geoid,from:location.hash}));grid.append(card);}target.append(grid);}
function pagination(total,page,limit) {
  const box=el('div','pagination');const pages=Math.max(1,Math.ceil(total/limit));const range=total ? `${count((page-1)*limit+1)}–${count(Math.min(page*limit,total))} of ${count(total)}` : 'No matching records';
  box.append(el('span','',range));const buttons=el('div','button-row');
  const turn=(next)=>{const params=Object.fromEntries(route.params);params.page=next;navigate(route.id?`${route.view}/${encodeURIComponent(route.id)}`:route.view,params);};
  const prev=action('← Previous',()=>turn(page-1));prev.disabled=page<=1;const next=action('Next →',()=>turn(page+1));next.disabled=page>=pages;
  append(buttons,prev,el('span','visually-hidden',`Page ${page} of ${pages}`),next);box.append(buttons);return box;
}
async function loadList(countyMode,form) {
  pageController?.abort();pageController=new AbortController();const signal=pageController.signal;
  const target=document.querySelector('#results');target.hidden=false;loading(target);
  const selected=document.querySelector('#active-filters');if(selected)activeFilters(form,selected);
  for(const anchor of document.querySelectorAll('.category-link')){const category=new URLSearchParams(anchor.hash.split('?')[1]).get('category');anchor.href=makeHash('laws',{...Object.fromEntries(route.params),category,page:1});anchor.classList.toggle('active',route.params.get('category')===category);}
  for(const anchor of document.querySelectorAll('.county-rules-choices a'))anchor.classList.toggle('active',new URLSearchParams(anchor.hash.split('?')[1]).get('category')===route.params.get('category'));
  const params=new URLSearchParams(route.params);const page=Math.max(1,Number(params.get('page'))||1);params.set('page',page);params.set('limit','50');
  if(route.view==='county')params.set('county',route.id);
  if(!countyMode)params.set('group',views[route.view].group==='all'?(params.get('group') || 'all'):views[route.view].group);
  try {
    if(route.view==='laws'&&window.ARCHIVE_PUBLICATION_PROMISE){await window.ARCHIVE_PUBLICATION_PROMISE;if(signal.aborted)return;}
    let hub=route.view==='laws'?{}:null,data;
    if(lawHubOpen()){hub=await api(`/api/explore?group=laws&state=${encodeURIComponent(route.params.get('state')||'')}`,signal).catch(error=>{if(error.name==='AbortError')throw error;return {unavailable:true};});if(!hub.unavailable)data={states:(hub.jurisdictions||[]).map(row=>row.state),categories:hub.categories||[],items:[],total:0};}
    if(!data)data=await api(`/api/${countyMode?'counties':'documents'}?${params}`,signal);if(signal.aborted)return;
    target.removeAttribute('aria-busy');target.replaceChildren();
    setOptions(form.querySelector('[name=state]'),data.states,'All jurisdictions',route.params.get('state'));
    if(countyMode&&data.availabilities)setOptions(form.querySelector('[name=availability]'),data.availabilities.map(value=>({...value,label:`${value.label} (${count(value.count)})`})),'All counties',route.params.get('availability'));
    if(!countyMode){setOptions(form.querySelector('[name=category]'),data.categories,'All categories',route.params.get('category'));if(form.querySelector('[name=kind]'))setOptions(form.querySelector('[name=kind]'),data.kinds,'All document types',route.params.get('kind'));
      const options=data.facet_options||{};const toOptions=map=>Object.entries(map||{}).map(([value,label])=>({value,label}));
      for(const [key,map,first] of [['subtype',options.subtype,'Any sub-category'],['file_type',options.file_type,'Any file type'],['review',options.review,'Any review state'],['jur_level',options.jur_level,'Any level'],['record_type',options.record_type,'Any record type'],['date_type',options.date_type,'Choose a date']]){const select=form.querySelector(`[name=${key}]`);if(select)setOptions(select,toOptions(map),first,route.params.get(key));}
      const validity=form.querySelector('[name=validity]');if(validity)setOptions(validity,[{value:'any',label:'Every capture (including parked and empty)'},...toOptions(options.validity)],'Sound captures only',route.params.get('validity'));
      const more=form.querySelector('.document-more-filters');if(more)more.hidden=!data.facets_ready;}
    if(selected)activeFilters(form,selected);
    const total=Number(data.total)||0;const items=Array.isArray(data.items)?data.items:[];
    const browseStates=document.querySelector('#state-browser');if(route.view==='laws'&&hub&&!hub.unavailable){renderLawHub(hub,browseStates);const title=main.querySelector('h1');if(title)title.textContent=route.params.get('state')?`${route.params.get('state')} laws & rules`:'State law';}else if(browseStates){browseStates.replaceChildren();if(!route.params.get('state')&&!route.params.get('q')&&!route.params.get('category')&&!route.params.get('dataset')&&!route.params.get('availability'))stateGrid(data.states,browseStates);}
    if(lawHubOpen()&&hub&&!hub.unavailable){target.hidden=true;announce('Browse law jurisdictions and categories.');return;}
    if(hub?.unavailable)target.append(el('p','county-section-note','Category summaries are temporarily unavailable. You can still browse saved documents below.'));
    if(countyMode&&!route.params.get('state')&&!route.params.get('q')&&!route.params.get('category')&&!route.params.get('dataset')&&!route.params.get('kind')&&!route.params.get('availability')){target.hidden=true;announce('Choose a jurisdiction or category to browse.');return;}target.hidden=false;
    const sourceView=route.params.get('view')==='sources';
    const resultLabel=countyMode?(total===1?'county':'counties'):sourceView?(total===1?'source record':'source records'):(total===1?'document':'documents');
    const explanation=countyMode?route.params.get('state')||'County directory':sourceView?'Individual saved versions':'Open a document to read';
    append(target,append(el('div','results-heading'),el('strong','',`${count(total)} ${resultLabel}`),el('small','',explanation)));
    if(!items.length)emptyState(target,'No matching records',countyMode?'Try a different county name or remove an availability filter.':'No saved records match these filters. An empty result does not mean no such legal material exists.',resetFilters);
    else if(countyMode)countyCards(items,target);
    else{const tbl=table(['Document','Jurisdiction','Type','']);documentRows(items,tbl.body);target.append(tbl.wrapper);}
    target.append(pagination(total,Number(data.page)||page,Number(data.limit)||50));announce(`${count(total)} ${resultLabel} found.`);
  }catch(error){if(error.name!=='AbortError')errorState(target,error,()=>loadList(countyMode,form));}
}
function lawHubOpen(){return route.view==='laws'&&!['q','category','dataset','availability','kind','group','view','subtype','file_type','review','jur_level','record_type','date_type','dfrom','dto','validity'].some(key=>route.params.get(key));}
function renderLawHub(data,target){
  target.replaceChildren();const state=route.params.get('state');
  if(state)target.append(routeLink(`Explore ${state} source references →`,'sources',{jurisdiction:state},'context-source-link'));
  const bulkPending=hostedDatasetPending('open_us_law');
  if(bulkPending)target.append(notice('The Open US Law bulk provision collection and its outline browser are not yet published. You can browse the published law and rule records below; coverage varies by state and source.'));
  if(!lawHubOpen()){append(target,routeLink(`← ${state?state+' law categories':'Browse law jurisdictions'}`,'laws',state?{state}:{},'back-link'));return;}
  if(!state){const section=el('section','law-jurisdictions');append(section,el('h2','','Explore by jurisdiction'),el('p','section-intro','Choose a state to see its collected statutes, court rules and other legal resources.'));const grid=el('div','state-grid law-state-grid');for(const row of data.jurisdictions||[]){const anchor=routeLink('', 'laws',{state:row.state},'state-link');append(anchor,el('span','',row.label||row.state),el('small','',`${count(row.total)} records`));grid.append(anchor);}section.append(grid);target.append(section);}
  if(state&&window.LawReader&&!bulkPending){const host=el('div','lawb-host');target.append(host);window.LawReader.browser(host,state,{open:id=>openRecord({id},document.activeElement),start:{kind:route.params.get('toc'),node:route.params.get('node')}});}
  const section=el('section','law-categories');append(section,el('h2','',state?`${state} documents by category`:'Browse by category'),el('p','section-intro','Open a category to search its saved text, original files and publisher links.'));
  const descriptions={statutes:'Enacted laws, codes and individual provisions.',rules:'Court rules, practice directions and orders.',constitutions:'Constitutional text and related provisions.',regulations:'Administrative rules and regulations.',forms:'Forms and other supporting documents.',guidance:'Practice guides and reference material.',directories:'Court and legal source directories.',other:'Additional collected legal resources.'};
  const cards=el('div','law-category-grid');for(const category of data.categories||[]){if(!Number(category.total))continue;const card=routeLink('','laws',{...(state?{state}:{}),category:category.id},'law-category-card');append(card,el('span','law-category-count',`${count(category.total)} records`),el('h3','',category.label),el('p','',descriptions[category.id]||'Collected source material.'),el('span','button-link','Browse documents →'));cards.append(card);}if(!cards.children.length)emptyState(section,'No categorized material yet','This jurisdiction has no matching records in the current library.');else section.append(cards);target.append(section);
  const details=el('details','library-details');if(bulkPending)details.open=true;append(details,el('summary','',bulkPending?'Published collections & coverage':'Collections & coverage'),el('p','','Counts combine saved document groups and publisher data rows. They do not establish unique laws, legal currency or complete jurisdiction coverage. Categories may overlap.'));
  const sources=el('div','hub-source-list');for(const dataset of data.datasets||[]){const entry=el('article','hub-source');append(entry,routeLink(dataset.label||datasetName(dataset.id),'laws',{...(state?{state}:{}),dataset:dataset.id}),el('p','',`${count(dataset.total)} records${dataset.snapshot_label?' · '+dataset.snapshot_label:''}`),evidenceDates(dataset.source_as_of,dataset.saved_at),dataset.snapshot_date?el('p','date-note',`Publisher snapshot: ${date(dataset.snapshot_date)}`):null,dataset.published_at?el('p','date-note',`Published ${date(dataset.published_at)}`):null);sources.append(entry);}details.append(sources);target.append(details);
}
function renderLibrary(countyMode,skipHeading=false) {
  const view=views[route.view];if(!skipHeading)heading(view.title,view.description,countyMode?'Local research':'Your library');
  // Federal court and DOJ sources are a scope of the document library, not a separate page.
  if(route.view==='documents'||route.view==='federal'){const scope=el('div','scope-switch');scope.setAttribute('aria-label','Document scope');for(const [view,label] of [['documents','Whole library'],['federal','Federal court & DOJ sources']]){const a=routeLink(label,view,{},view===route.view?'chip selected':'chip');if(view===route.view)a.setAttribute('aria-current','true');scope.append(a);}main.append(scope);}
  if(route.params.get('dataset'))main.append(el('p','collection-context',datasetName(route.params.get('dataset'))));
  const form=renderFilters(countyMode);main.append(form);
  const selected=filterSummary();selected.id='active-filters';main.append(selected);activeFilters(form,selected);
  if(false&&route.view==='laws'&&!lawHubOpen()){const categories=el('div','category-links');categories.setAttribute('aria-label','Law categories');for(const [id,label] of [['statutes','Statutes'],['rules','Rules & orders'],['constitutions','Constitutions'],['regulations','Regulations']])categories.append(routeLink(label,'laws',{...Object.fromEntries(route.params),category:id,page:1},`category-link${route.params.get('category')===id?' active':''}`));main.append(categories);}
  if(countyMode||route.view==='laws'){const states=el('div');states.id='state-browser';main.append(states);}
  const result=el('section','table-panel');result.id='results';result.setAttribute('aria-label',countyMode?'County search results':'Document search results');main.append(result);
  const options=el('details','library-details');options.append(el('summary','',countyMode?'About county coverage':'Browsing options & source versions'));
  if(countyMode)options.append(el('p','','The directory includes counties without saved local documents. Available links and files are shown for each county; a directory entry is not a complete county archive.'));
  else{const bar=el('div','view-switch');bar.setAttribute('aria-label','Record display');const sources=route.params.get('view')==='sources';
    for(const [value,label] of [['grouped','Combined documents'],['sources','Source versions']]){const button=action(label,()=>{const params=Object.fromEntries(route.params);delete params.page;if(value==='sources')params.view='sources';else delete params.view;navigate(route.id?`${route.view}/${encodeURIComponent(route.id)}`:route.view,params);},sources===(value==='sources')?'selected':'');button.setAttribute('aria-pressed',String(sources===(value==='sources')));bar.append(button);}append(options,el('p','','Combined browsing keeps equivalent copies together. Source versions show each retained capture.'),bar);}
  main.append(options);loadList(countyMode,form);
}
// Rules, forms and filing sources for one county. The endpoint may be absent or report {"available":false}: then nothing is shown.
async function renderCountyFiling(county,signal,target){
  let data;try{data=await api(`/api/county-filing?fips=${encodeURIComponent(county.geoid)}`,signal);}catch{target.remove();return;}
  if(signal.aborted){target.remove();return;}
  if(!data||!data.available||!Array.isArray(data.groups)||!data.groups.length){target.remove();return;}
  target.replaceChildren();
  target.append(append(el('div','section-heading'),el('h2','','Rules, forms and filing sources'),el('small','',[data.county?.name,data.county?.state].filter(Boolean).join(', '))));
  if(data.summary)target.append(el('p','muted-note',sentenceClip(data.summary,160)));
  for(const group of data.groups){
    const items=Array.isArray(group.items)?group.items:[];if(!items.length)continue;
    const box=el('div','filing-group');box.append(el('h3','county-section-heading',group.scope_label||'Filing sources'));
    const list=el('ul','filing-list');
    for(const item of items){
      const li=el('li','filing-item');
      if(item.kind_label||item.kind)li.append(el('span','tag',item.kind_label||human(item.kind)));
      const anchor=link(String(cleanTitle(item.title,item.url)||item.title||item.url||'Filing source'),item.url,true,'filing-title');
      li.append(anchor||el('span','filing-title',cleanTitle(item.title,item.url)||'Filing source'));
      const meta=[item.publisher,item.as_of?`as of ${item.as_of}`:''].filter(Boolean).join(' · ');
      if(meta)li.append(el('span','filing-meta',meta));
      list.append(li);
    }
    box.append(list);target.append(box);
  }
}
function countyTabs(county){
  const selected=['overview','litigation','rules','access'].includes(route.params.get('tab'))?route.params.get('tab'):['q','category','kind','availability','dataset'].some(key=>route.params.get(key))?'rules':'overview',nav=el('nav','county-section-tabs');nav.setAttribute('aria-label','County sections');
  for(const [id,label] of [['overview','Overview'],['litigation','Local rules & filing'],['rules','Other saved documents'],['access','Courts & access']]){const params={state:county.state,county:county.geoid,from:route.params.get('from'),tab:id};const anchor=routeLink(label,`county/${encodeURIComponent(county.geoid)}`,params,`county-section-tab${id===selected?' active':''}`);if(id===selected)anchor.setAttribute('aria-current','page');nav.append(anchor);}main.append(nav);return selected;
}
async function renderCountyLitigation(county,signal){
  const section=el('section','county-litigation');append(section,el('h2','','Local rules & filing resources'),el('p','section-intro','Browse saved court rules, forms, standing orders, filing guidance, fees and contacts. Court applicability is recorded separately from the county association.'));main.append(section);
  const form=el('form','filters litigation-filters');form.setAttribute('aria-label','Filter county litigation resources');
  const search=filterField('Search resources','q','search',route.params.get('q'),'Title or resource type'),type=filterField('Resource type','resource_type','select',route.params.get('resource_type')),availability=filterField('Availability','availability','select',route.params.get('availability'));
  setOptions(type.input,[],'All substantive resources',route.params.get('resource_type'));setOptions(availability.input,[{value:'saved',label:'Saved content'},{value:'linked',label:'Source link only'},{value:'needs_review',label:'Needs review'}],'Any availability',route.params.get('availability'));
  const submit=el('button','button','Search');submit.type='submit';const controls=append(el('div','button-row'),submit,action('Clear filters',()=>navigate(`county/${encodeURIComponent(county.geoid)}`,{state:county.state,county:county.geoid,from:route.params.get('from'),tab:'litigation'})));append(form,search.label,type.label,availability.label,controls);section.append(form);
  function apply(){navigate(`county/${encodeURIComponent(county.geoid)}`,{state:county.state,county:county.geoid,from:route.params.get('from'),tab:'litigation',q:search.input.value.trim(),resource_type:type.input.value,availability:availability.input.value});}
  form.addEventListener('submit',event=>{event.preventDefault();apply();});type.input.addEventListener('change',apply);availability.input.addEventListener('change',apply);
  const results=el('div','litigation-results');results.setAttribute('aria-live','polite');section.append(results);loading(results,'Opening verified county resources…');
  try{const params=new URLSearchParams({geoid:county.geoid,limit:'24'});for(const key of ['q','resource_type','availability','page'])if(route.params.get(key))params.set(key,route.params.get(key));const data=await api(`/api/county-litigation?${params}`,signal);if(signal.aborted)return;results.removeAttribute('aria-busy');results.replaceChildren();
    setOptions(type.input,(data.facets?.resource_types||[]).map(item=>({...item,label:`${item.label} (${count(item.count)})`})),'All substantive resources',route.params.get('resource_type'));setOptions(availability.input,(data.facets?.availability||[]).map(item=>({...item,label:`${item.label} (${count(item.count)})`})),'Any availability',route.params.get('availability'));
    if(!data.available){append(results,el('h3','','Resource packet not published yet'),el('p','','Validated local resources will appear here as their source packets are published. Existing county documents and court references remain available in the other tabs.'));return;}
    append(results,el('p','results-summary',`${count(data.total)} matching resource${data.total===1?'':'s'}`),dataNote([data.qualification,'Source directories and unclassified references are available through their explicit resource-type filters. A saved rules index does not mean every linked rule has been downloaded.'].filter(Boolean).join(' ')));
    if(!data.items?.length){emptyState(results,'No matching published resources','Try another resource type or clear these filters. No saved result does not mean the county has no applicable rule or form.',()=>navigate(`county/${encodeURIComponent(county.geoid)}`,{state:county.state,county:county.geoid,tab:'litigation'}));return;}
    const grid=el('div','county-resource-grid');for(const item of data.items){const card=el('article','county-resource-card litigation-card'),badges=el('div','litigation-badges');append(badges,badge(item.resource_type_label),badge(item.availability_label,item.availability==='needs_review'?'muted':''));append(card,badges,el('h3','',displayTitle(item)));if(item.document_shape)card.append(el('p','resource-shape',human(item.document_shape)));if(item.legal_status&&item.legal_status!=='unknown')card.append(el('p','resource-status',`Source status: ${human(item.legal_status)}`));if(item.description)card.append(el('p','',item.description));
      const applicability=item.applicability||{};if(applicability.court_name)card.append(el('p','resource-court',applicability.court_name));card.append(el('p','resource-applicability',applicability.note||'Court applicability is not established by the county association.'));
      card.append(evidenceDates(item.source_as_of,item.saved_at));const buttons=el('div','button-row');append(buttons,action(item.has_text?'Read resource':'View source details',event=>openRecord(item,event.currentTarget),'button-primary'),link('Original file ↗',item.original_url),link('Publisher ↗',item.source_url,true,'button-link'));card.append(buttons);grid.append(card);}results.append(grid);results.append(pagination(data.total,data.page,data.limit));
  }catch(error){if(error.name!=='AbortError'){results.removeAttribute('aria-busy');errorState(results,error,render);results.append(el('p','county-section-note','Existing county documents remain available in the other tabs.'));}}
}
function renderCountyRegistry(data,target){
  target.append(el('h2','','Courts & access'));
  if(!data.available){append(target,el('p','registry-empty','No court registry entry is linked to this county yet. The saved county resources remain available.'),evidenceDates(data.source_as_of,data.saved_at));return;}
  append(target,el('p','section-intro','Court, clerk and access references from the saved county registry.'),evidenceDates(data.source_as_of,data.saved_at),footnote('Source as of is the registry snapshot date. These references may describe historical offices; present service and live website access have not been rechecked.'));
  const counts=data.counts||{},metrics=el('div','insight-metrics county-registry-metrics');for(const [key,label] of [['courts','Court entries'],['clerks','Clerk entries'],['judge_seats','Judge-seat associations']])append(metrics,append(el('div'),el('strong','',count(counts[key])),el('span','',label)));target.append(metrics);
  function section(title,rows,kind){if(!rows?.length)return;const box=el('section','county-registry-section');box.append(el('h3','county-section-heading',title));
    function cards(list){const grid=el('div','county-resource-grid');for(const row of list){const card=el('article','county-resource-card');append(card,el('h4','',row.name||row.label||'Saved directory reference'));const descriptor=kind==='court'?row.type:kind==='clerk'?row.office:row.status;if(descriptor)card.append(el('p','',human(descriptor)));const anchor=link(kind==='link'?'Visit resource ↗':'Open source reference ↗',row.url,true,'button-link');card.append(anchor||el('p','','No website reference recorded.'));grid.append(card);}return grid;}
    box.append(cards(rows.slice(0,10)));if(rows.length>10){const more=el('details','library-details');append(more,el('summary','',`Show ${count(rows.length-10)} more ${title.toLowerCase()}`),cards(rows.slice(10)));box.append(more);}target.append(box);}
  section('Courts',data.courts,'court');section('Clerks & offices',data.clerks,'clerk');section('Access & resource links',data.links,'link');
  if(!data.courts?.length&&!data.clerks?.length&&!data.links?.length)target.append(el('p','registry-empty','The registry identifies this county but provides no court, clerk or access references.'));
  const details=el('details','library-details');append(details,el('summary','','Registry source & limitations'));const facts=el('dl','record-meta');metaRow(facts,'Source',data.source?.label);metaRow(facts,'Snapshot',data.source?.snapshot);metaRow(facts,'Verified source artifacts',data.source?.verified_artifacts);metaRow(facts,'Missing source artifacts',data.source?.missing_artifacts);metaRow(facts,'Source as of',data.source_as_of?date(data.source_as_of):'Unknown');metaRow(facts,'Saved',data.saved_at?date(data.saved_at):'Unknown');details.append(facts);if(data.source?.qualification)details.append(el('p','',data.source.qualification));for(const note of data.limitations||[])details.append(el('p','',note));details.append(el('p','','Judge-seat associations are source links between offices and people, not a count of unique judges or proof of current service.'));target.append(details);
}
async function renderCountyAccess(county,signal){
  const section=el('section','county-access');main.append(section);loading(section,'Opening court and access references…');
  try{const data=await api(`/api/county-registry?geoid=${encodeURIComponent(county.geoid)}`,signal);if(signal.aborted)return;section.removeAttribute('aria-busy');section.replaceChildren();renderCountyRegistry(data,section);}
  catch(error){if(error.name==='AbortError')return;section.removeAttribute('aria-busy');section.replaceChildren(el('p','county-section-note','The court directory is temporarily unavailable. Saved county documents and source links remain available below.'));}
  if(!signal.aborted)append(section,routeLink('Browse saved court & county records →','documents',{state:county.state,county:county.geoid,category:'directories'},'button'));
  if(!signal.aborted)await renderTrellisCounty(county,signal);
}
async function renderTrellisCounty(county,signal){
  const target=el('section','county-access trellis-county-profile');main.append(target);
  try{const data=await api(`/api/trellis-coverage/county?fips=${encodeURIComponent(county.geoid)}`,signal);if(signal.aborted)return;
    if(!data.available){target.remove();return;}
    append(target,el('h2','','County court profile'),el('p','section-intro',data.detail_captured?'Saved county contact and location information.':'This county appears in the saved statewide coverage list.'));
    const info=data.publisher_reported||{},facts=el('dl','record-meta');
    for(const [key,label] of [['seat','County seat'],['population','Population reported'],['phone_number','Phone'],['administration_address','County administration address'],['court_address','Court address'],['established_year','Established']])if(info[key]!==null&&info[key]!==undefined&&info[key]!=='')metaRow(facts,label,String(info[key]));
    if(facts.children.length)target.append(facts);
    if(info.website)target.append(link('County website listed in the saved profile ↗',info.website,true,'button-link'));
    const practices=data.practice_areas||[];if(practices.length)target.append(el('p','',`Practice areas listed: ${practices.join(', ')}`));
    if(data.has_documents!==null&&data.has_documents!==undefined)target.append(footnote(data.has_documents?'The saved coverage list reports document coverage for this county. Individual case documents have not been verified as downloaded.':'This state response does not report document coverage for the county.'));
    append(target,evidenceDates(data.source_as_of,data.captured_at),footnote('County details are publisher-reported. The administration address is not necessarily a courthouse; field currency has not been independently verified.'));
    const details=el('details','library-details');details.append(el('summary','','Source & capture details'));
    for(const source of data.detail_sources||[]){const url=source.source_url||source.url;if(url)details.append(link('Open captured source page ↗',url,true,'button-link'));}
    if(info.name_meaning)details.append(el('p','',info.name_meaning));
    const receipt=data.receipts?.county||data.detail_sources?.[0]?.receipt;if(receipt)details.append(link('View saved capture receipt',`/api/trellis-coverage/receipt?id=${encodeURIComponent(receipt)}`,true,'button-link'));
    details.append(el('p','',data.detail_captured?'Saved profile fields are available here. A profile snapshot does not represent a complete court archive.':'County detail fields have not been captured in this refreshed layer yet.'));target.append(details);
  }catch(error){if(error.name==='AbortError')return;target.replaceChildren(el('p','county-section-note','Refreshed county profile details are temporarily unavailable.'));}
}
function countyOverview(county){
  main.append(routeLink(`Explore ${county.state} source references →`,'sources',{jurisdiction:county.state},'context-source-link'));
  const packet=el('section','county-packet-banner');append(packet,el('div','',`${count(county.litigation_resources||0)} structured litigation resources in this county packet`),routeLink('Explore local rules & filing →',`county/${encodeURIComponent(county.geoid)}`,{state:county.state,county:county.geoid,from:route.params.get('from'),tab:'litigation'},'button'));main.append(packet);
  const grid=el('section','county-summary-grid');grid.setAttribute('aria-label','County library contents');grid.append(el('h2','visually-hidden','County library contents'));
  for(const [title,value,description,tab,category] of [['Local resources',county.local_resources,'Published local pages and documents linked to this county.','rules','rules'],['Saved county profiles',county.saved_profiles,'Collected county profile snapshots and directory information.','overview',null],['Saved local sites',county.saved_sites,'Collected site records; availability and authority vary by source.','access',null]]){const card=el('article','county-summary-card');append(card,el('h3','',title),el('strong','',count(value)),el('p','',description));if(tab!=='overview')card.append(routeLink(tab==='rules'?'Browse rules & forms →':'Explore courts & access →',`county/${encodeURIComponent(county.geoid)}`,{state:county.state,county:county.geoid,from:route.params.get('from'),tab,...(category?{category}:{})}));else if(county.refreshed_profiles_outside_document_index)card.append(action('Read county profile below',()=>document.querySelector('.trellis-county-profile')?.scrollIntoView({behavior:'smooth'}),'button-link'));else card.append(routeLink('View county documents →','documents',{county:county.geoid,state:county.state}));grid.append(card);}main.append(grid);
  main.append(footnote('A county directory entry identifies a jurisdiction. Saved resource counts describe this library and do not establish a complete county archive.'),routeLink('Browse all county documents →','documents',{state:county.state,county:county.geoid},'button'));
}
async function renderCounty(signal){
  loading(main,'Opening county resources…');
  try{const data=await api(`/api/counties?q=${encodeURIComponent(route.id||'')}&limit=10`,signal);if(signal.aborted)return;main.replaceChildren();main.removeAttribute('aria-busy');const county=data.items?.find(item=>String(item.geoid)===route.id);
    if(!county){emptyState(main,'County not found','Choose a county from the directory.',()=>navigate('counties'));return;}
    const previous=route.params.get('from'),back=routeLink(`← ${county.state} counties`,'counties',{state:county.state},'back-link');if(previous&&/^#counties(?:\?|$)/.test(previous)){back.href=previous;back.textContent='← Back to counties';}main.append(back);heading(county.name,`${county.state} · Local courts, rules and documents`,'County resources');
    if(Number(county.pending_local_resources)>0)main.append(el('p','publication-note county-publication-note',`${count(county.pending_local_resources)} local resource${Number(county.pending_local_resources)===1?'':'s'} awaiting publication in the validated collection.`));
    if(county.visual?.is_hero===true){const visual=county.visual,url=safeURL(visual.image_url||(visual.asset_id?`/library-assets/${encodeURIComponent(visual.asset_id)}`:null));if(url){const figure=el('figure','county-hero'),image=el('img');image.src=url;image.alt=visual.court_label||visual.court_name||county.name;append(figure,image,el('figcaption','',visual.association_note||visual.court_label||''));main.append(figure);}}
    if(county.visual&&county.visual.is_hero!==true){const visual=county.visual,url=safeURL(visual.image_url||(visual.asset_id?`/library-assets/${encodeURIComponent(visual.asset_id)}`:null));if(url){const mark=el('div','county-court-mark'),image=el('img');image.src=url;image.alt='';const label=el('div');append(label,el('span','','Associated court'),el('strong','',visual.court_label||visual.court_name||'Court mark'));append(mark,image,label);main.append(mark);const details=el('details','library-details court-mark-details');append(details,el('summary','','About this court mark'),el('p','',visual.association_note||'A saved court or judiciary mark. It is not a courthouse photograph.'),link('Court website ↗',visual.source_url,true,'button-link'));main.append(details);}}
    route.params.set('county',county.geoid);route.params.set('state',county.state);
    const tab=countyTabs(county);
    // Rules, forms and filing sources come first on the county page.
    const filing=(tab==='overview'||tab==='litigation')?el('section','panel county-filing'):null;
    if(filing){filing.append(el('p','quiet-empty','Checking rules and filing sources…'));main.append(filing);}
    if(tab==='overview'){countyOverview(county);if(filing)await renderCountyFiling(county,signal,filing);await renderTrellisCounty(county,signal);}
    else if(tab==='litigation'){if(filing)await renderCountyFiling(county,signal,filing);await renderCountyLitigation(county,signal);}
    else if(tab==='rules'){const choices=el('div','county-rules-choices');for(const [category,label] of [['','All saved documents'],['rules','Local rules & orders'],['forms','Forms & documents'],['statutes','Local laws & codes']])choices.append(routeLink(label,`county/${encodeURIComponent(county.geoid)}`,{state:county.state,county:county.geoid,from:route.params.get('from'),tab:'rules',category},`button${(route.params.get('category')||'')===category?' active':''}`));main.append(choices);renderLibrary(false,true);}
    else await renderCountyAccess(county,signal);
    if(!signal.aborted)announce(`${county.name} ${tab==='litigation'?'local rules and filing resources':tab==='rules'?'saved documents':tab==='access'?'court references':'overview'} opened.`);
  }catch(error){if(error.name!=='AbortError')errorState(main,error,render);}
}
function sourceFacetLabel(facet,value){const label=(sourceDirectoryFacets[facet]||[]).find(row=>String(row.value)===String(value))?.label;if(label)return label;if(facet==='jurisdictions'){const known={multi:'Multiple jurisdictions',us:'United States / federal',dc:'District of Columbia'};return known[value]||(/^[a-z]{2}$/i.test(String(value))?String(value).toUpperCase():readable(value));}return human(readable(value));}
function sourceStatus(value){return ({historically_verified:'Historical verification claim',not_verified_in_source:'Unverified in source map'})[value]||human(value)||'Verification not recorded';}
function sourceAccess(value){return ({api:'API listed',download:'Download listed',web:'Website listed'})[value]||'Access method unknown';}
function sourceSummary(data,target){
  const summary=data.summary||{},bar=el('div','source-directory-context');
  append(bar,badge(summary.draft===true?'Draft source map':'Imported source map','neutral'),el('p','','Browse source links and the saved content attached to them.'));
  const facts=el('div','source-directory-totals');for(const [key,label] of [['source_records','source listings'],['unique_urls','distinct URLs'],['saved_content_records','listings with saved content'],['archive_linked_records','listings saved in archive']])if(summary[key]!==undefined)facts.append(el('span','',`${count(summary[key])} ${label}`));if(facts.children.length)bar.append(facts);
  const discovery=el('div','source-map-discovery');if(summary.explicit_api_records!==undefined)discovery.append(routeLink(`${count(summary.explicit_api_records)} API listings →`,'sources',{access_method:'api'}));if(summary.api_bulk_layer_records!==undefined)discovery.append(routeLink(`${count(summary.api_bulk_layer_records)} API & bulk references →`,'sources',{api_bulk:1}));if(summary.saved_content_records)discovery.append(routeLink(`${count(summary.saved_content_records)} with saved content →`,'sources',{has:'saved'}));if(summary.archive_linked_records)discovery.append(routeLink(`${count(summary.archive_linked_records)} saved in archive →`,'sources',{has:'archived'}));discovery.append(routeLink('Browse by state (DOJ directory) →','resources'));if(discovery.children.length)bar.append(discovery);
  const details=el('details','source-map-details');append(details,el('summary','','About this source map'),el('p','',`Source-map date: ${summary.source_as_of?date(summary.source_as_of):'Unknown'}. Historical verification claims belong to that source snapshot.`));if(summary.historical_verified_claims!==undefined)details.append(el('p','',`${count(summary.historical_verified_claims)} historical verification claims in the imported map. These are not fresh checks.`));if(summary.qualification)details.append(el('p','',summary.qualification));if(summary.taxonomy_qualification)details.append(el('p','',summary.taxonomy_qualification));bar.append(details);target.replaceChildren(bar);
}
function sourceDisplayTitle(item){const title=String(item.title||item.host||'Source reference').trim(),subsection=readable(item.subsection||item.source_record?.subsection);return subsection&&['forms','judges','rules/iops','rules / iops','rules','local rules','opinions','orders','dockets'].includes(title.toLowerCase())?`${title[0].toUpperCase()+title.slice(1)} — ${subsection}`:title;}
function archiveRecordsSection(item){
  // Existing saved records that share this exact URL. They are linked, not re-downloaded.
  const section=el('section','source-captures source-archive-records');section.append(el('h2','','Saved records in this archive'));
  const records=Array.isArray(item.archive_records)?item.archive_records:[];
  if(!records.length){section.append(el('p','registry-empty','No previously saved record shares this exact URL. Related content may exist in the archive under a different address.'));return section;}
  const documents=records.filter(r=>r.kind!=='judge_entity'),judgesFound=records.filter(r=>r.kind==='judge_entity');
  section.append(el('p','section-intro','These records were saved earlier by other collections and share this exact source URL. Saved dates record collection time, not legal currency; review flags and publication status are shown as recorded.'));
  if(documents.length){const grid=el('div','source-capture-grid');
    for(const record of documents){const card=el('article','source-capture-card archive-record-card');
      append(card,el('h3','',record.title||(record.kind==='retained_capture'?'Retained capture':'Saved record')),el('p','archive-record-publication',record.publication_label||human(record.publication)));
      const meta=el('p','archive-record-meta');const parts=[];if(record.dataset)parts.push(datasetName(record.dataset));if(record.collection)parts.push(`Collection: ${record.collection}`);if(record.record_kind)parts.push(human(record.record_kind));if(record.state)parts.push([record.county,record.state].filter(Boolean).join(', '));meta.textContent=parts.join(' · ');if(parts.length)card.append(meta);
      const when=record.captured_at||record.retrieved_at;card.append(el('p','',`Saved: ${when?date(when,true):'Unknown'}${record.usable_text===false?' · No usable text extracted':''}`));
      if(record.match_basis==='exact_final_url')card.append(el('p','publication-note',`Matched on the address this capture was redirected to${record.recorded_source_url?` (requested: ${record.recorded_source_url})`:''}.`));
      if(Array.isArray(record.review_flags)&&record.review_flags.length)card.append(el('p','publication-note',`Review flags: ${record.review_flags.map(human).join(', ')}`));
      if(record.quality)card.append(el('p','archive-record-quality',readable(record.quality)));
      const actions=el('div','button-row');if(record.kind==='directory_record'){const open=action('Open saved record',event=>openRecord({id:record.record_id},event.currentTarget));actions.append(open);}
      append(actions,record.original_url?link('Open original file ↗',record.original_url):null,record.text_url?link('Read saved text ↗',record.text_url):null);card.append(actions);grid.append(card);}
    section.append(grid);}
  if(judgesFound.length){const block=el('div','source-judge-block');append(block,el('h3','',`${count(judgesFound.length)} judge profile${judgesFound.length===1?'':'s'} recorded from this page`),el('p','coverage-footnote','Each profile keeps its own source observation and capture date. Current service is not independently verified by this link.'));
    const list=el('div','source-judge-links');let shown=0;const more=action('Show all judges',()=>draw(judgesFound.length));
    function draw(limit){for(const judge of judgesFound.slice(shown,limit)){list.append(routeLink(judge.name||'Judge profile',judge.profile_route||`judge/${encodeURIComponent(judge.record_id)}`,{from:location.hash},'button-link'));}shown=Math.min(judgesFound.length,limit);more.hidden=shown>=judgesFound.length;}
    draw(Math.min(judgesFound.length,12));append(block,list,more);section.append(block);}
  return section;
}
function availabilityBadges(target,item){
  // Two separate facts: a capture attached to this exact URL, and existing archive records that share it.
  if(item.has_saved_content)target.append(badge('Saved content attached',''));
  if(item.has_archive_records){const n=Number(item.archive_record_count)||0;target.append(badge(n?`Saved in archive · ${count(n)} record${n===1?'':'s'}`:'Saved in archive',''));}
  if(!item.has_saved_content&&!item.has_archive_records)target.append(badge('Directory reference','neutral'));
}
function sourceCard(item){
  const card=el('article','source-card'),context=[item.jurisdiction_label||sourceFacetLabel('jurisdictions',item.jurisdiction),sourceFacetLabel('categories',item.category)].filter(Boolean).join(' · ');if(context)card.append(el('p','source-card-context',context));
  append(card,routeLink(sourceDisplayTitle(item),`source/${encodeURIComponent(item.id)}`,{from:location.hash},'source-card-title'),el('p','source-host',item.host||''));
  const flags=el('div','source-card-flags');append(flags,badge(sourceAccess(item.access_method),'neutral'));if(item.content_kind&&item.content_kind!=='page'&&item.content_kind!=='api')flags.append(badge(String(item.content_kind).toUpperCase(),'neutral'));if(item.source_type&&item.source_type!=='official')flags.append(badge(({nonprofit_or_assoc:'Nonprofit / association',commercial_or_thirdparty:'Third-party publisher',institutional:'Institutional'})[item.source_type]||human(item.source_type),'amber'));availabilityBadges(flags,item);card.append(flags);
  append(card,el('p','source-status',sourceStatus(item.verification_status)));
  if(item.notes)card.append(el('p','source-note-preview',readable(item.notes)));
  const actions=el('div','source-card-actions');append(actions,routeLink('View source details →',`source/${encodeURIComponent(item.id)}`,{from:location.hash}),link('Visit source ↗',item.url,true,'button-link'));card.append(actions);return card;
}
async function renderSourceDirectory(){
  heading('Source directory',views.sources.description,'Research references');
  const form=el('form','filters source-filters');form.setAttribute('aria-label','Filter source directory');
  const query=filterField('Search sources','q','search',route.params.get('q'),'Publisher, court, dataset or topic');form.append(query.label);
  const fields=[['jurisdiction','Jurisdiction','All jurisdictions','jurisdictions'],['category','Category','All categories','categories'],['task_family','Research task','Any research task','task_families'],['content_kind','File type','Any file type','content_kinds'],['has','Saved content','Any content','availability'],['layer','Source layer','Any source layer','layers',true],['source_type','Publisher type','Any publisher type','source_types',true],['access_requirements','Access requirement','Any access requirement','access_requirements',true],['access_method','Access method','Any access method','access_methods',true],['verification_status','Verification status','Any source-map status','statuses',true]];
  // Less common reference-type filters stay available without crowding the form.
  const more=el('details','source-more-filters'),moreGrid=el('div','source-more-grid');append(more,el('summary','','More filters'),moreGrid);
  for(const [key,label,first,,secondary] of fields){const field=filterField(label,key,'select',route.params.get(key));setOptions(field.input,key==='has'?[{value:'saved',label:'Saved content attached'},{value:'archived',label:'Saved in archive'},{value:'links_only',label:'Directory links only'}]:[],first,route.params.get(key));(secondary?moreGrid:form).append(field.label);if(secondary&&route.params.get(key))more.open=true;}
  append(form,more,action('Clear filters',()=>navigate('sources')));main.append(form);
  const selected=filterSummary(),context=el('div'),results=el('section','source-results');results.setAttribute('aria-label','Source directory results');append(main,selected,context,results);
  async function load(){pageController?.abort();pageController=new AbortController();const signal=pageController.signal;loading(results,'Finding source references…');activeFilters(form,selected);
    const params=new URLSearchParams(route.params);params.set('limit','30');params.set('page',Math.max(1,Number(params.get('page'))||1));
    try{const data=await api(`/api/sources?${params}`,signal);if(signal.aborted)return;if(data.ready===false)throw new Error('Source directory not ready');results.removeAttribute('aria-busy');results.replaceChildren();
      sourceDirectoryFacets=data.facets||{};for(const [key,,first,facet] of fields){const options=data.facets?.[facet]||[],requested=route.params.get(key)||'',selected=options.find(row=>String(row.value).toLowerCase()===requested.toLowerCase()||String(row.label).toLowerCase()===requested.toLowerCase())?.value||requested;setOptions(form.querySelector(`[name="${key}"]`),options.map(row=>({...row,label:row.count===undefined?row.label:`${row.label} (${count(row.count)})`})),first,selected);}activeFilters(form,selected);sourceSummary(data,context);
      const total=Number(data.total)||0;append(results,append(el('div','results-heading'),el('strong','',`${count(total)} source reference${total===1?'':'s'}`),el('small','','Listings and attached captures are distinct')));
      if(!data.items?.length)emptyState(results,'No matching sources','Try another search or remove a filter.',()=>navigate('sources'));else{const cards=el('div','source-grid');for(const item of data.items)cards.append(sourceCard(item));results.append(cards);}results.append(pagination(total,Number(data.page)||1,Number(data.limit)||30));announce(`${count(total)} source references found.`);
    }catch(error){if(error.name==='AbortError')return;results.removeAttribute('aria-busy');results.replaceChildren();append(results,el('div','registry-empty','The source directory is not available right now. Your saved documents remain available.'),append(el('div','button-row'),action('Try again',load),routeLink('Browse saved documents →','documents',{},'button')));announce('Source directory unavailable. Saved documents remain available.');}
  }
  const submit=()=>{clearTimeout(filterTimer);const params={};for(const field of form.querySelectorAll('input,select'))if(field.value.trim())params[field.name]=field.value.trim();if(route.params.get('api_bulk'))params.api_bulk=route.params.get('api_bulk');history.replaceState(null,'',makeHash('sources',params));route=readRoute();load();};
  form.addEventListener('submit',event=>{event.preventDefault();submit();});form.addEventListener('change',event=>{if(event.target.tagName==='SELECT')submit();});form.addEventListener('input',event=>{if(event.target.tagName!=='INPUT')return;clearTimeout(filterTimer);filterTimer=setTimeout(submit,350);});await load();
}
function apiReferencePanel(reference){
  if(!reference)return null;const section=el('section','source-api-reference');append(section,el('h2','','API & bulk access'),el('p','section-intro','Documentation and access requirements for this reference. A documentation review does not establish a working API connection.'));
  const requirements=reference.access_requirements||{},facts=el('dl','record-meta');metaRow(facts,'Reference type',human(reference.reference_class)||'Unknown');metaRow(facts,'Requirements status',human(requirements.status)||'Unknown');metaRow(facts,'Access method',readable(requirements.method)||'Unknown');metaRow(facts,'Documentation reviewed',reference.requirements_reviewed_at?date(reference.requirements_reviewed_at):'Unknown');metaRow(facts,'Live API query',reference.live_api_query_performed===false?'Not performed':reference.live_api_query_performed===true?'Recorded':'Unknown');section.append(facts);
  if(requirements.note)section.append(el('p','api-reference-note',readable(requirements.note)));if(reference.qualification)section.append(el('p','api-reference-note',readable(reference.qualification)));if(typeof requirements.source==='string'&&/^https?:\/\//i.test(requirements.source))append(section,link('Open access documentation ↗',requirements.source,true,'button-link'));return section;
}
async function renderSourceDetail(signal){
  loading(main,'Opening source reference…');
  try{const item=await api(`/api/source?id=${encodeURIComponent(route.id||'')}`,signal);if(signal.aborted)return;main.removeAttribute('aria-busy');main.replaceChildren();
    const back=routeLink('← Back to source directory','sources',{},'back-link'),from=route.params.get('from');if(from&&/^#sources(?:\?|$)/.test(from))back.href=from;main.append(back);
    heading(sourceDisplayTitle(item),[item.jurisdiction_label||sourceFacetLabel('jurisdictions',item.jurisdiction),sourceFacetLabel('categories',item.category)].filter(Boolean).join(' · '),'Source directory');document.title=`${sourceDisplayTitle(item)} · Legal Archive`;
    const flags=el('div','source-detail-flags');append(flags,badge(sourceAccess(item.access_method),'neutral'),badge(sourceStatus(item.verification_status),'neutral'));availabilityBadges(flags,item);main.append(flags);
    const intro=el('div','source-detail-intro');append(intro,link('Visit source ↗',item.url,true,'button button-primary'),el('p','',`Source-map date: ${item.source_as_of?date(item.source_as_of):'Unknown'}. Historical source claims are not a current access check.`));main.append(intro);
    const layout=el('div','source-detail-layout'),content=el('section'),aside=el('aside','profile-at-glance');content.append(el('h2','','About this source'));const description=readable(item.description||item.source_record?.description),notes=readable(item.notes);if(description)content.append(prose(description));if(notes&&notes!==description){if(description)content.append(el('h3','source-notes-heading','Source-map notes'));content.append(prose(notes));}if(!description&&!notes)content.append(el('p','quiet-empty','No additional description is recorded in the source map.'));
    if(Array.isArray(item.tags)&&item.tags.length){const tags=el('div','source-tags'),seen=new Set();for(const tag of item.tags){const key=String(tag).trim().toLowerCase();if(!key||seen.has(key))continue;seen.add(key);tags.append(badge(key==='api'?'API':human(tag),'neutral'));}content.append(tags);}
    aside.append(el('h2','','Access reference'));const facts=el('dl','profile-facts');metaRow(facts,'Host',item.host||'Unknown');metaRow(facts,'Method',sourceAccess(item.access_method));metaRow(facts,'Source-map access tag',readable(item.access_requirements)||'Unknown');if(item.access_method==='api')metaRow(facts,'API access','Not tested');metaRow(facts,'Listed in',readable(item.section)||'Not recorded');aside.append(facts);append(layout,content,aside);main.append(layout);
    append(main,apiReferencePanel(item.api_reference));
    const captures=el('section','source-captures');captures.append(el('h2','','Saved content'));
    if(item.captures?.length){captures.append(el('p','section-intro','These files capture the listed source URL. Linked PDFs and individual profiles may not be included. A capture date does not establish legal currency.'));const cards=el('div','source-capture-grid');for(const capture of item.captures){const card=el('article','source-capture-card');append(card,el('h3','',capture.title||'Saved capture'),el('p','',human(capture.type)||'Saved representation'),el('p','',`Captured: ${capture.captured_at?date(capture.captured_at,true):'Unknown'}`));if(capture.qualification)card.append(el('p','',readable(capture.qualification)));const actions=el('div','button-row');append(actions,link('Open saved file ↗',capture.original_url),link('Read saved text ↗',capture.text_url));card.append(actions);cards.append(card);}captures.append(cards);}else captures.append(el('p','registry-empty',item.has_saved_content?'Saved content is associated with this listing, but no files are attached in this view.':'No saved content is attached to this directory reference. Use the source link to visit its publisher.'));main.append(captures);
    main.append(archiveRecordsSection(item));
    const evidence=el('details','library-details source-observations');append(evidence,el('summary','','Source-map observations & provenance'));const originalTitle=el('dl','record-meta');metaRow(originalTitle,'Original listing title',item.title);evidence.append(originalTitle);
    for(const observation of item.observations||[]){const row=el('article','source-observation');append(row,el('h3','',observation.title||'Source-map observation'));const facts=el('dl','record-meta');metaRow(facts,'Source line',observation.line_number);metaRow(facts,'Section',[observation.section,observation.subsection].filter(Boolean).join(' / '));metaRow(facts,'Source-map date',observation.source_as_of?date(observation.source_as_of):'Unknown');row.append(facts);if(observation.notes)row.append(el('p','',readable(observation.notes)));if(observation.verbatim){const original=el('details','metadata-details');append(original,el('summary','','Original map entry'),el('pre','',observation.verbatim));row.append(original);}evidence.append(row);}
    if(item.source_record){const raw=el('details','metadata-details');append(raw,el('summary','','Original structured record'),el('pre','',JSON.stringify(item.source_record,null,2)));evidence.append(raw);}main.append(evidence);announce('Source reference opened.');
  }catch(error){if(error.name==='AbortError')return;main.removeAttribute('aria-busy');main.replaceChildren();heading('Source reference unavailable','The source may not be indexed yet. Saved documents remain available.','Source directory');append(main,routeLink('← Back to source directory','sources',{},'button'),action('Try again',()=>renderSourceDetail(pageController.signal)));}
}
async function renderCollections(signal){
  heading('Curated collections','Case material, reference collections and court directories gathered around a common subject.','Your library');const target=el('section','dataset-grid');main.append(target);loading(target,'Opening collections…');
  try{const response=await api('/api/collections',signal);if(signal.aborted)return;target.removeAttribute('aria-busy');target.replaceChildren();const items=Array.isArray(response)?response:response.items||[];
    for(const item of items){const card=el('article','panel curated-card');append(card,el('p','eyebrow',item.count_label||'Saved collection'),el('h2','',item.title),el('p','',item.description),el('p','collection-size',`${count(item.record_count)} ${item.count_label||'records'}`),routeLink('Explore collection →',`collection/${encodeURIComponent(item.id)}`));target.append(card);}if(!items.length)emptyState(target,'No collections available','Curated collections will appear here when they are ready.');
  }catch(error){if(error.name!=='AbortError')errorState(target,error,render);}
}
async function renderCollection(signal){
  loading(main,'Opening collection…');try{const params=new URLSearchParams(route.params);params.set('id',route.id);params.set('page_size','20');const data=await api(`/api/collection?${params}`,signal);if(signal.aborted)return;main.replaceChildren();main.removeAttribute('aria-busy');{const home=({'mdl-3080':['← MDL 3080','mdl/3080'],settlements:['← Settlements','settlements'],'court-registries':['← Courts','courts'],'court-assets':['← Courts','courts']})[route.id]||['← MDLs','mdls'];main.append(routeLink(home[0],home[1],{},'back-link'));}heading(data.title,data.description,'Curated collection');document.title=`${data.title} · Legal Archive`;
    const context=el('div','collection-overview');append(context,el('strong','',`${count(data.record_count)} ${data.count_label||'records'}`),data.scope_note?el('p','',data.scope_note):null);if(data.attribution)context.append(el('p','collection-attribution',data.attribution));main.append(context);
    const form=el('form','collection-search');form.setAttribute('aria-label','Search collection previews');const search=filterField('Find in available previews','collection-query','search',route.params.get('q'),'Search this collection');const button=el('button','button','Search');button.type='submit';append(form,search.label,button);form.addEventListener('submit',event=>{event.preventDefault();navigate(`collection/${encodeURIComponent(route.id)}`,{q:search.input.value.trim()});});main.append(form);
    const list=el('section','collection-documents');list.setAttribute('aria-label','Collection documents');for(const item of data.items||[]){const card=el('article','collection-document');append(card,el('p','card-location',[item.court_label,item.state].filter(Boolean).join(' · ')),el('h2','',item.title||'Collection document'),item.description?el('p','',item.description):null);const facts=el('dl','collection-facts');metaRow(facts,'Reported status',item.status);metaRow(facts,'Claim deadline',item.claim_deadline);metaRow(facts,'Status as of',item.status_as_of);if(facts.children.length)card.append(facts);const actions=el('div','button-row'),original=item.asset_id?`/library-assets/${encodeURIComponent(item.asset_id)}`:null,text=item.text_asset_id?`/library-assets/${encodeURIComponent(item.text_asset_id)}`:null;
      if(item.excerpt)actions.append(action('Read preview',event=>{lastTrigger=event.currentTarget;dialog.showModal();renderRecord({id:item.id,title:item.title,state:item.state,kind:item.resource_kind,text:item.excerpt,has_text:true,text_truncated:true,original_url:original,text_url:text,source_url:item.source_url,metadata:{source_date:item.source_date,status:item.status,status_as_of:item.status_as_of,attribution:item.attribution},quality:item.quality_notes,reading_notes:{method:'Curated source excerpt',original_preserved:Boolean(original)}});document.querySelector('#close-record').focus();},'button-primary'));
      append(actions,link('Open document ↗',original),link('Full text ↗',text),link('Publisher page ↗',item.source_url,true),link('Official website ↗',item.official_url,true));card.append(actions);if(item.documents?.length){const files=el('details','collection-evidence');files.append(el('summary','','Related reports & documents'));for(const doc of item.documents){const row=el('div','button-row');append(row,link(doc.title||'View report',doc.asset_id?`/library-assets/${encodeURIComponent(doc.asset_id)}`:doc.source_url,!doc.asset_id,'button-link'));files.append(row);}card.append(files);}if(item.proof_requirement){const terms=el('details','collection-evidence');append(terms,el('summary','','Eligibility & proof details'),el('p','',item.proof_requirement));card.append(terms);}list.append(card);}if(!(data.items||[]).length)emptyState(list,'No matching previews','Try a shorter search or browse all available previews.',()=>navigate(`collection/${encodeURIComponent(route.id)}`));main.append(list);main.append(pagination(data.total,data.page||1,data.page_size||20));
    const details=el('details','library-details');append(details,el('summary','','Collection details'),data.source_url?link('Collection source ↗',data.source_url,true,'button-link'):null,el('pre','metadata-json',JSON.stringify(Object.fromEntries(Object.entries(data).filter(([key])=>key!=='items')),null,2)));main.append(details);
  }catch(error){if(error.name!=='AbortError')errorState(main,error,render);}
}
function metaRow(list,label,value) { const display=readable(value);if(!display)return;append(list,el('dt','',label),el('dd','',display)); }
function prose(value,className='reading-content') {
  const box=el('div',className),content=typeof value==='string'?value:readable(value);
  for(const paragraph of content.split(/\n\s*\n/).filter(s=>s.trim()))box.append(el('p','',paragraph.trim()));
  return box;
}
function rawStructuredText(value) {
  if(typeof value!=='string' || !/^[\s]*[\[{]/.test(value))return false;
  try {const p=JSON.parse(value);return p!==null && typeof p==='object';}catch{return false;}
}
function visibleQuality(value) {
  if(typeof value==='string')return ({professional_evidence_linked:'Identity linked through corroborating professional evidence.',publisher_or_prior_confirmed_identity:'Identity linked by publisher identifiers or previously verified evidence.',source_identity_only:'Identity recorded by a single source.'})[value]||value;
  if(Array.isArray(value))return value.filter(v=>typeof v==='string').join('\n');
  if(!value||typeof value!=='object')return '';
  const labels={status:'Status',authority:'Authority',extraction_status:'Text extraction',text_status:'Text',limitations:'Limitations',warnings:'Source caution',caution:'Source caution',note:'Note'};
  return Object.entries(labels).filter(([key])=>value[key]).map(([key,label])=>`${label}: ${readable(value[key])}`).join('\n');
}
function readingNote(value) {
  if(typeof value==='string')return value;
  if(Array.isArray(value))return value.filter(v=>typeof v==='string').join('\n');
  if(!value||typeof value!=='object')return '';
  const methods={plain_text_spacing:'Paragraph spacing adjusted.',html_main_with_navigation_removed:'Main page content shown with navigation removed.',provider_markdown_cleanup:'Source formatting simplified for reading.',structured_body_field:'Readable content taken from the structured source record.',metadata_without_readable_body:'This source contains metadata without a readable document body.','Source-bound judge profile':'Profile assembled from preserved judge observations.'};
  const ocr=value.ocr&&typeof value.ocr==='object'?value.ocr:null;
  const recovery=value.recovery&&typeof value.recovery==='object'?value.recovery:null;
  const recoveredOCR=recovery&&/tesseract|ocr/i.test(recovery.method||'');
  const parts=[ocr||recoveredOCR?(value.original_preserved===true?'OCR-derived reading text; original preserved.':'OCR-derived reading text.'):(value.original_preserved===true?'Cleaned reading copy; original preserved.':'Derived reading copy.')];
  if(recovery){
    if(recovery.method==='word_com_native')parts.push('Readable text was recovered from the original legacy Word or RTF document.');
    else if(recoveredOCR)parts.push('A later OCR recovery pass added readable text.');
    else parts.push('A later recovery pass added readable text.');
    parts.push('Earlier extraction-gap flags remain in the source metadata.');
    const caution=visibleQuality(recovery.quality_notes);if(caution)parts.push(caution);
  }
  if(ocr){
    const completed=ocr.ocr_pages_completed,required=ocr.ocr_pages_required;
    if(typeof completed==='number'&&typeof required==='number')parts.push(`${count(completed)}/${count(required)} required pages processed.`);
    if(ocr.ocr_scope&&ocr.ocr_scope!=='all_pages')parts.push(`OCR scope: ${human(ocr.ocr_scope)}.`);
    if(typeof ocr.pdf_pages==='number'&&typeof required==='number'&&ocr.pdf_pages>required)parts.push(`The original document contains ${count(ocr.pdf_pages)} pages.`);
    if(typeof ocr.mean_page_confidence==='number'&&Number.isFinite(ocr.mean_page_confidence))parts.push(`Mean engine confidence: ${Number(ocr.mean_page_confidence.toFixed(1))}; this is not verified text accuracy.`);
    const flagged=Array.isArray(ocr.pages_below_70_confidence)?ocr.pages_below_70_confidence.length:ocr.pages_below_70_confidence;
    if(typeof flagged==='number'&&flagged>0)parts.push(`${count(flagged)} page${flagged===1?'':'s'} flagged for low OCR confidence; check the original.`);
    if(typeof completed==='number'&&typeof required==='number'&&completed<required)parts.push('OCR coverage of the required pages is incomplete.');
  }
  if(value.method)parts.push(methods[value.method]||human(value.method));
  if(value.html_fallback)parts.push('The page structure could not be fully parsed; check the saved original.');
  for(const key of ['warning','warnings','caution','limitations'])if(value[key])parts.push(readable(value[key]));
  return parts.join(' ');
}
function fieldSection(parent,label,values) {
  const items=(Array.isArray(values)?values:[values]).map(v=>readable(v)).filter(Boolean);if(!items.length)return;
  const section=el('section','profile-section');section.append(el('h3','',label));const list=el('ul','profile-list');for(const item of [...new Set(items)])list.append(el('li','',item));section.append(list);parent.append(section);
}
function renderSources(parent,item,profile) {
  const sources=Array.isArray(item.source_records)&&item.source_records.length?item.source_records:Array.isArray(profile.members)?profile.members:[];
  if(!sources.length)return;
  const section=el('section','record-sources');append(section,el('h3','',`${count(sources.length)} source record${sources.length===1?'':'s'}`),el('p','coverage-footnote','Each source keeps its own capture date, version and claims. Grouping preserves differing historical statements.'));
  const grid=el('div','source-card-grid');let shown=0;
  const more=action('Show more sources',()=>draw());
  function draw(){for(const source of sources.slice(shown,shown+20)){const m=source.metadata||{},card=el('article','source-card');append(card,el('h4','',source.title||source.name||datasetName(source.dataset)||'Saved source'),el('p','source-card-note',[datasetName(source.dataset),human(source.source_class||source.kind)].filter(Boolean).join(' · ')));
    const member=(profile.members||[]).find(v=>v.dataset===source.dataset&&v.source_url===source.source_url)||{};
    const captured=source.captured_at||source.capture_time||m.captured_at||member.captured_at;const list=el('dl','source-facts');if(captured && readable(captured))metaRow(list,'Captured',Array.isArray(captured)?captured.map(v=>date(v,true)):date(captured,true));metaRow(list,'Status',source.status||m.status);if(list.children.length)card.append(list);
    const actions=el('div','button-row');append(actions,link('Original publisher ↗',source.source_url||source.url,true,'button-link'),link('Saved file ↗',source.original_url,false,'button-link'),link('Readable text ↗',source.text_url,false,'button-link'));
    if(source.id && source.id!==item.id && !isOfficialSourceProfile(profile))actions.append(action('Inspect source',event=>openRecord(source,event.currentTarget),'button-small'));card.append(actions);grid.append(card);}
    shown=Math.min(sources.length,shown+20);more.hidden=shown>=sources.length;more.textContent=`Show more sources (${count(sources.length-shown)} remaining)`;
  }
  append(section,grid,more);parent.append(section);draw();
}
function analysisPeriod(value){if(!value||typeof value!=='object')return value||'Unknown';const window=value.cycle_survey_window;if(window)return [window.start&&window.end?`${date(window.start)} – ${date(window.end)}`:date(window.start||window.end),value.evaluation_cycle?`${value.evaluation_cycle} evaluation cycle`:null].filter(Boolean).join(' · ');if(value.label)return value.label;if(value.evaluation_cycle)return `${value.evaluation_cycle} evaluation cycle`;return readable(Object.fromEntries(Object.entries(value).filter(([key])=>!/source|url|anchor/i.test(key))))||'Unknown';}
function analysisFamily(a){
  const metric=a.metric||'',type=a.analysis_type||'';
  if(['commission_performance_evaluation','commission_vote','survey_overall_rating'].includes(type))return 'Evaluations & surveys';
  if(metric==='motion_case_result_list_count')return 'Result-list counts';
  if(type==='public_preview_motion_count'||['motion_type_cases','motion_outcome_cases'].includes(metric))return 'Motion measures';
  if(type==='public_preview_citation_frequency')return 'Citation frequencies';
  if(metric==='opinions_by_area_of_law')return 'Areas of law';
  if(type==='vendor_reported_case_assignment_count')return 'Case assignments';
  return 'Other published measures';
}
function analysisBasis(a){
  if(a.component==='context'||a.record_class==='public_ui_preview')return 'Saved publisher preview';
  if(['commission_performance_evaluation','commission_vote','survey_overall_rating'].includes(a.analysis_type))return 'Judicial performance evaluation';
  if(a.analysis_type==='vendor_reported_case_assignment_count')return 'Published assignment count';
  return a.source_reported?'Source-reported measure':'Saved analysis record';
}
function renderAnalysisReferences(parent,references){
  const rows=(Array.isArray(references)?references:[]).filter(item=>item.availability==='publisher_link'&&safeURL(item.url||item.source_url,true));
  if(!rows.length)return;
  const section=el('section','analysis-references');append(section,el('h2','','Report & dashboard links'),el('p','analysis-intro','These references were observed on the saved judge profile. Their report or dashboard contents are not saved here.'));
  const list=el('div','profile-documents');for(const item of rows){const card=el('article','profile-document');append(card,badge('Link only','neutral'),el('h3','',item.title||item.label||'Publisher report'),el('p','source-card-note',[item.publisher,human(item.kind)].filter(Boolean).join(' · ')),link('Open publisher reference ↗',item.url||item.source_url,true,'button-link'));list.append(card);}append(section,list);parent.append(section);
}
function renderAnalysis(parent,analyses,references=[],options={}) {
  const rows=(Array.isArray(analyses)?analyses:[]).map(row=>({raw:row,data:row.payload||row}));
  if(!rows.length){if(options.showEmpty){const empty=el('section','analysis-empty');append(empty,el('h2','','No analysis measures saved'),el('p','','This profile has no saved outcome measures, evaluations or citation analysis. Its biography and linked sources may still be useful.'));parent.append(empty);}renderAnalysisReferences(parent,references);return;}
  const section=el('section','profile-section judge-analysis');append(section,el('h2','','Saved analysis & evaluations'),el('p','analysis-intro','Explore the publisher’s reported measures. Counts may overlap; no win rate, ranking or prediction is calculated.'));
  const periods=rows.filter(row=>Boolean(row.data.period)).length,scope=el('div','analysis-scope');append(scope,el('strong','',`${count(rows.length)} saved measures`),el('span','',periods===rows.length?'Reporting period recorded for every measure':`${count(rows.length-periods)} without a stated reporting period`));section.append(scope);
  let filtered=rows,shown=0;const list=el('div','analysis-list'),result=el('p','analysis-results');result.setAttribute('role','status');const more=action('Show more measures',()=>draw());
  let query,kind;
  if(rows.length>18){const form=el('form','analysis-filters');form.setAttribute('aria-label','Filter saved analysis');query=filterField('Find a measure','analysis_q','search',options.preserveFilters?route.params.get('analysis_q')||'':'','Motion, cited case or reported measure');kind=filterField('Measure type','analysis_type','select',options.preserveFilters?route.params.get('analysis_type')||'':'');const families=[...new Set(rows.map(row=>analysisFamily(row.data)))].sort();setOptions(kind.input,families.map(value=>({value,label:`${value} (${count(rows.filter(row=>analysisFamily(row.data)===value).length)})`})),'All measure types',options.preserveFilters?route.params.get('analysis_type')||'':'');append(form,query.label,kind.label);form.addEventListener('submit',event=>event.preventDefault());query.input.addEventListener('input',applyFilters);kind.input.addEventListener('change',applyFilters);section.append(form);}
  function applyFilters(){const term=query?.input.value.trim().toLocaleLowerCase()||'',family=kind?.input.value||'';filtered=rows.filter(row=>(!family||analysisFamily(row.data)===family)&&(!term||[row.data.label,row.data.metric,row.data.motion_type,row.data.outcome,row.data.publisher].filter(Boolean).join(' ').toLocaleLowerCase().includes(term)));shown=0;list.replaceChildren();if(options.preserveFilters){const params=Object.fromEntries(route.params);for(const [key,value] of [['analysis_q',query?.input.value||''],['analysis_type',family]]){if(value)params[key]=value;else delete params[key];}history.replaceState(null,'',makeHash(`judge/${encodeURIComponent(route.id)}`,params));route=readRoute();}draw();}
  function draw(){for(const row of filtered.slice(shown,shown+18)){const a=row.data,analysis=row.raw,card=el('article','analysis-card');const label=a.label||human(a.motion_type)||human(a.metric)||human(a.analysis_type)||'Reported measure';const hasScale=a.scale_maximum!==undefined&&a.scale_maximum!==null&&Number.isFinite(Number(a.scale_maximum));const nativeValue=a.value_as_reported??a.value,value=hasScale?`${readable(nativeValue)} / ${Number(a.scale_maximum)}`:a.unit==='categorical'?human(nativeValue):[readable(nativeValue),human((a.unit||'').replace(/\s*\(native heading:[^)]+\)/i,''))].filter(Boolean).join(' ');append(card,el('p','analysis-publisher',a.publisher||datasetName(a.dataset||analysis.dataset)||'Published measure'),el('p','analysis-basis',analysisBasis(a)),el('h3','',[label,a.outcome].filter(Boolean).join(' · ')),el('div','analysis-value',value));if(hasScale){const scale=[a.scale_label,a.scale_minimum!==undefined&&a.scale_minimum!==null?`Reported scale: ${a.scale_minimum}–${a.scale_maximum}`:'Source-reported survey scale'].filter(Boolean).join(' · ');card.append(el('p','analysis-interpretation',scale));}
    const facts=el('dl','source-facts');metaRow(facts,'Period',analysisPeriod(a.period));metaRow(facts,'Saved',a.captured_at?date(a.captured_at):'Unknown');for(const [key,title] of [['cohort','Case group'],['sample_size','Sample size'],['numerator','Numerator'],['denominator','Denominator']])metaRow(facts,title,a[key]);card.append(facts);if(a.interpretation)card.append(el('p','analysis-interpretation',readable(a.interpretation)));
    const details=el('details','analysis-details');details.append(el('summary','','Method & source'));const anchor=a.source_anchor||analysis.input_evidence||{},evidence=a.evidence||{};if(a.sample_size==null&&a.denominator==null)details.append(el('p','source-card-note','Sample size and denominator are not stated in this saved measure.'));append(details,prose(readable(a.limitations),'source-card-note'),link('Publisher source ↗',a.source_url||anchor.source_url||evidence.source_url,true,'button-link'),link('Methodology ↗',a.methodology_url,true,'button-link'));if(a.native_chart_heading)details.append(el('p','source-card-note',`Publisher heading: ${a.native_chart_heading}`));card.append(details);list.append(card);}
    shown=Math.min(filtered.length,shown+18);more.hidden=shown>=filtered.length;more.textContent=`Show more measures (${count(filtered.length-shown)} remaining)`;result.textContent=filtered.length?`Showing ${count(shown)} of ${count(filtered.length)} measures${filtered.length<rows.length?' matching these filters':''}`:'No saved measures match these filters.';
  }append(section,result,list,more);parent.append(section);applyFilters();renderAnalysisReferences(parent,references);
}
function renderJudge(parent,profile) {
  const native=profile.native_record||profile,entity=Boolean(profile.entity_id);
  const context=el('dl','record-meta');for(const [key,label] of [['publisher','Publisher'],['judge_system','Court system'],['judge_systems','Court systems'],['courts','Reported courts'],['state_labels','Jurisdictions']])metaRow(context,label,profile[key]||native[key]);if(context.children.length)parent.append(context);
  const aliases=(profile.aliases||[]).map(value=>typeof value==='string'?value:value.name).filter(value=>value&&value!==profile.name);if(aliases.length)parent.append(el('p','source-card-note',`Also recorded as: ${[...new Set(aliases)].join('; ')}`));
  const biography=profile.biography||native.biography;let populated=false;
  if(biography&&readable(biography)){append(parent,el('h3','profile-heading','Biography'),prose(Array.isArray(biography)?biography.map(value=>readable(value)).join('\n\n'):biography));populated=true;}
  for(const [key,label] of [['education','Education'],['appointments','Appointments & historical service'],['service','Other reported service'],['professional_career','Professional career']]){const value=profile[key]||native[key];if(value&&readable(value)){fieldSection(parent,label,value);populated=true;}}
  if(entity){const label=({source_identity_only:'Single source identity',publisher_or_prior_confirmed_identity:'Linked publisher or previously verified identity',professional_evidence_linked:'Linked through corroborating professional evidence'})[profile.identity_status]||human(profile.identity_status);const note=el('div','quality-note');note.textContent=[label,`${count(profile.member_count)} source observations retained. Current judicial service has not been independently verified.`].filter(Boolean).join('\n');parent.append(note);}
  renderAnalysis(parent,profile.analyses||native.analyses);
  if(profile.field_provenance){const details=el('details','metadata-details readable-evidence');details.append(el('summary','','Profile field evidence'));for(const [field,claims] of Object.entries(profile.field_provenance)){if(!Array.isArray(claims)||!claims.length)continue;details.append(el('h4','',human(field)));for(const claim of claims){const row=el('div','field-evidence');const anchor=claim.source_anchor||{};append(row,el('p','',readable(claim.value||claim.courts_as_reported||claim.states)),el('small','',`${human(claim.dataset)||'Source observation'}${claim.captured_at?' · '+date(claim.captured_at):''}`),link('Source ↗',claim.source_url||anchor.source_url,true,'button-link'));details.append(row);}}parent.append(details);}
  return entity && populated;
}
const judgeProfileCache=new Map();
const peopleSectionLists={judges:'#judges',people:'#people'};
function savedPeopleList(hash,view){return typeof hash==='string'&&new RegExp(`^#${view}(?:\\?|$)`).test(hash)?hash:null;}
function peopleSectionTargets(){
  if(route.view==='judges')peopleSectionLists.judges=makeHash('judges',Object.fromEntries(route.params));
  if(route.view==='people')peopleSectionLists.people=makeHash('people',Object.fromEntries(route.params));
  if(route.view==='judge')peopleSectionLists.judges=savedPeopleList(route.params.get('from'),'judges')||peopleSectionLists.judges;
  if(route.view==='person')peopleSectionLists.people=savedPeopleList(route.params.get('from'),'people')||peopleSectionLists.people;
  if(['people','person'].includes(route.view)){
    const historicalParams=new URLSearchParams(peopleSectionLists.people.split('?')[1]||'');
    peopleSectionLists.judges=savedPeopleList(historicalParams.get('from'),'judges')||peopleSectionLists.judges;
  }
  const historicalParams=new URLSearchParams(peopleSectionLists.people.split('?')[1]||'');historicalParams.set('from',peopleSectionLists.judges);
  return {judges:peopleSectionLists.judges,people:makeHash('people',Object.fromEntries(historicalParams))};
}
function refreshPeopleSectionNavigation(nav){
  const targets=peopleSectionTargets(),selected=['people','person'].includes(route.view)?'people':'judges';
  for(const anchor of nav.children){const active=anchor.dataset.section===selected;anchor.href=targets[anchor.dataset.section];anchor.classList.toggle('active',active);if(active)anchor.setAttribute('aria-current','page');else anchor.removeAttribute('aria-current');}
}
function peopleSectionNavigation(){
  const nav=el('nav','people-section-nav');nav.setAttribute('aria-label','People collections');
  for(const [section,label] of [['judges','Judge profiles'],['people','Historical biographies']]){const anchor=el('a','people-section-link',label);anchor.dataset.section=section;anchor.addEventListener('click',()=>refreshPeopleSectionNavigation(nav));nav.append(anchor);}
  refreshPeopleSectionNavigation(nav);return nav;
}
function judgeAvatar(profile,large=false){
  const holder=el('div',`judge-avatar${large?' large':''}`),name=profile.name||'Judge';holder.setAttribute('aria-hidden','true');const parts=name.replace(/^(hon\.?|judge)\s+/i,'').split(/\s+/).filter(Boolean);holder.append(el('span','',parts.length>1?`${parts[0][0]}${parts[parts.length-1][0]}`:parts[0]?.slice(0,2)||'J'));
  const url=safeURL(profile.photo_url);if(url){const photo=el('img');photo.src=url;photo.alt='';photo.loading='lazy';photo.addEventListener('error',()=>photo.remove(),{once:true});holder.append(photo);}return holder;
}
function judgeCard(profile){
  const card=el('article','judge-card'),body=el('div','judge-card-body');const target=`judge/${encodeURIComponent(profile.id)}`;append(card,judgeAvatar(profile));
  const selectedCourt=route.view==='judges'?route.params.get('court'):null,courtLabel=Array.isArray(profile.courts)?profile.courts.includes(selectedCourt)?selectedCourt:profile.courts[0]:readable(profile.courts);
  append(body,el('p','card-role',profile.role||'Judicial profile'),routeLink(profile.name||'Judge profile',target,{from:location.hash||'#judges'},'judge-name'),el('p','judge-court',courtLabel),el('p','card-location',profile.location||profile.state||readable(profile.states)));
  if(profile.biography_excerpt)body.append(el('p','judge-excerpt',profile.biography_excerpt));
  const footer=el('div','judge-card-footer');if(Number(profile.analysis_count)>0)footer.append(el('span','analysis-available','Analysis available'));if(Number(profile.report_link_count)>0)footer.append(el('span','analysis-available',`${count(profile.report_link_count)} report links`));footer.append(routeLink('View profile →',target,{from:location.hash||'#judges'}));body.append(footer);card.append(body);return card;
}
async function renderJudges(signal){
  heading('Judges','Explore biographies, careers and available judicial analysis.','People & courts');
  const sectionNav=peopleSectionNavigation();main.append(sectionNav);
  const form=el('form','filters judge-filters');form.setAttribute('aria-label','Find a judge');
  const query=filterField('Name or keyword','q','search',route.params.get('q'),'Search judges'),state=filterField('State / jurisdiction','state','select',route.params.get('state')),system=filterField('Court system','system','select',route.params.get('system')),court=filterField('Court','court','select',route.params.get('court')),sort=filterField('Sort by','sort','select',route.params.get('sort'));
  setOptions(state.input,[],'All jurisdictions',route.params.get('state'));setOptions(system.input,[],'All court systems',route.params.get('system'));
  setOptions(court.input,[],'All courts',route.params.get('court'));setOptions(sort.input,[{value:'name',label:'Name A–Z'}],'Most information',route.params.get('sort'));query.label.classList.add('judge-query-filter');court.label.classList.add('judge-court-filter');
  // Evidence filters from the structured overlay; options and counts arrive with the results.
  const status=filterField('FJC-reported status','status','select',route.params.get('status')),role=filterField('Role','role','select',route.params.get('role')),president=filterField('Appointed by','president','select',route.params.get('president'));
  setOptions(status.input,[],'Any status',route.params.get('status'));setOptions(role.input,[],'Any role',route.params.get('role'));setOptions(president.input,[],'Any president',route.params.get('president'));
  append(form,query.label,state.label,system.label,court.label,role.label,status.label,president.label,sort.label,action('Clear filters',()=>navigate('judges')));main.append(form);
  const chips=el('div','filter-chips');chips.setAttribute('aria-label','Profile information');for(const [value,label] of [['details','Detailed profiles'],['all','All directory entries'],['biography','With biographies'],['analysis','With analysis'],['reports','With report links'],['photo','With photos']]){const button=action(label,()=>{const params=Object.fromEntries(route.params);delete params.page;params.has=value;navigate('judges',params);},(route.params.get('has')||'details')===value?'active':'');button.setAttribute('aria-pressed',String((route.params.get('has')||'details')===value));chips.append(button);}main.append(chips);
  const selected=filterSummary();main.append(selected);activeFilters(form,selected);
  const target=el('section','judge-results');target.setAttribute('aria-label','Judge results');main.append(target);
  const submit=()=>{clearTimeout(filterTimer);const params={};for(const input of form.querySelectorAll('input,select'))if(input.value.trim())params[input.name]=input.value.trim();if(route.params.get('has'))params.has=route.params.get('has');history.replaceState(null,'',makeHash('judges',params));route=readRoute();refreshPeopleSectionNavigation(sectionNav);activeFilters(form,selected);load();};
  form.addEventListener('submit',event=>{event.preventDefault();submit();});form.addEventListener('change',event=>{if(event.target===state.input||event.target===system.input){court.input.value='';setOptions(court.input,[],'All courts','');}if(event.target.tagName==='SELECT')submit();});query.input.addEventListener('input',()=>{clearTimeout(filterTimer);filterTimer=setTimeout(submit,350);});
  let requestController;
  async function load(){requestController?.abort();requestController=new AbortController();const requestSignal=requestController.signal;const stop=()=>requestController.abort();signal.addEventListener('abort',stop,{once:true});loading(target,'Finding judges…');
    try{const params=new URLSearchParams(route.params);if(!params.has('has'))params.set('has','details');params.set('limit','24');const data=await api(`/api/judges?${params}`,requestSignal);if(signal.aborted||requestSignal.aborted)return;target.removeAttribute('aria-busy');target.replaceChildren();setOptions(state.input,data.states,'All jurisdictions',route.params.get('state'));setOptions(system.input,data.systems,'All court systems',route.params.get('system'));const sf=data.structured_facets||{},facetOptions=rows=>(rows||[]).map(([value,n])=>({value,label:`${human(value)} (${count(n)})`}));setOptions(status.input,facetOptions(sf.status),'Any status',route.params.get('status'));setOptions(role.input,facetOptions(sf.role),'Any role',route.params.get('role'));setOptions(president.input,facetOptions(sf.appointing_president||sf.president),'Any president',route.params.get('president'));for(const field of [status,role,president])field.label.hidden=!Object.keys(sf).length;setOptions(court.input,(data.courts||[]).map(value=>({...value,label:`${value.label} (${count(value.count)})`})),'All courts',route.params.get('court'));activeFilters(form,selected);
      target.append(append(el('div','results-heading'),el('strong','',`${count(data.total)} profile${Number(data.total)===1?'':'s'}`),el('small','',route.params.get('court')?'Saved court affiliations; may include historical service':({all:'All saved directory entries',biography:'Profiles with biographies',analysis:'Profiles with available analysis',photo:'Profiles with verified portraits'})[route.params.get('has')]||'Profiles with professional details')));
      if(route.params.get('has')==='photo')target.append(el('p','photo-filter-note',`${count(data.total)} ${Number(data.total)===1?'profile with a verified local portrait matches':'profiles with verified local portraits match'} these filters. Photo references without saved images are excluded.`));
      if(data.items?.length){const grid=el('div','judge-grid');for(const item of data.items)grid.append(window.JudgeUI&&typeof window.JudgeUI.card==='function'?window.JudgeUI.card(item):judgeCard(item));target.append(grid);target.append(pagination(data.total,data.page||1,data.limit||24));}else emptyState(target,'No judges found','Try another name or broaden your filters.',()=>navigate('judges'));announce(`${count(data.total)} judge profile${Number(data.total)===1?'':'s'} found.`);
    }catch(error){if(error.name!=='AbortError')errorState(target,error,load);}finally{signal.removeEventListener('abort',stop);}
  }await load();
}
// CourtListener's historical people snapshot stays separate from consolidated judges.
function historicalText(value){return typeof value==='string'||typeof value==='number'?String(value):value?.text?String(value.text):'';}
function peopleParams(values=route.params){
  const params=new URLSearchParams();
  for(const key of ['q','court','from'])if(values.get(key))params.set(key,values.get(key));
  params.set('include_aliases',values.get('include_aliases')==='1'?'1':'0');
  const offset=Number(values.get('offset')),limit=Number(values.get('limit'));
  params.set('offset',String(Number.isFinite(offset)&&offset>=0?Math.floor(offset):0));
  params.set('limit',String(Number.isFinite(limit)&&limit>0?Math.max(1,Math.min(100,Math.floor(limit))):24));
  return params;
}
function peoplePagination(data,params){
  const total=Math.max(0,Number(data.total)||0),offset=Math.max(0,Number(data.offset)||0),limit=Math.max(1,Number(data.limit)||24);
  const box=el('nav','pagination');box.setAttribute('aria-label','Historical biography pages');
  box.append(el('span','',total?`${count(offset+1)}–${count(Math.min(offset+limit,total))} of ${count(total)}`:'No matching biographies'));
  const turn=next=>{const query=new URLSearchParams(params);query.set('offset',String(next));navigate('people',Object.fromEntries(query));};
  const previous=action('← Previous',()=>turn(Math.max(0,offset-limit))),next=action('Next →',()=>turn(offset+limit));
  previous.disabled=offset===0;next.disabled=offset+limit>=total;append(box,append(el('div','button-row'),previous,next));return box;
}
function historicalPersonCard(person,returnTo){
  const card=el('article','historical-person-card'),title=el('h2');title.append(routeLink(person.name||'Unnamed source record','person',{id:person.id,from:returnTo},'historical-person-name'));card.append(title);
  if(person.is_alias)card.append(badge('Alias record','neutral'));
  if(Array.isArray(person.courts)&&person.courts.length)card.append(el('p','historical-courts',person.courts.join(' · ')));
  if(historicalText(person.career_summary))card.append(el('p','historical-summary',historicalText(person.career_summary)));
  if(historicalText(person.education_summary))card.append(el('p','historical-education-preview',historicalText(person.education_summary)));
  card.append(routeLink('Read biography →','person',{id:person.id,from:returnTo}));return card;
}
async function renderPeople(signal){
  heading('Historical biographies','Search saved CourtListener career and education records. This supplemental collection is separate from the consolidated Judges directory.','People & courts');
  main.append(peopleSectionNavigation());
  const params=peopleParams(),form=el('form','filters people-filters');form.setAttribute('aria-label','Find a historical biography');
  const query=filterField('Name','people-query','search',params.get('q'),'Search a name'),court=filterField('Court affiliation','people-court','select',params.get('court'));
  query.input.name='q';court.input.name='court';setOptions(court.input,[],'All courts',params.get('court'));
  const aliases=el('label','people-alias-filter'),check=el('input');check.type='checkbox';check.name='include_aliases';check.checked=params.get('include_aliases')==='1';append(aliases,check,el('span','','Include alias records'));
  const search=el('button','button button-primary','Search');search.type='submit';const buttons=append(el('div','button-row'),search,action('Clear',()=>navigate('people',params.get('from')?{from:params.get('from')}:{})));
  append(form,query.label,court.label,aliases,buttons);main.append(form);
  form.addEventListener('submit',event=>{event.preventDefault();navigate('people',{q:query.input.value.trim(),court:court.input.value,include_aliases:check.checked?'1':'0',limit:params.get('limit'),from:params.get('from')});});
  const target=el('section','historical-results');target.setAttribute('aria-label','Historical biography results');main.append(target);loading(target,'Finding historical biographies…');
  try{
    const request=new URLSearchParams(params);request.delete('from');const data=await api(`/api/people?${request}`,signal);if(signal.aborted)return;
    target.removeAttribute('aria-busy');target.replaceChildren();
    if(data.ready!==true){emptyState(target,'Historical collection is not ready','The saved biographies will appear after their local import has been validated.');return;}
    setOptions(court.input,data.courts||[],'All courts',params.get('court'));
    const snapshot=data.summary?.snapshot_label||'Historical saved snapshot';
    target.append(el('p','historical-snapshot',`${snapshot}. Career affiliations describe the saved source; current service has not been verified.`));
    target.append(append(el('div','results-heading'),el('strong','',`${count(data.total)} matching ${Number(data.total)===1?'biography':'biographies'}`),el('small','',check.checked?'Includes separately labeled aliases':'Alias records excluded')));
    if(Array.isArray(data.items)&&data.items.length){const grid=el('div','historical-grid'),returnTo=makeHash('people',Object.fromEntries(params));for(const person of data.items)grid.append(window.JudgeUI&&typeof window.JudgeUI.card==='function'?window.JudgeUI.card(person):historicalPersonCard(person,returnTo));append(target,grid,peoplePagination(data,params));}
    else emptyState(target,'No matching biographies','Try another name or a different court.',()=>navigate('people',params.get('from')?{from:params.get('from')}:{}));
    const details=el('details','library-details historical-collection-details');details.append(el('summary','','About this historical collection'));const facts=el('dl','source-facts');
    for(const [key,label] of [['people','Source people records'],['positions','Career records'],['educations','Education records']])if(Number.isFinite(Number(data.summary?.[key])))metaRow(facts,label,count(data.summary[key]));
    append(details,facts,el('p','','Source records and aliases are not additional consolidated judge identities. Photo-reference flags do not mean image files were downloaded.'));target.append(details);announce(`${count(data.total)} historical biographies found.`);
  }catch(error){if(error.name!=='AbortError'&&!signal.aborted)errorState(target,error,render);}
}
function historicalPosition(position){
  const item=el('li','historical-position');append(item,el('h3','',historicalText(position.role)||'Reported position'));
  if(position.inferred===true)item.append(badge('Source-inferred values','neutral'));
  const organization=[historicalText(position.court),historicalText(position.organization)].filter(Boolean);if(organization.length)item.append(el('p','historical-position-place',[...new Set(organization)].join(' · ')));
  if(historicalText(position.location))item.append(el('p','card-location',historicalText(position.location)));
  const start=historicalText(position.start),end=historicalText(position.end),period=start&&end?`${start} – ${end}`:start?`Started ${start}`:end?`Ended ${end}`:'';
  if(period)item.append(el('p','historical-position-period',period));
  if(Array.isArray(position.dates)&&position.dates.length){const dates=el('dl','historical-dates');for(const entry of position.dates)metaRow(dates,historicalText(entry.label)||'Reported date',historicalText(entry.text));if(dates.children.length)item.append(dates);}
  return item;
}
async function renderHistoricalPerson(signal){
  loading(main,'Opening historical biography…');
  try{
    const id=route.params.get('id')||'',person=await api(`/api/person?id=${encodeURIComponent(id)}`,signal);if(signal.aborted)return;main.replaceChildren();main.removeAttribute('aria-busy');
    if(person.ready!==true){emptyState(main,'Historical biography unavailable','This saved source record is not available.');main.append(routeLink('Browse historical biographies','people'));return;}
    const previous=route.params.get('from'),returnTo=previous&&/^#people(?:\?|$)/.test(previous)?previous:'#people',back=el('a','back-link','← Back to historical biographies');back.href=returnTo;
    if(window.JudgeUI&&typeof window.JudgeUI.person==='function'){document.title=`${person.name||'Historical biography'} · Legal Archive`;document.querySelector('#breadcrumb-current').textContent=person.name||'Historical biography';window.JudgeUI.person(main,person,{back:()=>{location.hash=returnTo;}});announce(`${person.name||'Historical biography'} opened.`);return;}
    main.append(back);
    heading(person.name||'Historical biography','Career and education as recorded in the saved CourtListener source.','Historical biography');document.title=`${person.name||'Historical biography'} · Legal Archive`;document.querySelector('#breadcrumb-current').textContent=person.name||'Historical biography';
    main.append(peopleSectionNavigation());
    const source=person.source||{};main.append(el('p','historical-snapshot',`${source.snapshot_label||'Historical saved snapshot'}. Current judicial service has not been verified.`));
    if(person.is_alias){const note=el('div','notice');append(note,el('p','','This is an alias record in the source collection.'));
      if(person.alias_of?.id!==undefined&&person.alias_of?.id!==null)note.append(routeLink(`Open ${person.alias_of.name||'the referenced person'}`,'person',{id:person.alias_of.id,from:returnTo}));main.append(note);}
    const life=el('dl','historical-life');metaRow(life,'Born',historicalText(person.birth));metaRow(life,'Died',historicalText(person.death));if(life.children.length)main.append(life);
    const layout=el('div','historical-profile-layout'),career=el('section','historical-career');career.append(el('h2','','Career timeline'));
    if(Array.isArray(person.positions)&&person.positions.length){const list=el('ol','historical-timeline');for(const position of person.positions)list.append(historicalPosition(position));career.append(list);}else career.append(el('p','quiet-empty','No career entries were recorded in this source.'));
    const education=el('section','historical-education');education.append(el('h2','','Education'));
    if(Array.isArray(person.educations)&&person.educations.length){const list=el('ul','historical-education-list');for(const entry of person.educations){const item=el('li');append(item,el('h3','',historicalText(entry.school)||'School not recorded'),el('p','',[...new Set([historicalText(entry.degree),historicalText(entry.degree_detail)].filter(Boolean))].join(' · ')));if(historicalText(entry.year))item.append(el('p','card-location',historicalText(entry.year)));list.append(item);}education.append(list);}else education.append(el('p','quiet-empty','No education entries were recorded in this source.'));
    append(layout,career,education);main.append(layout);
    const details=el('details','library-details historical-source-details');details.append(el('summary','','Source details'));const facts=el('dl','source-facts');metaRow(facts,'Publisher',source.label||'CourtListener');metaRow(facts,'Snapshot',source.snapshot_label);metaRow(facts,'Native person ID',source.person_id??person.id);if(person.birth?.precision)metaRow(facts,'Birth-date precision',human(person.birth.precision));if(person.death?.precision)metaRow(facts,'Death-date precision',human(person.death.precision));if(person.photo_reference)metaRow(facts,'Photo reference','Recorded by the publisher; no downloaded portrait is supplied by this view.');details.append(facts);
    if(source.qualification)details.append(el('p','',historicalText(source.qualification)));
    if(source.has_inferred_values===true)details.append(el('p','','Some values in this profile were inferred by the source. Affected career entries are labeled; they have not been independently verified.'));
    if(Array.isArray(source.source_files)&&source.source_files.length){details.append(el('h3','','Saved source evidence'));const list=el('ul','historical-source-files');for(const file of source.source_files){const row=el('li');append(row,el('strong','',historicalText(file.table)||'Source table'),el('code','',historicalText(file.sha256)));list.append(row);}details.append(list);}
    main.append(details);announce(`Opened historical biography for ${person.name||'the selected person'}.`);
  }catch(error){if(error.name!=='AbortError'&&!signal.aborted)errorState(main,error,render);}
}
function profileArray(value){return (Array.isArray(value)?value:value?[value]:[]).map(item=>typeof item==='string'?item:item?.text||item?.literal_text||item?.as_reported||'').filter(Boolean);}
function isOfficialSourceProfile(profile){return profile.profile_layer==='official_source'||profile.profile_type==='official_source_profile'||profile.source_record_type==='official_source_profile';}
function profileDetails(profile){
  const details=el('details','library-details profile-details');details.append(el('summary','','Profile details & sources'));details.append(el('p','','Court affiliations and career entries may describe historical service. Current office and legal applicability should be checked against the dated original.'));
  if(profile.photo_url&&profile.photo_provenance){const photo=el('details','metadata-details');append(photo,el('summary','','Portrait attribution'),el('pre','',JSON.stringify(profile.photo_provenance,null,2)));details.append(photo);}
  const record={id:profile.id,source_records:profile.source_records||(profile.sources||[]).map(source=>({...source,source_url:source.url,dataset:source.publisher})),metadata:profile};renderSources(details,record,profile);
  if(isOfficialSourceProfile(profile)&&!record.source_records?.length)append(details,link('Official court profile ↗',profile.source_url,true,'button-link'));
  if(profile.provenance||profile.field_provenance){const nested=el('details','metadata-details');append(nested,el('summary','','Field evidence'),el('pre','',JSON.stringify(profile.provenance||profile.field_provenance,null,2)));details.append(nested);}
  const reference=profile.record_id||(!isOfficialSourceProfile(profile)?profile.id:null);if(reference)details.append(action('Inspect saved profile record',event=>openRecord({id:reference},event.currentTarget),'button-small'));
  return details;
}
function libraryInsights(profile){
  const data=profile.library_insights;if(!data)return null;
  const section=el('section','library-insights');append(section,el('h2','',data.label||'Library insights'),el('p','insight-summary',data.summary||'A summary of material saved in this library.'));
  const metrics=el('div','insight-metrics');for(const item of (data.metrics||[]).slice(0,4))append(metrics,append(el('div'),el('strong','',typeof item.value==='number'?count(item.value):readable(item.value)),el('span','',item.label)));if(metrics.children.length)section.append(metrics);
  if(data.available_sections?.length)section.append(el('p','insight-available',`Available: ${data.available_sections.join(' · ')}`));
  const details=el('details','insight-details');details.append(el('summary','','Scope & gaps'));for(const gap of data.gaps||[])details.append(el('p','',gap));if(data.analysis_scope){const scope=data.analysis_scope;if(scope.publishers?.length)details.append(el('p','',`Analysis publishers: ${scope.publishers.join(', ')}`));if(scope.periods?.length)details.append(el('p','',`Reported periods: ${scope.periods.slice(0,5).join('; ')}`));}if(data.methodology)details.append(el('p','',data.methodology));section.append(details);return section;
}
async function renderJudgeProfile(signal){
  loading(main,'Opening judge profile…');
  try{let profile=judgeProfileCache.get(route.id);if(!profile){profile=await api(`/api/judge?id=${encodeURIComponent(route.id||'')}`,signal);if(judgeProfileCache.size>=8)judgeProfileCache.delete(judgeProfileCache.keys().next().value);judgeProfileCache.set(route.id,profile);}if(signal.aborted)return;main.replaceChildren();main.removeAttribute('aria-busy');
    document.title=`${profile.name} · Legal Archive`;document.querySelector('#breadcrumb-current').textContent=profile.name;
    const previous=route.params.get('from'),back=el('a','back-link','← Back to judges');back.href=previous&&/^#judges(?:\?|$)/.test(previous)?previous:'#judges';
    if(window.JudgeUI&&typeof window.JudgeUI.profile==='function'){window.JudgeUI.profile(main,profile,{back:()=>{location.hash=back.getAttribute('href');}});announce(`${profile.name} opened.`);return;}
    main.append(back);
    const official=isOfficialSourceProfile(profile),header=el('section','judge-profile-header'),identity=el('div','judge-identity');append(identity,el('p','eyebrow',official?'Official court profile':profile.role||'Judicial profile'),el('h1','',official?profile.profile_heading_as_published||profile.name:profile.name),el('p','profile-court',Array.isArray(profile.courts)?profile.courts[0]:readable(profile.courts)),el('p','profile-location',[profile.location||profile.state||readable(profile.states),profile.system].filter(Boolean).join(' · ')));append(header,judgeAvatar(profile,true),identity);
    // Compact fact chips: only recorded evidence, each chip names its source or date.
    if(typeof judgeChips==='function'){const chips=judgeChips(profile);if(chips)identity.append(chips);}main.append(header);
    main.append(peopleSectionNavigation());main.append(evidenceDates(profile.source_as_of,profile.saved_at));if(profile.date_note)main.append(el('p','date-note',profile.date_note));
    const biography=profileArray(profile.biography).join('\n\n'),careerKeys=['education','appointments','service','professional_career'],hasCareer=careerKeys.some(key=>profileArray(profile[key]).length),analyses=Array.isArray(profile.analyses)?profile.analyses:[],documents=Array.isArray(profile.documents)?profile.documents:[];
    const tabs=[['overview','Overview']];if(hasCareer)tabs.push(['career','Career']);tabs.push(['analysis','Analysis & reports']);if(documents.length)tabs.push(['documents','Documents']);
    const bar=el('div','profile-tabs');bar.setAttribute('role','tablist');bar.setAttribute('aria-label','Judge profile sections');const panel=el('section','profile-tab-panel');panel.id='judge-tab-panel';panel.setAttribute('role','tabpanel');panel.tabIndex=0;
    const buttons=[];
    function select(value,update=true){if(!tabs.some(([key])=>key===value))value='overview';for(const button of buttons){const active=button.dataset.tab===value;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;button.classList.toggle('active',active);}panel.setAttribute('aria-labelledby',`judge-tab-${value}`);panel.replaceChildren();
      if(update){const params=Object.fromEntries(route.params);if(value==='overview')delete params.tab;else params.tab=value;history.replaceState(null,'',makeHash(`judge/${encodeURIComponent(route.id)}`,params));route=readRoute();}
      if(value==='overview'){const layout=el('div','profile-overview'),article=el('article','profile-biography');article.append(el('h2','',biography?'Biography':'Professional background'));if(biography)article.append(prose(biography));else{const background=profileArray(profile.professional_career);if(background.length)article.append(prose(background.join('\n\n')));const appointment=profileArray(profile.appointments);if(appointment.length)fieldSection(article,'Judicial appointment',appointment.slice(0,1));if(!background.length&&!appointment.length)article.append(el('p','quiet-empty','A biography is not yet available in your saved library.'));}const mdls=profile.mdls&&Array.isArray(profile.mdls.results)?profile.mdls.results:[];
      if(mdls.length){const box=el('section','judge-mdls');box.append(el('h2','',`MDL assignments (${mdls[0].counts_label||'as listed by the JPML'})`));const list=el('ul','career-timeline');for(const m of mdls)list.append(append(el('li'),routeLink(`MDL ${m.mdl_number}: ${m.title}`,`mdl/${m.mdl_number}`,{from:location.hash},'button-link'),el('div','record-subline',`${m.court_name||''} · ${count(m.actions_pending)} actions pending · profile match: ${human(m.judge_match_kind||'exact')}`)));box.append(list);box.append(footnote('Transferee judge as printed in the JPML report, joined to this profile by exact name and court. This is not a current-assignment check and implies nothing about outcomes.'));article.append(box);}
      if(typeof judgeStructuredSection==='function'){const structured=judgeStructuredSection(profile);if(structured)article.append(structured);}
      if(typeof judgeEvidenceSection==='function'){const evidence=judgeEvidenceSection(profile);if(evidence)article.append(evidence);}
      if(typeof judgeDisclosuresSection==='function'){const disclosures=judgeDisclosuresSection(profile);if(disclosures)article.append(disclosures);}
      const aside=el('aside','profile-at-glance');aside.append(el('h2','','At a glance'));const facts=el('dl','profile-facts');metaRow(facts,'Court',Array.isArray(profile.courts)?profile.courts[0]:profile.courts);metaRow(facts,'Jurisdiction',profile.states||profile.state);metaRow(facts,'Court system',profile.system);if(official)metaRow(facts,'Published role',profile.role);metaRow(facts,'Term as published',profile.term_as_published);const education=profileArray(profile.education);if(education.length)metaRow(facts,'Education',education.slice(0,2));aside.append(facts);append(layout,article,aside);panel.append(layout);append(panel,libraryInsights(profile));}
      if(value==='career'){append(panel,el('h2','','Education & professional history'),el('p','career-note',profile.career_note||'Career dates reflect saved records. Current office has not been independently verified.'));for(const [key,label] of [['education','Education'],['appointments','Judicial appointments'],['service','Service'],['professional_career','Professional career']]){const entries=profileArray(profile[key]);if(!entries.length)continue;const section=el('section','career-section');section.append(el('h3','',label));const timeline=el('ul','career-timeline');for(const text of [...new Set(entries)])timeline.append(el('li','',text));section.append(timeline);panel.append(section);}}
      if(value==='analysis')renderAnalysis(panel,analyses,profile.analysis_references,{showEmpty:true,preserveFilters:true});
      if(value==='documents'){panel.append(el('h2','','Related documents'));const list=el('div','profile-documents');for(const document of documents){const card=el('article','profile-document');card.append(el('h3','',document.title||'Related document'));if(document.description)card.append(el('p','',document.description));const actions=el('div','button-row');const recordID=document.record_id||(document.availability==='indexed_record'?document.id:null);if(recordID)actions.append(action('Read document',event=>openRecord({...document,id:recordID},event.currentTarget),'button-primary'));append(actions,link('Open original ↗',document.original_url),link(document.availability==='publisher_link'||document.external?'Open report ↗':'Visit source ↗',document.source_url||document.url,true));card.append(actions);list.append(card);}panel.append(list);}
    }
    for(const [value,label] of tabs){const button=action(label,()=>select(value));button.id=`judge-tab-${value}`;button.dataset.tab=value;button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);button.addEventListener('keydown',event=>{const index=buttons.indexOf(button);let next;if(event.key==='ArrowRight')next=(index+1)%buttons.length;if(event.key==='ArrowLeft')next=(index-1+buttons.length)%buttons.length;if(event.key==='Home')next=0;if(event.key==='End')next=buttons.length-1;if(next!==undefined){event.preventDefault();buttons[next].focus();select(buttons[next].dataset.tab);}});buttons.push(button);bar.append(button);}
    append(main,bar,panel,profileDetails(profile));select(route.params.get('tab')||'overview',false);announce(`${profile.name} profile opened.`);
  }catch(error){if(error.name!=='AbortError')errorState(main,error,render);}
}
function renderCountyReading(parent,profile) {
  if(!profile)return false;
  const section=el('section','county-reading');append(section,el('h3','',profile.heading||'Saved county information'));
  const labels={population:'Population',county_seat:'County seat',phone_number:'Phone number',administration_address:'Administration address',form_of_government:'Form of government',board_of_supervisors:'Board of supervisors',meaning:'County name background'};
  const facts=el('dl','county-reading-facts');for(const [key,label] of Object.entries(labels))metaRow(facts,label,profile.fields?.[key]);
  if(facts.children.length)section.append(facts);
  if(profile.website_url)section.append(link('County website reported in the saved profile ↗',profile.website_url,true,'button-link'));
  if(profile.courthouses?.length){const box=el('section','county-courthouse-links');append(box,el('h4','','Courthouse references'),el('p','coverage-footnote','Links listed in the saved county profile; linked pages are not necessarily saved or current.'));const list=el('div','reader-source-links');for(const court of profile.courthouses)append(list,link(court.name+' ↗',court.url,true,'button-link'));box.append(list);section.append(box);}
  if(profile.overview){const background=el('details','library-details');append(background,el('summary','','Court background as published'),prose(profile.overview));section.append(background);}
  if(!facts.children.length&&!profile.website_url&&!profile.overview&&!profile.courthouses?.length)section.append(el('p','quiet-empty','This saved profile has no verified county information fields. Its original capture remains available above.'));
  section.append(footnote(profile.qualification||'Publisher-reported information; current status has not been independently verified.'));parent.append(section);return true;
}
function countyResourceProse(text,title){
  const box=el('div','record-text reading-content');for(const paragraph of String(text||'').split(/\n\s*\n/).filter(value=>value.trim())){const value=paragraph.trim();if(/^#{1,6}\s*$/.test(value))continue;const heading=value.match(/^(#{1,6})\s+([^\n]+)$/);if(heading){if(heading[2]!==title)box.append(el(heading[1].length<3?'h3':'h4','',heading[2]));}else box.append(el('p','',value));}return box;
}
function renderDocumentOutline(parent,structure){
  if(!structure||(!structure.outline?.length&&!structure.date_observations?.length&&!structure.status_observations?.length))return;
  const details=el('details','metadata-details document-outline');append(details,el('summary','','Document outline & date evidence'),el('p','coverage-footnote',structure.qualification||'Exact source-text observations. Headings are outline candidates, not separate saved rules; observed dates do not establish current legal status.'));
  if(structure.outline?.length){const list=el('ol','document-outline-list');for(const row of structure.outline.slice(0,100))list.append(el('li','',[row.label,row.number,row.title].filter(Boolean).join(' ')));details.append(list);if(structure.outline.length>100||structure.outline_truncated)details.append(el('p','coverage-footnote','Showing the first 100 outline candidates. Full source text remains available above.'));}
  if(structure.date_observations?.length){details.append(el('h4','','Dates observed in the source'));const facts=el('dl','source-facts');for(const row of structure.date_observations.slice(0,100))metaRow(facts,human(row.label),row.value);details.append(facts);}
  if(structure.status_observations?.length){details.append(el('h4','','Status wording in the opening text'));const list=el('ul');for(const row of structure.status_observations.slice(0,100))list.append(el('li','',row.evidence?.excerpt||human(row.status)));details.append(list);}parent.append(details);
}
function renderRecord(item) {
  const body=document.querySelector('#record-body');body.removeAttribute('aria-busy');body.replaceChildren();
  const lawRecord=item.dataset==='open_us_law'&&window.LawReader;
  if(lawRecord){const where=el('div','lawctx-host');body.append(where);window.LawReader.context(where,item.id,{open:id=>openRecord({id})});}
  const lawCitation=lawRecord&&(item.metadata?.citation||'');
  const title=el('h2','',lawRecord?window.LawReader.title(lawCitation,displayTitle(item)):displayTitle(item));title.id='record-title';append(body,lawRecord?null:el('p','reader-context',[item.state,item.county,kindLabel(item.kind)].filter(Boolean).join(' · ')),title);if(lawRecord)body.classList.add('law-record');else body.classList.remove('law-record');
  const observation=item.metadata||{},profile=observation.entity||observation,sourceMetadata=observation.metadata||{};if(!lawRecord||item.source_as_of||item.saved_at)body.append(evidenceDates(item.source_as_of,item.saved_at));
  if(lawRecord){const line=[human(observation.status),observation.snapshot?`Publisher snapshot ${observation.snapshot}`:'',item.state,kindLabel(item.kind)].filter(Boolean).join(' · ');if(line)body.append(el('p','law-record-line',line));}
  const meta=el('dl','record-meta important-meta');
  for(const [key,label] of lawRecord?[]:[['citation',item.dataset==='open_us_law'?'Publisher citation':'Citation'],['status','Reported status'],['source_status','Source status'],['effective_date','Effective date'],['source_date','Source date'],['status_as_of','Status as of'],['snapshot','Snapshot'],['snapshot_date','Snapshot date'],['version','Version']]){let value=observation[key]??sourceMetadata[key];if(value&&typeof value==='object')value=value.label||value.date||value.name||value.version;metaRow(meta,label,human(value));}if(meta.children.length)body.append(meta);
  const actions=el('div','button-row reader-actions');if(lawRecord)append(actions,link('Official source ↗',item.source_url,true,'button'),link('Full text ↗',item.text_url));else append(actions,link(item.dataset==='open_us_law'?'Source data file ↗':item.dataset==='trellis_browser_counties'?'Saved profile snapshot ↗':'Open original ↗',item.original_url,false,'button'),link('Full text ↗',item.text_url),link('Publisher page ↗',item.source_url,true));
  // Every saved record has a stable deep link and a citation that names its source and saved date; nothing is inferred.
  const permalink=location.origin+location.pathname+makeHash(`record/${encodeURIComponent(item.id)}`);
  append(actions,copyButton('Copy link',permalink),copyButton('Copy citation',citationText(item,permalink)));body.append(actions);
  if(sourceMetadata.nj_currentness_discrepancy)body.append(el('p','reader-caution',sourceMetadata.nj_currentness_discrepancy));
  if(item.dataset==='open_us_law')body.append(el('p','reader-note',`Publisher snapshot text. Check the edition and current status at the official source.${!item.source_url?' No official address is recorded for this provision.':''}`));
  if(item.dataset==='county_litigation'){
    const context=el('section','county-reading');append(context,el('h3','',item.resource_type_label||'County resource'),el('p','resource-shape',[human(item.document_shape),item.availability_label].filter(Boolean).join(' · ')));
    const facts=el('dl','county-reading-facts');metaRow(facts,'Source status',human(item.legal_status||'unknown'));metaRow(facts,'Court',item.applicability?.court_name);metaRow(facts,'Applicability',item.applicability?.note||'Not established by the county association');metaRow(facts,'Authority',human(item.authority?.class||item.authority?.status||'unknown'));metaRow(facts,'Published',item.published_at?date(item.published_at):'Unknown');metaRow(facts,'Effective',item.effective_date?date(item.effective_date):'Unknown');context.append(facts);
    if(item.document_shape==='rule_index'||item.document_shape==='form_directory')context.append(el('p','reader-caution','This saved page is an index. Its linked rules or forms are separate documents and may not be saved.'));
    if(item.availability==='needs_review')context.append(el('p','reader-caution','This source needs review. Check its extraction and applicability evidence in Details & sources.'));body.append(context);
  }
  const reading=item.reading_notes||{},recovery=reading.recovery||{},ocr=reading.ocr||/tesseract|ocr/i.test(recovery.method||'');
  if(ocr)body.append(el('p','reader-caution','OCR reading copy. Character errors may remain; check the original when exact wording matters.'));
  else if(recovery.method==='word_com_native')body.append(el('p','reader-caution','Recovered Word/RTF text. Table layout may differ from the original.'));
  const completeProfile=item.county_profile?renderCountyReading(body,item.county_profile):item.group==='judges'?renderJudge(body,profile):false;
  if(!completeProfile){
    if(item.text&&!rawStructuredText(item.text))body.append(item.dataset==='county_litigation'?countyResourceProse(item.text,item.title):lawRecord?window.LawReader.reading(item.text):prose(item.text,'record-text reading-content'));
    else body.append(el('p','quiet-empty',item.dataset==='county_litigation'&&item.has_original?'Original saved; readable text is not yet available. Open the original file above.':item.has_text?'This item has no readable preview. Open its full text or original file.':'No readable text is saved for this item. Its original or publisher page may be available above.'));
    if(lawRecord&&window.LawReader.citedBy){const citing=el('div','lawcited-host');body.append(citing);window.LawReader.citedBy(citing,item.id);}
  }
  if(item.text_truncated)body.append(el('p','coverage-footnote',item.text_url?'This is a preview. Open Full text for the complete saved reading copy.':'This is an excerpt. Open the document or publisher page for the full source.'));
  const details=el('details','library-details document-details');details.append(el('summary','','Details & sources'));
  const context=el('dl','record-meta');metaRow(context,'Collection',datasetName(item.dataset));const captured=observation.captured_at||sourceMetadata.captured_at;if(captured&&readable(captured))metaRow(context,'Captured',Array.isArray(captured)?captured.map(v=>date(v,true)):date(captured,true));metaRow(context,'Attribution',observation.attribution||sourceMetadata.attribution);details.append(context);
  const quality=visibleQuality(item.quality);if(quality)details.append(el('p','quality-note',quality));if(readingNote(reading))details.append(el('p','quality-note',readingNote(reading)));
  renderSources(details,item,profile);
  renderDocumentOutline(details,observation.document_structure);
  if(Array.isArray(item.links)&&item.links.length){const related=el('details','metadata-details'),links=el('div','reader-source-links');append(related,el('summary','',`Links observed in the saved source (${count(item.links.length)})`),el('p','coverage-footnote','These are source-page references. They may include navigation or links to material that has not been saved.'));let shown=0;const more=action('Show more links',()=>drawLinks(),'button-small');function drawLinks(){for(const entry of item.links.slice(shown,shown+20))append(links,link(entry.title||entry.label||'Source reference ↗',entry.url||entry.href,true,'button-link'));shown=Math.min(shown+20,item.links.length);more.hidden=shown>=item.links.length;}drawLinks();append(related,links,more);details.append(related);}
  const provenance=el('details','metadata-details');append(provenance,el('summary','','Reading provenance'),el('pre','',JSON.stringify(reading,null,2)));details.append(provenance);
  const metadata=el('details','metadata-details');append(metadata,el('summary','','Raw evidence & source metadata'),el('p','coverage-footnote','Original artifacts retain the complete source representation, including any site navigation and unrelated links.'),link('Original extraction ↗',item.extracted_url,false,'button-link'),el('pre','',JSON.stringify({metadata:item.metadata||{},source_quality:item.quality},null,2)));details.append(metadata);
  details.append(el('p','coverage-footnote','A capture date is not an effective date. Retain the source edition, historical status and authority when using this material.'));body.append(details);
}
function citationText(item,permalink){
  const parts=[displayTitle(item)];const place=[item.county,item.state].filter(Boolean).join(', ');if(place)parts.push(place);
  if(item.source_url)parts.push(`Source: ${item.source_url}`);if(item.source_as_of)parts.push(`Source as of ${date(item.source_as_of)}`);if(item.saved_at)parts.push(`Saved ${date(item.saved_at,true)}`);
  parts.push(`Legal Archive record ${item.id}`);parts.push(permalink);return parts.join(' — ')+'. Saved material is a snapshot; legal currency is not established.';
}
function copyButton(label,text){const button=action(label,async()=>{try{await navigator.clipboard.writeText(text);button.textContent='Copied';}catch{button.textContent='Copy failed';}setTimeout(()=>{button.textContent=label;},1600);},'button');button.title=text.length>120?text.slice(0,117)+'…':text;return button;}
async function openRecord(item,trigger) {
  if(!dialog.open)lastTrigger=trigger;recordController?.abort();recordController=new AbortController();const signal=recordController.signal;
  if(!dialog.open)dialog.showModal();const body=document.querySelector('#record-body');loading(body,'Opening saved record…');
  const hiddenTitle=el('h2','visually-hidden','Saved record');hiddenTitle.id='record-title';body.prepend(hiddenTitle);document.querySelector('#close-record').focus();
  try{const full=await api(`/api/record?id=${encodeURIComponent(item.id)}`,signal);if(!signal.aborted)renderRecord(full);}catch(error){if(error.name!=='AbortError'){errorState(body,error,()=>openRecord(item,trigger));const t=body.querySelector('h2');if(t)t.id='record-title';}}
}
function navigate(view,params={}) { const hash=makeHash(view,params);if(location.hash===hash){route=readRoute();render();}else location.hash=hash; }
// Three hubs hold every page; the tab bar under the top bar switches between a hub's pages.
// A page with a supplement id is listed only when that data layer has passed validation.
// A tab may hold several segments; each segment is an ordinary hash route, so every old deep link still opens its page.
const HUBS=[
  {id:'places',views:[
    {view:'overview',label:'Map'},
    {view:'laws',label:'State law',segments:[
      {view:'laws',label:'Codes, rules & constitutions',hint:'Full text by state, heading by heading'},
      {view:'limitation-periods',label:'Limitation periods',hint:'Filing deadlines by claim type, checked against the saved statutes',supplement:'limitation_periods_20260920'}]},
    {view:'counties',label:'Counties'},
    {view:'coverage',label:'Coverage'},
    {view:'resources',label:'State resources',supplement:'doj_state_resource_map_20260919'}]},
  {id:'law',views:[
    {view:'regulations',label:'Law & regulations',segments:[
      {view:'regulations',label:'Code of Federal Regulations',hint:'Section text with amendment history',supplement:'federal_regulations_20260919'},
      {view:'federal-register',label:'Federal Register',hint:'Rules, proposed rules and notices, 1994 to 2026',supplement:'federal_register_history_20260919'},
      {view:'public-laws',label:'Public Laws & U.S. Code',hint:'Acts of Congress and the Code sections they change',supplement:'public_law_uscode_20260919'},
      {view:'citation-index',label:'Cited authorities',hint:'Cases, statutes and regulations cited inside saved documents',supplement:'citation_index_20260920'}]},
    {view:'agencies',label:'Agencies',segments:[
      {view:'agencies',label:'Agencies & safety data',hint:'Agency profiles, recalls, approvals, enforcement',supplement:'agency_safety_20260919'},
      {view:'agency-documents',label:'Agency & science documents',hint:'Downloaded reports, profiles and orders with full text',supplement:'agency_science_documents_20260919'},
      {view:'cpsc-injury-data',label:'Injury & incident data',hint:'Product injury surveillance records',supplement:'cpsc_injury_data_20260919'}]},
    {view:'sources',label:'Sources',segments:[
      {view:'sources',label:'Source directory',hint:'Curated official sources and APIs'},
      {view:'urls',label:'URL directory',hint:'Every known official address, by category and place',supplement:'url_directory_20260919'},
      {view:'saved-pages',label:'Saved web pages',hint:'Searchable text of captured court and government pages',supplement:'saved_web_pages_20260919'},
      {view:'source-documents',label:'Downloaded source files',hint:'Court forms, rules, e-filing guides and settlement documents with full text',supplement:'source_directory_documents_20260919'}]},
    {view:'documents',label:'All documents',segments:[
      {view:'documents',label:'Whole library',hint:'Every saved document, by jurisdiction and category'},
      {view:'federal',label:'Federal court & DOJ sources',hint:'Federal court and Justice Department references'}]}]},
  {id:'litigation',views:[
    {view:'judges',label:'Judges',segments:[
      {view:'judges',label:'Judge profiles',hint:'Current judges with appointments, MDLs and evidence'},
      {view:'people',label:'Historical biographies',hint:'Past and present judges, career and education records'},
      {view:'judge-disclosures',label:'Financial disclosures',hint:'Annual filings and holdings as filed',supplement:'judge_financial_disclosures_20260919'}]},
    {view:'mdls',label:'MDLs & mass torts',supplement:'jpml_mdl_20260919',segments:[
      {view:'mdls',group:'Federal MDLs',label:'MDL registry',hint:'Pending multidistrict litigation with judges and counts',supplement:'jpml_mdl_20260919'},
      {view:'mdl-documents',label:'Docket documents',hint:'Orders and filings on the master dockets',supplement:'mdl_docket_documents_20260919'},
      {view:'mdl-activity',label:'Docket activity',hint:'Docket entries by type',supplement:'mdl_docket_activity_20260919'},
      {view:'mdl-cases',label:'Member cases',hint:'Cases in the saved docket sample',supplement:'mdl_case_inventory_20260919'},
      {view:'expert-rulings',label:'Expert challenges',hint:'Docket filings about expert admissibility',supplement:'expert_rulings_scan_20260919'},
      {view:'state-proceedings',group:'State courts',label:'State mass torts',hint:'New Jersey MCL and California coordinated proceedings',supplement:'state_coordinated_proceedings_20260919'},
      {view:'settlements',group:'Outcomes',label:'Settlement notices',hint:'Class and mass settlement notices with deadlines',supplement:'settlements_20260919'},
      {view:'verdict-reports',label:'Verdict & settlement reports',hint:'Publisher-reported results by state, year and amount',supplement:'verdict_settlement_reports_20260919'}]},
    {view:'courts',label:'Courts',supplement:'court_spine_20260919',segments:[
      {view:'courts',label:'Court registry',hint:'Every court with identifiers and marks',supplement:'court_spine_20260919'},
      {view:'court-documents',label:'Court documents',hint:'Forms, local rules, orders and fee schedules',supplement:'court_document_library_20260919'},
      {view:'court-coverage',label:'Court document coverage',hint:'Existing forms and rules mapped to 270 source court entries',supplement:'docsupload_coverage_20260927'},
      {view:'uscourts',label:'U.S. Courts publications',hint:'Saved uscourts.gov pages and reports',supplement:'uscourts_pages_20260919'},
      {view:'statistics',label:'Court statistics',hint:'Caseload tables and per-judge reports',supplement:'federal_court_statistics_20260919'},
      {view:'citation-guide',label:'Reporters & citation forms',hint:'What a reporter or code abbreviation means',supplement:'citation_reference_flp_20260920'}]},
    {view:'counsel',label:'Counsel & firms',segments:[
      {view:'counsel-directory',label:'Firms & attorneys',hint:'Firms and counsel across the saved dockets',supplement:'counsel_directory_20260919'},
      {view:'counsel',label:'MDL master-docket counsel',hint:'Counsel recorded on six MDL master dockets',supplement:'mdl_counsel_20260919'},
      {view:'mdl-appearances',label:'Appearances by docket',hint:'Appearances and party counts in the saved release',supplement:'mdl_counsel_appearances_20260919'}]}]}
];
function hubViews(tab){return tab.segments&&tab.segments.length?tab.segments:[tab];}
// Before the supplement list has loaded, a gated page is listed only when it is the page being shown.
function segmentVisible(seg,activeView,ready){if(!seg.supplement)return true;if(ready)return ready.has(seg.supplement);return seg.view===activeView;}
let lastActiveView='overview';
function renderHubTabs(activeView){
  if(activeView)lastActiveView=activeView;else activeView=lastActiveView;
  const ready=window.READY_SUPPLEMENTS; // utility pages (downloads, status) belong to no hub
  const hub=HUBS.find(h=>h.views.some(tab=>hubViews(tab).some(seg=>seg.view===activeView)))||{id:null,views:[]};
  document.querySelectorAll('[data-hub]').forEach(a=>{const active=a.dataset.hub===hub.id;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  const bar=document.querySelector('#hub-tabs'),strip=document.querySelector('#hub-segments');
  if(strip)strip.replaceChildren();
  if(!bar)return;bar.replaceChildren();
  const activeTab=hub.views.find(tab=>hubViews(tab).some(seg=>seg.view===activeView));
  window.ACTIVE_SEGMENTS={active:activeView,items:[]};
  for(const tab of hub.views){
    const shown=hubViews(tab).filter(seg=>segmentVisible(seg,activeView,ready));
    if(!shown.length)continue;
    const isActive=tab===activeTab,link=el('a',isActive?'hub-tab active':'hub-tab',tab.label);
    link.href=`#${(shown.find(seg=>seg.view===tab.view)||shown[0]).view}`;
    if(partialHostedRelease()&&Array.isArray(window.ARCHIVE_PUBLICATION.available_views)&&!shown.some(seg=>window.ARCHIVE_PUBLICATION.available_views.includes(seg.view)||(seg.supplement&&ready?.has(seg.supplement)))){
      link.append(el('small','publication-pending-label','Not yet published'));link.title='This section is not included in the current published release.';
    }
    if(isActive)link.setAttribute('aria-current','page');
    bar.append(link);
    if(isActive)window.ACTIVE_SEGMENTS={active:activeView,items:shown.length>1?shown:[]};
  }
  const liveRail=main&&main.querySelector(':scope > .workbench > .rail');if(liveRail)renderRailCollections(liveRail);else if(typeof applyWorkbench==='function'&&typeof route!=='undefined'&&route)try{applyWorkbench();}catch(error){}
}
async function render() {
  const sequence=++pageSequence;clearTimeout(filterTimer);pageController?.abort();pageController=new AbortController();const signal=pageController.signal;
  main.replaceChildren();main.removeAttribute('aria-busy');document.querySelector('#breadcrumb-current').textContent=navName(route.view);
  document.title=`${navName(route.view)} · Legal Archive`;
  const area=extensionArea(route.view);
  const AREA_TAB={'indiana-code':'laws','sd-statutes':'laws','state-codes':'laws'};const activeView=area?(AREA_TAB[route.view]||area.parent||route.view):({judge:'judges',person:'people',county:'counties',collection:'mdls',collections:'mdls',source:'sources',record:'documents','indiana-code':'laws','sd-statutes':'laws','state-codes':'laws'}[route.view]||route.view);
  renderHubTabs(activeView);
  if(area){
    try{await area.render(signal,route);}catch(error){if(error.name!=='AbortError'&&sequence===pageSequence)errorState(main,error,render);}
  }else if(route.view==='datasets'||route.view==='library'){navigate('overview');return;} // both footer pages were retired
  else if(route.view==='overview') {
    loading(main);
    try{const data=await loadSummary();if(sequence!==pageSequence)return;main.removeAttribute('aria-busy');main.replaceChildren();renderOverview(data);}catch(error){if(error.name!=='AbortError'&&sequence===pageSequence)errorState(main,error,render);}
  }else if(route.view==='sources')await renderSourceDirectory(signal);
  else if(route.view==='source')await renderSourceDetail(signal);
  else if(route.view==='collections'){navigate('mdls');return;} // the collections index was retired; its contents live under MDLs, Settlements and Courts
  else if(route.view==='collection')await renderCollection(signal);
  else if(route.view==='judges')await renderJudges(signal);
  else if(route.view==='judge')await renderJudgeProfile(signal);
  else if(route.view==='people')await renderPeople(signal);
  else if(route.view==='person')await renderHistoricalPerson(signal);
  else if(route.view==='county')await renderCounty(signal);
  else if(route.view==='record'){renderLibrary(false);if(route.id)openRecord({id:route.id},null);else emptyState(main,'No record selected','Choose a document from the library to open it.',()=>navigate('documents'));}
  else renderLibrary(route.view==='counties');
  if(!signal.aborted&&sequence===pageSequence&&typeof window.renderEnrichmentContext==='function')window.renderEnrichmentContext(main,route,signal);
}
document.querySelector('#close-record').addEventListener('click',()=>dialog.close());
document.querySelector('.skip-link').addEventListener('click',event=>{event.preventDefault();main.focus();main.scrollIntoView({block:'start'});});
dialog.addEventListener('close',()=>{recordController?.abort();lastTrigger?.isConnected && lastTrigger.focus();});
dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
// Reload both the interface and its data while keeping the current route and filters.
document.querySelector('#refresh-button').addEventListener('click',()=>location.reload());
window.addEventListener('hashchange',()=>{if(dialog.open)dialog.close();route=readRoute();render();main.focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});});
if(route.view!=='overview')loadSummary().catch(()=>{document.querySelector('#snapshot-label').textContent='';});
// Render once, after areas.js (also deferred) has registered its pages and the state grid.

/* Workbench layout. A page that has filters (or belongs to a tab with several collections) gets a left rail: the tab's
   collections as a vertical list, then the page's own filters stacked, with the results to the right. Pages keep rendering
   exactly as before; this only re-parents what they produced, so every route and filter keeps working. */
/* The collection list is rebuilt whenever the tab bar is (the list of published data layers arrives after the first paint). */
function renderRailCollections(rail){
  const segments=(window.ACTIVE_SEGMENTS&&window.ACTIVE_SEGMENTS.items)||[],active=window.ACTIVE_SEGMENTS&&window.ACTIVE_SEGMENTS.active;
  for(const stale of rail.querySelectorAll(':scope > .rail-collections'))stale.remove();
  if(segments.length<2)return;
  const group=el('nav','rail-group rail-collections');group.setAttribute('aria-label','Collections');group.append(el('h2','rail-title','Collection'));
  const dense=segments.length>5;if(dense)group.classList.add('is-dense');
  for(const seg of segments){if(seg.group)group.append(el('h3','rail-subtitle',seg.group));const a=el('a',seg.view===active?'rail-item active':'rail-item');a.href=`#${seg.view}`;if(seg.view===active)a.setAttribute('aria-current','page');a.append(el('strong','',seg.label));if(seg.hint&&(!dense||seg.view===active))a.append(el('span','',seg.hint));else if(seg.hint)a.title=seg.hint;group.append(a);}
  rail.prepend(group);
}
function applyWorkbench(){
  if(!main)return;
  const segments=(window.ACTIVE_SEGMENTS&&window.ACTIVE_SEGMENTS.items)||[],active=window.ACTIVE_SEGMENTS&&window.ACTIVE_SEGMENTS.active;
  let bench=main.querySelector(':scope > .workbench');
  const looseForm=main.querySelector(':scope > form.filters');
  if(!bench){
    const listing=['laws','documents','federal','judges','people','counties','sources'].includes(route.view)||Boolean(extensionArea(route.view));
    if(!looseForm&&(segments.length<2||!listing||route.id))return;
    bench=el('div','workbench');const rail=el('aside','rail'),body=el('div','workbench-body');rail.setAttribute('aria-label','Collections and filters');
    renderRailCollections(rail);
    bench.append(rail,body);
    const anchor=looseForm||[...main.children].find(node=>!node.matches('.page-heading,.home-hero,.notice,.view-switch,.scope-switch,.page-summary,.back-link'))||null;
    if(anchor)main.insertBefore(bench,anchor);else main.append(bench);
  }
  const rail=bench.querySelector(':scope > .rail'),body=bench.querySelector(':scope > .workbench-body');
  // Pages render in stages: whatever they append after the rail exists is adopted here (filters into the rail, the rest beside it).
  let node=bench.nextSibling;
  while(node){
    const next=node.nextSibling;
    if(node.nodeType===1&&node.matches('form.filters')){
      let group=rail.querySelector(':scope > .rail-filter-group');
      if(!group){group=el('div','rail-group rail-filter-group');group.append(el('h2','rail-title','Filters'));const toggle=el('details','rail-filters');toggle.open=window.matchMedia('(min-width: 1001px)').matches;toggle.append(el('summary','','Show filters'));group.append(toggle);rail.append(group);}
      const toggle=group.querySelector('details');for(const old of toggle.querySelectorAll(':scope > form.filters'))old.remove();toggle.append(node);
    }else if(node.nodeType===1&&node.matches('.filter-chips')){
      // Quick-view buttons become a plain option list in the rail instead of a row of pills above the results.
      for(const stale of rail.querySelectorAll(':scope > .rail-options'))stale.remove();
      const group=el('div','rail-group rail-options');group.append(el('h2','rail-title',node.getAttribute('aria-label')||'Show'));group.append(node);
      const filters=rail.querySelector(':scope > .rail-filter-group');if(filters)rail.insertBefore(group,filters);else rail.append(group);
    }else if(node.nodeType===1&&node.matches('.page-heading,.home-hero,.notice,.page-summary,.back-link')){
      main.insertBefore(node,bench); // a heading drawn after the rail still belongs above it
    }else body.append(node);
    node=next;
  }
}
const workbenchObserver=new MutationObserver(()=>{workbenchObserver.disconnect();try{applyWorkbench();}catch(error){console.error(error);}finally{workbenchObserver.observe(main,{childList:true});}});
workbenchObserver.observe(main,{childList:true});
document.addEventListener('DOMContentLoaded',()=>{route=readRoute();window.ARCHIVE_PUBLICATION_PROMISE=checkPublicationStatus();render();});
