/* Dated additions and explicit evidence links, separate from the frozen corpus. */
(function () {
  const registry = window.ARCHIVE_AREAS = window.ARCHIVE_AREAS || {};
  const relationLabels = {in_state:'County in state',listed_filing_source:'Listed filing source',captured_as:'Saved reader',links_to:'Links to',contains:'Contains',excerpt_of:'Section from',has_native_identifier:'Native identifier',source_names_county:'Names county',order_in_mdl:'Order in MDL',cites_mdl:'Cites MDL',cites_docket:'Cites docket',lists_docket_in_schedule:'Docket printed in schedule',named_court:'Names court',county_context:'County context',serves_geography_as_source_reported:'Source reports court area',contains_layout_section:'Contains layout fragment'};
  const shapeLabel = value => ({section:'Extracted section',body:'Document body',rule_body:'Rule text',order_body:'Order text',index:'Source index'})[value] || human(value||'unclassified');
  const statusLabel = value => ({rescission_notices_only:'Rescission notices only',rule_text_with_rescission_notices:'Includes rescission notices',compilation_includes_active_text_and_rescinded_notices:'Compilation includes rescission notices',rule_text_currency_unverified:'Rule text — currency not verified'})[value] || '';
  function tabs() {
    const row=el('div','button-row');append(row,routeLink('Saved additions','additions',{},'button'),routeLink('Evidence connections','connections',{},'button'));return row;
  }
  function sourceCard(item) {
    const card=el('article','panel enrichment-card');
    append(card,routeLink(item.title,`addition/${encodeURIComponent(item.id)}`,{},'document-title'),
      el('p','record-subline',[item.state||'Federal / shared',human(item.resource_type||item.category),shapeLabel(item.document_shape),item.source_page?'Source page '+item.source_page:''].filter(Boolean).join(' · ')),
      el('p','muted-note',`Captured ${item.captured_at?date(item.captured_at):'date unknown'} · ${human(item.review_status||'source evidence recorded')}`));
    if(statusLabel(item.legal_status))card.append(el('p','publication-note',statusLabel(item.legal_status)));
    const buttons=el('div','button-row');append(buttons,routeLink('Read saved text',`addition/${encodeURIComponent(item.id)}`,{},'button-link'),link(item.document_shape==='section'?'Source document ↗':'Original file ↗',item.original_url),link('Publisher ↗',item.source_url,true,'button-link'));card.append(buttons);return card;
  }
  function relatedSection(data) {
    const section=el('section','panel');section.setAttribute('aria-label','Related reading');
    section.append(el('h2','','Related reading'));
    if(!data?.available){section.append(el('p','muted-note','Related reading has not been published here yet.'));return section;}
    section.append(el('p','muted-note',`${count(data.matched)} saved readers found through recorded source connections. Each group explains the connection; separate captures and versions remain separate.`));
    for(const cluster of data.clusters){
      const group=el('details','library-details');group.append(el('summary','',`${cluster.label} · ${count(cluster.matched)}${cluster.truncated?' · showing '+cluster.items.length:''}`));
      for(const item of cluster.items){
        const card=el('article','enrichment-card');
        append(card,routeLink(item.title,`addition/${encodeURIComponent(item.id)}`,{},'document-title'),el('p','record-subline',[shapeLabel(item.document_shape),item.captured_at?'Captured '+date(item.captured_at):'Capture date not established',statusLabel(item.legal_status)].filter(Boolean).join(' · ')));
        const why=el('details','library-details');why.append(el('summary','','Why this is related'));
        for(const path of item.evidence_paths){
          const trail=el('div','');trail.append(el('p','muted-note',path.map(edge=>relationLabels[edge.relation]||human(edge.relation)).join(' → ')));
          for(const edge of path){
            if(edge.evidence?.quote)trail.append(el('p','',String(edge.evidence.quote)));
            if(edge.scope_label)trail.append(el('p','muted-note',edge.scope_label));
            if(edge.qualification)trail.append(el('p','muted-note',edge.qualification));
            if(edge.evidence?.source_url)trail.append(link('Evidence source ↗',edge.evidence.source_url,true,'button-link'));
          }
          why.append(trail);
        }
        card.append(why);group.append(card);
      }
      section.append(group);
    }
    if(!data.clusters.length)section.append(el('p','muted-note','No related saved readers were found within the checked source connections.'));
    if(data.incomplete)section.append(el('p','publication-note',data.pending_entities?.length||data.pending_collections?.length?'Some related source connections are still pending publication. This is a partial result.':'This is a bounded view of the recorded connections; larger source groups may contain additional readers.'));
    section.append(footnote(data.qualification));return section;
  }
  async function renderRelated(target,entity,signal){
    target.append(el('p','muted-note','Finding related saved reading…'));
    try{
      const data=await api('/api/enrichment/graph?'+new URLSearchParams({entity,related:'1',limit:'1'}),signal);if(signal.aborted)return;
      target.replaceChildren(relatedSection(data.available?data.related:null));
    }catch(error){if(error.name==='AbortError')return;target.replaceChildren(el('p','muted-note','Related reading could not be loaded. The saved reader above remains available.'));}
  }
  registry.additions={title:'Source additions',nav:'Source additions',async render(signal,route){
    heading('Source additions','Saved documents, source indexes, and extracted sections, with dates and links to their original files.','Research library');main.append(tabs());
    const params=new URLSearchParams(route.params);const data=await api('/api/enrichment?'+params,signal);if(signal.aborted)return;
    if(!data.available){emptyState(main,'Additions not published here yet','The local collection and hosted release publish separately.');return;}
    if(data.pending_collections?.length)main.append(el('p','publication-note','An additional source collection is awaiting publication. The readers listed here remain available.'));
    const form=el('form','filters');form.setAttribute('aria-label','Filter source additions');
    const fields=[];
    for(const [name,label] of [['q','Search'],['state','State'],['resource_type','Type'],['document_shape','Content'],['lane','Collection']]){
      const field=filterField(label,name,name==='q'?'search':'select',params.get(name)||'');
      if(name!=='q')setOptions(field.input,(data.facets[name]||[]).map(v=>({value:v,label:name==='document_shape'?shapeLabel(v):human(v)})),'All',params.get(name)||'');
      fields.push([name,field.input]);form.append(field.label);
    }
    const submit=el('button','button','Apply filters');submit.type='submit';form.append(submit);
    form.addEventListener('submit',event=>{event.preventDefault();const next=Object.fromEntries(fields.map(([name,input])=>[name,input.value]));for(const name of ['county','mdl'])if(params.has(name))next[name]=params.get(name);navigate('additions',next);});main.append(form);
    if(params.has('county')||params.has('mdl'))main.append(el('p','muted-note',`Scoped to ${params.has('county')?'county '+params.get('county'):'MDL '+params.get('mdl')}. Only recorded source relationships are included.`));
    main.append(el('p','page-summary',`${count(data.total)} matching readers · ${count(data.summary.original_documents)} ${data.summary.source_file_count_basis==='per_collection'?'source files counted within each collection':'distinct source files across this layer'}`));
    main.append(el('p','muted-note','Reader counts include sections extracted from shared documents. A section is not an additional download.'));
    if(!data.items.length)emptyState(main,'No saved additions match','Change the filters or browse the evidence connections for source links.',()=>navigate('additions'));
    for(const item of data.items)main.append(sourceCard(item));
    main.append(pagination(data.total,data.page,data.limit),footnote(data.qualification));
  }};
  registry.addition={title:'Saved source',nav:'Saved source',parent:'additions',async render(signal,route){
    const item=await api('/api/enrichment/record?id='+encodeURIComponent(route.id||''),signal);if(signal.aborted)return;
    heading(item.title,[item.state,human(item.resource_type||item.category),shapeLabel(item.document_shape)].filter(Boolean).join(' · '),'Saved source addition');
    const row=el('div','button-row');append(row,routeLink('← Source additions','additions',{},'button-link'),link(item.document_shape==='section'?'Download source document':'Download original',item.original_url),link('Publisher ↗',item.source_url,true),routeLink('Related sources & identifiers','connections',{entity:item.id},'button'));main.append(row);
    if(item.parent_compilation_id?.startsWith('addition:'))row.append(routeLink('Full source reader',`addition/${encodeURIComponent(item.parent_compilation_id)}`,{},'button-link'));
    if(item.document_shape==='section')main.append(el('p','muted-note',`This reader is an extracted section of a larger source document${item.source_page?', starting on source page '+item.source_page:''}. Open the source document to review it in context.`));
    if(statusLabel(item.legal_status))main.append(el('p','publication-note',statusLabel(item.legal_status)));
    if(Array.isArray(item.quality_notes)&&item.quality_notes.length){
      const notes=el('section','publication-note');notes.append(el('h2','','Reader notes'));
      for(const note of item.quality_notes)if(typeof note==='string')notes.append(el('p','',note));main.append(notes);
    }
    const dates=el('dl','record-meta');for(const [label,value] of [['Captured',item.captured_at],['Source as of',item.source_as_of],['Filed',item.filed_at],['Publisher date',item.published_at],['Effective date',item.effective_from]])metaRow(dates,label,value?date(value,label==='Captured'):'Not established');metaRow(dates,'Review',human(item.review_status)||'Not established');main.append(dates);
    if(item.text)main.append(prose(item.text,'reading-content'));else emptyState(main,'Text is not available','The original file is preserved above.');
    const related=el('div','');main.append(related);renderRelated(related,item.id,signal);
    if(item.hierarchy?.length){const details=el('details','library-details'),list=el('ol','career-timeline');for(const part of item.hierarchy)list.append(el('li','',typeof part==='string'?part:[part.label||part.title||part.name||part.id,part.page?'page '+part.page:''].filter(Boolean).join(' · ')));append(details,el('summary','','Source outline'),list);main.append(details);}
    if(item.qualification)main.append(footnote(item.qualification));
    main.append(footnote('Capture time is not an effective date. An archived rule, form or order is not independently certified as current.'));
  }};
  registry.connections={title:'Evidence connections',nav:'Evidence connections',async render(signal,route){
    heading('Evidence connections','Explore recorded county, source, document and MDL relationships.','Research library');main.append(tabs());
    const entity=route.params.get('entity')||'';const data=await api('/api/enrichment/graph?'+new URLSearchParams({entity,limit:'100',related:'1'}),signal);if(signal.aborted)return;
    if(!data.available){emptyState(main,'Connections not published here yet','This layer becomes available after its evidence checks and publication.');return;}
    const form=el('form','filters'),field=filterField('Identifier','entity','search',entity);field.input.placeholder='state:MI, county:26163, mdl:2873';const submit=el('button','button','Explore');submit.type='submit';append(form,field.label,submit);form.addEventListener('submit',event=>{event.preventDefault();navigate('connections',{entity:field.input.value});});main.append(form);
    const examples=el('div','button-row');for(const [id,label] of [['state:MI','Michigan'],['county:26163','Wayne County'],['mdl:2873','MDL 2873']])examples.append(routeLink(label,'connections',{entity:id},'button-link'));main.append(examples);
    main.append(el('p','page-summary',entity?`${count(data.total)} direct connections${data.truncated?' · showing the first 100':''}`:`${count(data.summary.graph_nodes)} entities · ${count(data.summary.graph_edges)} recorded ${data.summary.graph_count_basis==='per_collection'?'connection observations across collections':'connections'}`));
    if(entity)main.append(relatedSection(data.related));
    const direct=el('details','library-details');direct.append(el('summary','',`Direct source connections · ${count(data.total)}`));
    const nodes=new Map(data.nodes.map(node=>[node.id,node]));
    for(const e of data.edges){
      const card=el('article','panel enrichment-card');const row=el('div','button-row');
      for(const [i,id] of [e.source,e.target].entries()){const n=nodes.get(id)||{label:id};if(i)row.append(el('span','muted-note',relationLabels[e.relation]||human(e.relation)));row.append(routeLink(n.label,'connections',{entity:id},'button-link'));}
      card.append(row);if(e.scope_label)card.append(el('p','muted-note',e.scope_label));
      const evidence=el('details','library-details');evidence.append(el('summary','','Evidence & source'));
      if(e.evidence?.quote)evidence.append(el('p','',String(e.evidence.quote)));
      if(e.evidence?.source_url)evidence.append(link('Source ↗',e.evidence.source_url,true,'button-link'));
      const actions=el('div','button-row');for(const id of [e.source,e.target]){const n=nodes.get(id);if(n?.resource_id)actions.append(routeLink('Read saved document',`addition/${encodeURIComponent(n.resource_id)}`,{},'button'));else if(n?.url)actions.append(link('Open listed source ↗',n.url,true));}card.append(actions);
      evidence.append(el('p','muted-note',human(e.review_status||'Evidence recorded')));card.append(evidence);direct.append(card);
    }
    if(data.total)main.append(direct);
    if(entity&&!data.total)emptyState(main,'No recorded connections for this identifier','An empty result means no relationship has been established in this layer.');
    main.append(footnote(data.qualification));
  }};
  window.renderEnrichmentContext=async function(target,route,signal){
    let params,entity;
    if(route.view==='state'){params={state:route.id};entity='state:'+route.id;}
    else if(route.view==='county'){params={county:route.id};entity='county:'+route.id;}
    else if(route.view==='mdl'){params={mdl:route.id};entity='mdl:'+route.id;}
    else if(route.view==='overview'){params={};}
    else return;
    try{
      const data=await api('/api/enrichment?'+new URLSearchParams({...params,limit:'4'}),signal);if(signal.aborted||!data.available)return;
      const section=el('section','panel');append(section,el('h2','','Source additions & connections'),el('p','muted-note',`${count(data.total)} readers in this view, including extracted sections. Source relationships do not establish legal applicability.`));
      for(const item of data.items)section.append(sourceCard(item));
      const buttons=el('div','button-row');append(buttons,routeLink('Browse additions','additions',params,'button'),routeLink('Explore evidence connections','connections',entity?{entity}:{},'button'));section.append(buttons);target.append(section);
    }catch(error){if(error.name==='AbortError')return;}
  };
})();
