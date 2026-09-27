import { createContext } from './cloud-context.mjs';
import { handleGeneric } from './generic-api.mjs';
import { handlePlacesJudges } from './places-judges-api.mjs';
import { handleReferencesMdl } from './references-mdl-api.mjs';
import { handleNavigation } from './navigation-api.mjs';
import { handleDocuments } from './documents-api.mjs';
import { handleLawOutline } from './law-outline-api.mjs';
import { handleFederal } from './federal-api.mjs';
import { handleRelated } from './related-api.mjs';
import { handleCountyResources } from './county-resources-api.mjs';
import { handleEnrichment } from './enrichment-api.mjs';

export async function handleCloud(request, context=createContext()) {
  const url=new URL(request.url);
  const path=url.pathname, p=Object.fromEntries(url.searchParams);
  if (!['GET','HEAD'].includes(request.method)) return Response.json({error:'Read-only archive'},{status:405});
  if (path==='/api/health') {
    const datasets=await context.datasets(), release=await context.context('publication:release');
    const expected=release?.datasets;
    const ready=release?.validated===true && Array.isArray(expected) && expected.length>0 && expected.every(id=>datasets.some(d=>d.id===id&&d.ready&&d.imported_records===d.expected_records));
    return {service:'legal-archive-supabase',ready:!!ready,release:release?.id||null,datasets:datasets.map(d=>({id:d.id,ready:d.ready,records:d.imported_records}))};
  }
  if (path==='/api/county-litigation-asset') return context.asset(path+url.search);
  for (const handler of [handleEnrichment,handleCountyResources,handleDocuments,handlePlacesJudges,handleReferencesMdl,handleNavigation,handleLawOutline,handleFederal,handleRelated,handleGeneric]) {
    const value=await handler(path,p,context);
    if(value!==null)return value;
  }
  if (/^\/(files|bulk-files|bulk-metadata|recovery-metadata|agency-files|mdl-files|supplement-files|source-assets|library-assets|judge-images)\//.test(path)) return context.asset(path+url.search);
  return Response.json({error:'Unknown archive route',route:path},{status:404});
}
