"""Export reviewed code to a separate clean checkout without data or source history."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CODE={'.py','.mjs','.js','.cjs','.css','.html','.sql','.ps1','.cmd','.sh','.toml','.yaml','.yml'}
EXTRA_ROOT={'.gitignore','.gitattributes','.dockerignore','.vercelignore','.env.example','README.md','requirements.txt','package.json','package-lock.json','middleware.js','vercel.json','compose.yaml','bootstrap.py','TRANSFER.md','data_manifest.json'}
TREES={'deploy','api','supabase','pipeline','scripts','tools','sources','corpus'}

def allowed(name):
    path=Path(name);parts=path.parts
    if any(part in {'.auth','.firecrawl','.git','node_modules','__pycache__'} for part in parts):return False
    if len(parts)==1:return name in EXTRA_ROOT
    if parts[:2]==('delivery','archive-directory'):
        return path.suffix in CODE or path.name=='README.md' or name in {'delivery/archive-directory/assets/us-counties-albers-10m.json','delivery/archive-directory/assets/us-counties-albers-10m.SOURCE.txt'}
    if parts[0] in TREES:
        return path.suffix in CODE or path.name in {'README.md','requirements.txt','config.toml'}
    return False

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',type=Path);args=parser.parse_args();dest=args.destination.resolve()
    if dest==ROOT or dest.is_relative_to(ROOT):raise ValueError('Use a separate destination outside the source corpus')
    listed=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
    for folder in ['deploy','api','supabase','sources/docsupload_coverage_20260927']:
        listed.extend(p.relative_to(ROOT).as_posix() for p in (ROOT/folder).rglob('*') if p.is_file())
    listed.extend(EXTRA_ROOT)
    listed.extend(['delivery/archive-directory/docsupload_coverage.py','delivery/archive-directory/test_docsupload_coverage.py','delivery/archive-directory/hosting.py','delivery/archive-directory/test_hosting.py','delivery/archive-directory/test_ui_release.cjs','sources/county_litigation_20260919/publish_saved_checkpoint.py','sources/county_litigation_20260919/test_publish_saved_checkpoint.py'])
    files=sorted({name for name in listed if name and allowed(name) and (ROOT/name).is_file()})
    if dest.exists() and any(dest.iterdir()):raise ValueError('Destination must be empty; never overwrite an existing checkout')
    dest.mkdir(parents=True,exist_ok=True);receipt=[]
    for name in files:
        source=ROOT/name;target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        receipt.append({'path':name,'bytes':source.stat().st_size,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    (ROOT/'reports/supabase_migration_20260927/repository_export.json').write_text(json.dumps({'destination':str(dest),'files':receipt,'bytes':sum(r['bytes'] for r in receipt)},indent=2)+'\n','utf-8')
    print(json.dumps({'files':len(files),'bytes':sum(r['bytes'] for r in receipt),'destination':str(dest)}))

if __name__=='__main__':main()
