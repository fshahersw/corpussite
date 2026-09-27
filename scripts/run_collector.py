"""Hidden process wrapper with independent lifetime, private credentials and logs."""
import argparse,ctypes,datetime,json,msvcrt,os,pathlib,runpy,subprocess,sys,traceback
ROOT=pathlib.Path(__file__).resolve().parents[1]
JOBS={
 'official-laws':(ROOT/'sources/official_laws/collect_official_laws.py',['documents'],ROOT/'sources/official_laws'),
 'official-law-resume3':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_law_resume_pass3_20260913'),'run','--max-pages','0','--max-seconds','3600'],ROOT/'corpus/official_law_resume_pass3_20260913'),
 'official-law-quick':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_law_resume_quick_20260914'),'run','--max-pages','0','--max-seconds','900'],ROOT/'corpus/official_law_resume_quick_20260914'),
 'official-law-state-rules':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_law_state_rules_followup_20260914'),'run','--max-pages','0','--max-seconds','1200'],ROOT/'corpus/official_law_state_rules_followup_20260914'),
 'county-entries-continuation':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/county_entries_continuation_20260913'),'run','--max-pages','0','--max-seconds','900'],ROOT/'corpus/county_entries_continuation_20260913'),
 'county-registry-continuation':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/county_registry_continuation_20260913'),'run','--max-pages','0','--max-seconds','1200'],ROOT/'corpus/county_registry_continuation_20260913'),
 'county-local-documents':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/county_local_documents_20260914'),'run','--max-pages','0','--max-seconds','1200'],ROOT/'corpus/county_local_documents_20260914'),
 'county-wa-rules':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/county_local_rules_washington_20260914'),'run','--max-pages','0','--max-seconds','1200'],ROOT/'corpus/county_local_rules_washington_20260914'),
 'official-courts':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_courts'),'run','--max-pages','0','--max-seconds','0','--wait-for-retries'],ROOT/'corpus/official_courts'),
 'official-law-pages':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_law_pages'),'run','--max-pages','0','--max-seconds','0','--wait-for-retries'],ROOT/'corpus/official_law_pages'),
 'official-law-recovery':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_law_recovery_pass_1'),'run','--max-pages','0','--max-seconds','0','--wait-for-retries'],ROOT/'corpus/official_law_recovery_pass_1'),
 'official-law-nebraska':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/official_law_nebraska_chapters'),'run','--max-pages','0','--max-seconds','0','--wait-for-retries'],ROOT/'corpus/official_law_nebraska_chapters'),
 'county-sites':(ROOT/'pipeline/corpus_crawler.py',['--root',str(ROOT/'corpus/county_sites'),'run','--max-pages','0','--max-seconds','0','--wait-for-retries'],ROOT/'corpus/county_sites'),
 'firecrawl':(ROOT/'pipeline/firecrawl_worker.py',['--workers','2'],ROOT/'sources/trellis/worker'),
 'firecrawl-batch':(ROOT/'pipeline/firecrawl_batch_worker.py',['--run','--batch-size','50','--cache-max-age-ms','86400000','--page-timeout-ms','180000'],ROOT/'sources/trellis/worker'),
 'ocr':(ROOT/'sources/official_courts/ocr_worker.cjs',[],ROOT/'sources/official_courts/ocr'),
 'ocr-courts':(ROOT/'sources/official_courts/ocr_worker.cjs',[],ROOT/'corpus/official_courts/ocr'),
 'ocr-laws':(ROOT/'pipeline/prepare_law_ocr.py',['--run-ocr'],ROOT/'sources/official_laws/ocr'),
 'ocr-law-recovery':(ROOT/'sources/official_courts/ocr_worker.cjs',[],ROOT/'corpus/official_law_recovery_pass_1/ocr'),
 'ocr-law-pages':(ROOT/'pipeline/prepare_court_ocr.py',['--root',str(ROOT/'corpus/official_law_pages'),'--run-ocr'],ROOT/'corpus/official_law_pages/ocr'),
}
def timestamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def private_key(credential_file=None):
    # Windows DPAPI decrypts only for the Windows user who encrypted the key.
    class DataBlob(ctypes.Structure):
        _fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
    key_path=pathlib.Path(credential_file) if credential_file else pathlib.Path.home()/'AppData/Local/LegalCorpusArchive/firecrawl-key.dpapi'
    encrypted=bytes.fromhex(key_path.read_text(encoding='utf-8-sig').strip())
    buffer=(ctypes.c_ubyte*len(encrypted)).from_buffer_copy(encrypted)
    source=DataBlob(len(encrypted),buffer); decrypted=DataBlob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(DataBlob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(DataBlob)]
    crypt.CryptUnprotectData.restype=ctypes.c_int
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(decrypted)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:key=ctypes.string_at(decrypted.data,decrypted.size).decode('utf-16-le')
    finally:
        ctypes.memset(decrypted.data,0,decrypted.size)
        kernel.LocalFree(decrypted.data)
    if not key.startswith('fc-'):raise RuntimeError('No usable private Firecrawl credential')
    return key
