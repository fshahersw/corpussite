"""Render the seven flagged source PDFs and preserve OCR model provenance."""
import hashlib,json,time
from pathlib import Path
import fitz
from collect import ROOT,read_jsonl,write_json,now,requests,WindowsTrustAdapter,UA

OUT=ROOT/'ocr';OUT.mkdir(exist_ok=True)
MODELS=OUT/'models';MODELS.mkdir(exist_ok=True)
MODEL_URL='https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata'

def prepare():
    model=MODELS/'eng.traineddata'
    if not model.exists():
        with requests.Session() as session:
            session.mount('https://',WindowsTrustAdapter())
            response=session.get(MODEL_URL,timeout=(15,90),verify=True,headers={'User-Agent':UA})
            response.raise_for_status()
            assert len(response.content)>1000000, 'Unexpectedly small traineddata response'
            model.write_bytes(response.content)
        write_json(MODELS/'eng.provenance.json',dict(source_url=MODEL_URL,repository='https://github.com/tesseract-ocr/tessdata_fast',retrieved_at_utc=now(),sha256=hashlib.sha256(model.read_bytes()).hexdigest(),bytes=model.stat().st_size,purpose='English LSTM OCR of locally saved court PDFs',original_file='eng.traineddata',response_headers={k:v for k,v in response.headers.items() if k.lower() in ['etag','last-modified','content-type','content-length']}))
    sources=[r for r in read_jsonl(ROOT/'manifests/downloaded_documents.jsonl') if r.get('pdf_text_status')=='needs_ocr_or_text_unavailable']
    records=[];pages=[];started=time.monotonic()
    for rec in sources:
        path=ROOT/rec['raw_path'];assert hashlib.sha256(path.read_bytes()).hexdigest()==rec['sha256']
        doc_dir=OUT/rec['source_id'];render_dir=doc_dir/'rendered';render_dir.mkdir(parents=True,exist_ok=True)
        document=fitz.open(path)
        records.append(dict(source_id=rec['source_id'],source_url=rec['requested_url'],source_pdf_path=str(path),source_pdf_relative_path=rec['raw_path'],source_pdf_sha256=rec['sha256'],embedded_text_path=rec.get('text_path'),pdf_pages=len(document),label=rec.get('label',''),jurisdiction=rec['jurisdiction']))
        for i,page in enumerate(document):
            image_path=render_dir/f'page-{i+1:04d}.png'
            if not image_path.exists():page.get_pixmap(dpi=200,colorspace=fitz.csGRAY,alpha=False).save(image_path)
            pages.append(dict(source_id=rec['source_id'],page_number=i+1,image_path=str(image_path),image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),dpi=200,source_pdf_sha256=rec['sha256'],source_url=rec['requested_url']))
        document.close()
        print(json.dumps(dict(source_id=rec['source_id'],pages_rendered=len([p for p in pages if p['source_id']==rec['source_id']]),elapsed_seconds=round(time.monotonic()-started,1))),flush=True)
    with (OUT/'documents.jsonl').open('w',encoding='utf-8') as f:
        for rec in records:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
    with (OUT/'pages.jsonl').open('w',encoding='utf-8') as f:
        for rec in pages:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
    write_json(OUT/'queue_summary.json',dict(documents=len(records),pages=len(pages),render_dpi=200,model_path=str(model),model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),embedded_text_preserved=True,render_engine=fitz.VersionBind))
    print(json.dumps(dict(documents=len(records),pages=len(pages),elapsed_seconds=round(time.monotonic()-started,1))),flush=True)

if __name__=='__main__':prepare()
