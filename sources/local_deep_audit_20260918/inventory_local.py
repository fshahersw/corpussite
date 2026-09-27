"""Read-only filename inventory of additional user project/data locations."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import json, os, re, shutil, subprocess, time

OUT = Path(__file__).resolve().parent
HOME_DIR = Path('C:/Users/firas')
ROOTS = [HOME_DIR / p for p in [
 'ansel','casemgtautomation','csv-download-automation-agent','fireplexity','foroffice','modal-app',
 'seeger-weiss-office-web','SeegerWeissAssistantUpgrade','SeegerWeissOffice','SeegerWeissOfficeAudit-20260909',
 'SeegerWeissOfficeWeb','SeegerWeissOfficeWebBuild','SeegerWeissOfficeWebRemainingBuild',
 'SeegerWeissPDF','SeegerWeissSheets','SeegerWeissSlides','SeegerWeissWriter','word-addin',
 '.claude-worktrees/FOR-CLAUDE','.claude-worktrees/MD-DONE',
 'OneDrive/10qly_seed','OneDrive/Cursortry','OneDrive/GITREPO','OneDrive/netlify','OneDrive/Documents','OneDrive/Desktop',
 'Downloads/gptagent','Downloads/gptagent2','Downloads/Frontier_Workspace','Downloads/Frontier_Workspace2',
 'Downloads/newdownloads','Downloads/returnedfiles','Downloads/mnt','Downloads/Depo-Provera Navigator',
 'Downloads/seeger-drafting','Downloads/seeger-drafting2','Downloads/seeger-drafting3',
 'Downloads/officeAI','Downloads/mco','Downloads/mco2','Desktop/word-addin-for-work',
]]
EXCLUDES = ['node_modules','vendor','.git','.next','dist','build','.venv','venv','__pycache__',
 '.cache','cache','.auth','.aws','.ssh','.mcp-auth','ChromeDebug','Cookies','Local Storage','Session Storage']
TERMS = re.compile(r'judge|judicial|court|county|counties|trellis|fjc|caselaw|statute|constitution|portrait|legal.{0,10}(data|source|corpus)|source.{0,10}register',re.I)

def main():
    started=time.time(); rg=shutil.which('rg'); assert rg
    summaries=[]; errors=[]; all_paths=set(); candidates=[]
    with (OUT/'additional_file_inventory.jsonl').open('w',encoding='utf8') as inventory:
        for folder in ROOTS:
            if not folder.is_dir():
                summaries.append({'root':str(folder),'exists':False});continue
            args=[rg,'--files','--hidden','--no-ignore']
            for name in EXCLUDES:args+=['-g','!**/'+name+'/**']
            for pattern in ['!**/.env*','!**/*credentials*','!**/*token*','!**/*secret*','!**/*auth*.json','!**/*.pem','!**/*.key']:
                args+=['-g',pattern]
            args+=[str(folder)]
            try:r=subprocess.run(args,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=90)
            except subprocess.TimeoutExpired:
                errors.append({'root':str(folder),'error':'90-second filename enumeration limit'});continue
            if r.stderr.strip():errors.append({'root':str(folder),'error':r.stderr.strip()[:1500]})
            counts=Counter();count=0;placeholder=0;matches=0
            for name in r.stdout.splitlines():
                path=Path(name);canonical=str(path).casefold()
                if canonical in all_paths:continue
                all_paths.add(canonical)
                try:stat=path.stat()
                except OSError as exc:errors.append({'path':str(path),'error':type(exc).__name__});continue
                relative=str(path.relative_to(folder))
                flags=getattr(stat,'st_file_attributes',0)
                is_placeholder=bool(flags & (0x1000|0x40000|0x400000))
                row={'path':str(path),'root':str(folder),'relative_path':relative,'bytes':stat.st_size,
                     'mtime_ns':stat.st_mtime_ns,'extension':path.suffix.lower(),'cloud_placeholder':is_placeholder}
                inventory.write(json.dumps(row,ensure_ascii=False)+'\n');count+=1;counts[row['extension']]+=1;placeholder+=is_placeholder
                if TERMS.search(relative):candidates.append(row);matches+=1
            summaries.append({'root':str(folder),'exists':True,'files':count,'name_candidates':matches,'cloud_placeholders':placeholder,'extensions':dict(counts.most_common(15))})
    (OUT/'additional_name_candidates.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in candidates),encoding='utf8')
    result={'completed_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.time()-started,2),
            'roots_attempted':len(ROOTS),'files_enumerated':len(all_paths),'name_candidates':len(candidates),
            'roots':summaries,'errors':errors,'excluded_directories':EXCLUDES,'external_files_modified':False,
            'file_contents_read':False,'cloud_files_hydrated':False,'method':'rg file names followed by filesystem metadata; no symlink following'}
    (OUT/'additional_inventory_summary.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'roots','excluded_directories'}}))

if __name__=='__main__':main()
