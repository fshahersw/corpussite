"""Archive explicitly mapped browser downloads with byte hashes and PDF text."""
import argparse, datetime, hashlib, json, pathlib, shutil
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEST = ROOT / 'sources' / 'trellis' / 'browser'

def ingest(record):
    source = pathlib.Path(record['download_path'])
    if not source.is_file():
        return {**record, 'status': 'download_not_present'}
    raw = source.read_bytes()
    if not raw.startswith(b'%PDF-'):
        return {**record, 'status': 'invalid_pdf'}
    digest = hashlib.sha256(raw).hexdigest()
    target_dir = DEST / record['jurisdiction'] / record['county'] / record['case_number']
    target_dir.mkdir(parents=True, exist_ok=True)
    name = record['kind'] + '_' + str(record.get('document_id',digest[:12]))
    target = target_dir / (name + '.pdf')
    shutil.copy2(source, target)
    reader = PdfReader(target)
    text = '\n\n'.join(f'--- PAGE {i+1} ---\n' + (page.extract_text() or '') for i,page in enumerate(reader.pages))
    text_path = target.with_suffix('.txt')
    text_path.write_text(text, encoding='utf-8')
    meta = {**record, 'status':'downloaded', 'sha256':digest, 'bytes':len(raw), 'pages':len(reader.pages),
            'extracted_chars':len(text), 'raw_path':str(target.relative_to(ROOT)),
            'text_path':str(text_path.relative_to(ROOT)),
            'archived_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'provenance':'User-authorized paid Trellis Chrome session; ordinary visible download control'}
    target.with_suffix('.json').write_text(json.dumps(meta,indent=2), encoding='utf-8')
    return meta

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('manifest'); args=parser.parse_args()
    results=[ingest(json.loads(line)) for line in pathlib.Path(args.manifest).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    (DEST/'downloads_manifest.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps({'downloaded':sum(x['status']=='downloaded' for x in results),'bytes':sum(x.get('bytes',0) for x in results),'pages':sum(x.get('pages',0) for x in results),'results':[{k:r[k] for k in ('status','raw_path','pages') if k in r} for r in results]},indent=2))