def main():
    p=argparse.ArgumentParser();p.add_argument('job',choices=JOBS);p.add_argument('--pages',type=int);p.add_argument('--credential-file');p.add_argument('--selection-file');p.add_argument('--once-batch',action='store_true');p.add_argument('--seconds',type=int);args=p.parse_args()
    if (args.selection_file or args.once_batch) and args.job!='firecrawl-batch':p.error('Batch selection options require firecrawl-batch')
    if args.seconds is not None and (args.job not in {'official-law-resume3','official-law-quick','official-law-state-rules','county-entries-continuation','county-registry-continuation','county-local-documents','county-wa-rules'} or not 1 <= args.seconds <= 3600):p.error('Seconds requires a finite continuation job and a budget of 1 to 3600')
    script,argv,logdir=JOBS[args.job];argv=list(argv)
    if args.seconds is not None:argv[argv.index('--max-seconds')+1]=str(args.seconds)
    if args.selection_file:argv.extend(['--selection-file',str(pathlib.Path(args.selection_file).resolve(strict=True))])
    if args.once_batch:argv=[value for value in argv if value!='--run'];argv.append('--once-batch')
    logdir.mkdir(parents=True,exist_ok=True)
    with (logdir/'collector.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return
        try:
            with (logdir/'background.log').open('a',encoding='utf-8',buffering=1) as out,(logdir/'background.errors.log').open('a',encoding='utf-8',buffering=1) as err:
                sys.stdout=out;sys.stderr=err
                metadata={'process_id':os.getpid(),'job':args.job,'started_at_utc':timestamp(),'status':'running','command':['python',str(script),*argv],'stdout':str(logdir/'background.log'),'stderr':str(logdir/'background.errors.log'),'working_directory':str(script.parent),'launch_method':'Windows WMI hidden process'}
                active=logdir/'active_run.json';active.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
                print(json.dumps({'event':'started','job':args.job,'pid':os.getpid(),'at':timestamp()}),flush=True)
                try:
                    if args.job in ('firecrawl','firecrawl-batch'):os.environ['FIRECRAWL_API_KEY']=private_key(args.credential_file)
                    if args.pages is not None and args.job=='firecrawl':argv=[*argv,'--pages',str(args.pages)]
                    os.chdir(script.parent);sys.path.insert(0,str(script.parent));sys.argv=[str(script),*argv]
                    if args.job in ('ocr','ocr-courts','ocr-law-recovery'):
                        os.environ['OFFICIAL_OCR_RUN_SECONDS']='7200'
                        os.environ['OFFICIAL_OCR_ROOT']=str(logdir)
                        os.environ['OFFICIAL_OCR_WORKERS']='2'
                        result=subprocess.run(['C:/Program Files/nodejs/node.exe',str(script)],stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
                        if result.returncode:raise RuntimeError('OCR worker exit '+str(result.returncode))
                    else:
                        try:runpy.run_path(str(script),run_name='__main__')
                        except SystemExit as exit_result:
                            if exit_result.code not in (None,0):raise
                    metadata['status']='exited'
                except BaseException as exc:
                    metadata['status']='failed';metadata['error']=type(exc).__name__+': '+str(exc)
                    trace=traceback.format_exc();key=os.environ.get('FIRECRAWL_API_KEY','')
                    if key:trace=trace.replace(key,'[REDACTED]');metadata['error']=metadata['error'].replace(key,'[REDACTED]')
                    print(trace,file=err,flush=True)
                finally:
                    metadata['ended_at_utc']=timestamp();active.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
                    print(json.dumps({'event':metadata['status'],'job':args.job,'at':timestamp()}),flush=True)
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
if __name__=='__main__':main()
