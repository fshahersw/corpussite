import { handleCloud } from '../deploy/cloud-api.mjs';

export default async function handler(req,res) {
  try {
    const incoming=new Request('https://archive.invalid'+(req.headers['x-corpus-route'] || req.url),{
      method:req.method,headers:new Headers(Object.entries(req.headers).filter(([,v])=>typeof v==='string'))
    });
    const result=await handleCloud(incoming);
    const response=result instanceof Response ? result : Response.json(result);
    res.statusCode=response.status;
    for (const [name,value] of response.headers) res.setHeader(name,value);
    res.setHeader('Cache-Control','private, no-store');
    res.setHeader('X-Content-Type-Options','nosniff');
    res.end(req.method==='HEAD' ? undefined : Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    console.error('Corpus request failed:',error.name);
    res.statusCode=503;res.setHeader('Content-Type','application/json');
    res.setHeader('Cache-Control','private, no-store');
    res.setHeader('X-Content-Type-Options','nosniff');
    res.end(JSON.stringify({error:'The archive data connection is temporarily unavailable.'}));
  }
}
