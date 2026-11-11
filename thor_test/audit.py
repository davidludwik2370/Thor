#!/usr/bin/env python3
"""Reproducible, isolated Odin -> Thor -> idiomatic Thor corpus checks."""
import argparse,concurrent.futures,hashlib,json,os,re,shutil,signal,statistics,subprocess,time
from pathlib import Path
CORPUS=Path(__file__).resolve().parent
COMPILER=CORPUS.parent
ROOTS=[Path('/Users/davidludwik/Code/Odin'),Path('/Users/davidludwik/Code/Odin_Projects')]
ODIN=Path(shutil.which('odin') or '/Users/davidludwik/Odin/Odin/odin').resolve()
THOR=COMPILER/'rust/target/release/thor-rust'
SKIP={'.git','node_modules','__pycache__','.DS_Store'}
ART=CORPUS/'artifacts'

def write_json(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(data,indent=2)+'\n');temporary.replace(path)

def source_files(root):
 for directory,dirs,names in os.walk(root,followlinks=False):
  at=Path(directory)
  dirs[:]=sorted(n for n in dirs if n not in SKIP and not n.startswith('.thor-') and not n.endswith(('.app','.dSYM')) and not (at/n).is_symlink() and not (at.name=='.vscode' and n=='bin'))
  for name in sorted(names):
   p=at/name
   if name in SKIP or p.is_symlink() or not p.is_file():continue
   # Omit generated executables, retaining foreign archives/shared libraries.
   if p.suffix not in {'.a','.dylib','.so','.dll','.o'}:
    with p.open('rb') as stream:magic=stream.read(4)
    if magic in {b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\x7fELF'}:continue
   yield p

def manifest():
 return {str(p):{'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for root in ROOTS for p in source_files(root)}

def code_only(text):
 # Preserve positions and nested comments while excluding string contents.
 result=list(text);i=0
 while i<len(text):
  start=i
  if text.startswith('//',i):
   end=text.find('\n',i);i=len(text) if end<0 else end
  elif text.startswith('/*',i):
   depth=1;i+=2
   while i<len(text) and depth:
    if text.startswith('/*',i):depth+=1;i+=2
    elif text.startswith('*/',i):depth-=1;i+=2
    else:i+=1
  elif text[i] in '\"\'`':
   quote=text[i];i+=1
   while i<len(text):
    if text[i]=='\\' and quote!='`':i+=2
    elif text[i]==quote:i+=1;break
    else:i+=1
  else:i+=1;continue
  for j in range(start,min(i,len(text))):
   if result[j]!='\n':result[j]=' '
 return ''.join(result)

def prepare():
 if (CORPUS/'odin').exists():raise RuntimeError('Snapshot already exists; preserve it and use the existing inventory.')
 before=manifest();write_json(ART/'original-files-before.json',before)
 for original in before:
  p=Path(original);root=next(r for r in ROOTS if p.is_relative_to(r));rel=Path(root.name)/p.relative_to(root)
  target=CORPUS/'odin'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
 for mode in ['thor','idiomatic']:
  shutil.copytree(CORPUS/'odin',CORPUS/mode)
  for source in sorted((CORPUS/mode).rglob('*.odin')):
   target=source.with_suffix('.thor')
   if target.exists():raise RuntimeError('Source extension collision: '+str(source))
   source.rename(target)
 cases=[]
 packages=sorted({p.parent for p in (CORPUS/'odin').rglob('*.odin')})
 for directory in packages:
  sources=sorted(directory.glob('*.odin'));regular=[p for p in sources if not p.name.endswith('_test.odin')]
  clean={p.name:code_only(p.read_text(errors='replace')) for p in regular}
  mains=[name for name,text in clean.items() if re.search(r'\bmain\s*::\s*proc\s*\(',text)]
  package_main=any(re.search(r'\bpackage\s+main\b',text) for text in clean.values())
  rel=str(directory.relative_to(CORPUS/'odin'))
  cases.append({'id':len(cases),'label':rel,'relative':rel,'kind':'package' if mains else 'library','file':False,'mains':mains,'sources':[str(p.relative_to(CORPUS/'odin')) for p in sources]})
  if len(mains)>1:
   for name in mains:cases.append({'id':len(cases),'label':rel+'/'+name,'relative':rel+'/'+name,'kind':'standalone','file':True,'mains':[name],'sources':[rel+'/'+name]})
 write_json(ART/'inventory.json',{'roots':[str(r) for r in ROOTS],'original_files':len(before),'original_bytes':sum(row['size'] for row in before.values()),'cases':cases})
 verify_originals()
 print('Copied',len(before),'files;',len(packages),'packages;',len(cases),'build cases',flush=True)

def verify_originals():
 before=json.loads((ART/'original-files-before.json').read_text());after=manifest()
 changed=[p for p in sorted(before.keys()|after.keys()) if before.get(p)!=after.get(p)]
 write_json(ART/'original-files-changed.json',changed)
 if changed:raise RuntimeError('Original files changed: '+repr(changed))
 return True

def entry(case,mode):
 path=CORPUS/mode/case['relative']
 return path.with_suffix('.thor') if mode!='odin' and case['file'] else path

def build(case,mode,tag='baseline',flags=None,profile=False):
 flags=['-o:speed'] if flags is None else flags
 src=entry(case,mode);out=ART/'bin'/tag/mode/f"{case['id']:03}";out.parent.mkdir(parents=True,exist_ok=True)
 args=[str(ODIN),'build',str(src)] if mode=='odin' else [str(THOR),'build',str(src),'--output-dir',str(ART/'generated'/mode/f"{case['id']:03}"),'--cache-dir',str(ART/'cache'/mode/f"{case['id']:03}")]
 if case['file'] and mode=='odin':args+=['-file']
 if case['kind']=='library':args+=['-build-mode:static','-no-entry-point']
 args+=['-out:'+str(out),*flags]
 log=ART/'logs'/tag/f"{case['id']:03}-{mode}.log";log.parent.mkdir(parents=True,exist_ok=True)
 environment={**os.environ,'THOR_ROOT':str(COMPILER),'THOR_JOBS':'4'};environment.pop('THOR_PROFILE',None)
 if profile and mode!='odin':environment['THOR_PROFILE']=str(log.with_suffix('.profile.json'))
 start=time.monotonic()
 process=subprocess.Popen(args,cwd=src.parent if case['file'] else src,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=environment,start_new_session=True)
 timeout=False
 try:output,_=process.communicate(timeout=90)
 except subprocess.TimeoutExpired:
  timeout=True;os.killpg(process.pid,signal.SIGTERM)
  try:output,_=process.communicate(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);output,_=process.communicate()
 status=process.returncode
 log.write_bytes(output)
 binary=out.with_suffix('.a') if case['kind']=='library' else out
 return {'command':args,'status':status,'timeout':timeout,'seconds':time.monotonic()-start,'log':str(log.relative_to(CORPUS)),'binary':str(binary.relative_to(CORPUS)),'binary_exists':binary.is_file()}

def builds(modes,tag,ids=None,flags=None):
 inventory=json.loads((ART/'inventory.json').read_text());cases=[c for c in inventory['cases'] if ids is None or c['id'] in ids]
 output=ART/(tag+'-builds.json');rows=[]
 def job(case):return {'id':case['id'],'label':case['label'],**{m:build(case,m,tag,flags=flags) for m in modes}}
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  for row in pool.map(job,cases):
   rows.append(row);write_json(output,rows)
   print(len(rows),'/',len(cases),row['label'],*(m+':'+str(row[m]['status']) for m in modes),flush=True)
 verify_originals()
 failures=[r['label'] for r in rows if 'odin' in r and r['odin']['status']==0 and any(r[m]['status']!=0 for m in modes if m!='odin')]
 print('Conversion-only failures:',failures,flush=True)
 return rows

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','verify']);p.add_argument('--modes',default='odin,thor');p.add_argument('--tag',default='baseline');p.add_argument('--ids');p.add_argument('--unchecked',action='store_true');args=p.parse_args()
 if args.action=='prepare':prepare()
 elif args.action=='verify':verify_originals();print('Originals unchanged')
 else:builds(args.modes.split(','),args.tag,None if args.ids is None else {int(x) for x in args.ids.split(',')},['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'] if args.unchecked else None)
if __name__=='__main__':main()
