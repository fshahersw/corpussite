/** Presentation projections retain source fields and individual text-source gates. */
export function projectDocument(dataset,input,publisherTextReady=false){
 const d={...input};
 if(dataset==='federal_regulations_parts')return {...d,source_title:d.title,title:`${d.title} CFR Part ${d.part}${d.heading?' — '+d.heading:''}`,subtitle:d.chapter_name||'',facts:[['CFR title',d.title],['Part',d.part],['Chapter',d.chapter_name||d.chapter],['Source structure date',d.temporal?.source_as_of]],links:[...(d.links||[]),...(d.ecfr_url?[{label:'eCFR source',url:d.ecfr_url}]:[])]};
 if(dataset==='federal_regulations_sections'){
  const texts=(d.texts||[]).filter(t=>t.source!=='open_us_law'||publisherTextReady);
  const sections=texts.filter(t=>t.text).map(t=>({heading:t.source==='open_us_law'?'Publisher snapshot text':'Saved regulation text',text:t.text,source_label:t.label}));
  if(d.history?.length)sections.push({heading:'Version metadata',header:['Version date','Issue date','Change as reported'],rows:d.history.map(h=>[h.version_date,h.ecfr_issue_date,h.removed?'Removed':h.name_as_reported||'Recorded version'])});
  if(d.related_fr_documents?.documents?.length)sections.push({heading:'Related Federal Register publications',items:d.related_fr_documents.documents.map(r=>({title:r.title,subtitle:[r.type,r.citation,r.publication_date].filter(Boolean).join(' · '),links:[{label:'Publication',url:'#law/federal_regulations_documents/'+encodeURIComponent(r.id)}]}))});
  return {...d,texts,source_title:d.title,title:`${d.citation||`${d.title} CFR § ${d.section}`}${d.heading?' — '+d.heading:''}`,subtitle:d.part_heading||'',sections,held_text_sources:(d.texts||[]).length-texts.length,links:[...(d.links||[]),...(d.ecfr_url?[{label:'eCFR source',url:d.ecfr_url}]:[])],facts:[['CFR title',d.title],['Part',d.part],['Section',d.section],['Source snapshot',d.temporal?.source_as_of]],qualification:'Saved text sources retain their individual edition and source dates. Version-history dates are metadata, not independently verified effective dates.'};
 }
 return d;
}
