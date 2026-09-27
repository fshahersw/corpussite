"""Bound large metadata into hash-bound pieces before PostgREST insertion."""
import hashlib,json
CHUNK_CHARS=120_000
def write_context(client,key,data,source_sha256=None):
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    digest=hashlib.sha256(raw.encode('utf-8')).hexdigest()
    value=data
    if len(raw.encode('utf-8'))>400_000:
        pieces=[]
        for i in range(0,len(raw),CHUNK_CHARS):
            name=f'{key}:part:{digest[:16]}:{i//CHUNK_CHARS:06d}'
            chunk=raw[i:i+CHUNK_CHARS]
            client.upsert('corpus_context',[{'key':name,'data':chunk,'source_sha256':hashlib.sha256(chunk.encode('utf-8')).hexdigest(),'ready':False}])
            pieces.append(name)
        value={'__corpus_chunked_v1':True,'parts':pieces,'sha256':digest,'characters':len(raw)}
    client.upsert('corpus_context',[{'key':key,'data':value,'source_sha256':source_sha256 or digest,'ready':False}])
