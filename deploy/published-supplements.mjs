// Individually accepted supplement screens can be available before the global
// supplement directory. Keep this list explicit: local inventory is not a
// publication gate, and unlisted collections remain held with that directory.
const dependencies = {
  federal_regulations_20260919:['federal_regulations_parts','federal_regulations_sections','federal_regulations_documents'],
  jpml_mdl_20260919:['mdls'],
  court_spine_20260919:['court_spine'],
  doj_state_resource_map_20260919:[],
  url_directory_20260919:['url_directory'],
  state_coordinated_proceedings_20260919:['state_proceedings'],
  court_document_library_20260919:['court_documents'],
  federal_court_opinions_20260820:['federal_opinions_20260820'],
  settlements_20260919:['settlements'],
  federal_court_statistics_20260919:['court_statistics'],
  mdl_counsel_20260919:['mdl_counsel'],
  uscourts_pages_20260919:['uscourts_pages'],
  docsupload_coverage_20260927:['docsupload_coverage'],
  judge_financial_disclosures_20260919:['judge_disclosures'],
  federal_register_history_20260919:['federal_register_history'],
  mdl_case_inventory_20260919:['mdl_case_inventory'],
  mdl_counsel_appearances_20260919:['mdl_appearances'],
  mdl_docket_documents_20260919:['mdl_docket_documents'],
  mdl_docket_activity_20260919:['mdl_docket_activity'],
  agency_science_documents_20260919:['agency_science_documents'],
  public_law_uscode_20260919:['public_laws'],
  counsel_directory_20260919:['counsel_directory'],
  verdict_settlement_reports_20260919:['verdict_reports'],
  cpsc_injury_data_20260919:['cpsc_injury_data'],
  expert_rulings_scan_20260919:['expert_rulings'],
  source_directory_documents_20260919:['source_documents'],
  citation_reference_flp_20260920:['citation_reference'],
  citation_index_20260920:['citation_index'],
  sd_statutes_20260919:['sd_statutes'],
  saved_web_pages_20260919:['saved_pages'],
  indiana_code_2026_20260919:['indiana_code'],
  limitation_periods_20260920:['limitation_periods'],
  law_tier_20260919:[],
};

export async function publishedSupplements(context) {
  const datasets=await context.datasets();
  const ready=new Set(datasets.filter(row=>row.ready===true &&
    Number.isSafeInteger(row.expected_records) && row.expected_records>=0 &&
    row.imported_records===row.expected_records).map(row=>row.id));
  const selected=Object.entries(dependencies).filter(([,ids])=>ids.every(id=>ready.has(id)));
  const values=await Promise.all(selected.map(async([name])=>{
    // context() reads only published rows and verifies any chunked payload.
    // The named gate also covers the accepted context-only resource screens.
    const item=await context.context('supplement:'+name);
    return item?.name===name && item.ready===true ? item : null;
  }));
  const items=values.filter(Boolean);
  return items.length?{items,total:items.length,ready:items.length,
    qualification:'Only individually published, validated supplements are listed. Required catalog datasets must be published with matching imported and expected record counts. Readiness does not establish legal currency or source completeness.'}:null;
}
