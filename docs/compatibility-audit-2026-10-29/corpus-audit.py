#!/usr/bin/env python3
"""Read-only corpus audit: native Odin vs copied/renamed Thor builds."""
import concurrent.futures, hashlib, json, os, re, shutil, signal, subprocess, tempfile, threading, time
from pathlib import Path
ROOTS=[Path('/Users/davidludwik/Code/Odin'),Path('/Users/davidludwik/Code/Odin_Projects')]
THOR=Path('/Users/davidludwik/Thor/thor')
ODIN=Path('/Users/davidludwik/Odin/Odin/odin')
SKIP={'.git','.thor-build','.thor-cache','node_modules','__pycache__'}
AUDIT=Path(tempfile.mkdtemp(prefix='thor-project-audit-'))
Path('/tmp/thor-project-audit-path').write_text(str(AUDIT))
(AUDIT/'runner.pid').write_text(str(os.getpid()))
(AUDIT/'logs').mkdir()
print('AUDIT',AUDIT,flush=True)

def files(root):
 for d,dirs,names in os.walk(root,followlinks=False):
  p=Path(d)
  dirs[:]=[n for n in sorted(dirs) if n not in SKIP and not n.endswith(('.app','.dSYM')) and not (p/n).is_symlink() and not (p.name=='.vscode' and n=='bin')]
  for n in sorted(names):
   q=p/n
   if q.is_file() and not q.is_symlink():yield q

def manifest():
 return {str(p):{'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for r in ROOTS for p in files(r)}

before=manifest()
(AUDIT/'original-files-before.json').write_text(json.dumps(before,indent=2))
for mode in ('odin','thor'):
 for root in ROOTS:
  for p in files(root):
   rel=p.relative_to(root)
   dest=AUDIT/mode/root.name/rel
   if mode=='thor' and dest.suffix=='.odin':dest=dest.with_suffix('.thor')
   if dest.exists():raise RuntimeError(f'Copy collision: {p} -> {dest}')
   dest.parent.mkdir(parents=True,exist_ok=True)
   shutil.copy2(p,dest)

inv=json.loads(Path(__file__).with_name('inventory.json').read_text())
cases=[]
for p in sorted(inv['packages'],key=lambda p:p['path']):
 path=Path(p['path'])
 root=next(r for r in ROOTS if path.is_relative_to(r))
 rel=Path(root.name)/path.relative_to(root)
 cases.append({'label':str(rel),'original':str(path),'relative':str(rel),'kind':'package' if p['main'] else 'library','file':None})
 mains=[f for f in p['files'] if re.search(r'\bmain\s*::\s*proc\s*\(', (path/f).read_text())]
 if len(mains)>1:
  for f in sorted(mains):
   cases.append({'label':str(rel/f),'original':str(path/f),'relative':str(rel/f),'kind':'standalone-version','file':f})
(AUDIT/'cases.json').write_text(json.dumps(cases,indent=2))
print('CASES',len(cases),flush=True)
lock=threading.Lock()
results=[]

def build(case,index,mode):
 src=AUDIT/mode/case['relative']
 if mode=='thor' and src.suffix=='.odin':src=src.with_suffix('.thor')
 out=AUDIT/'bin'/f'{index:03}-{mode}'
 out.parent.mkdir(exist_ok=True)
 flags=['-out:'+str(out)]
 if case['kind']=='library':flags+=['-build-mode:static','-no-entry-point']
 if case['file'] and mode=='odin':flags+=['-file']
 if mode=='odin':cmd=[str(ODIN),'build',str(src),*flags]
 else:cmd=[str(THOR),'build',str(src),'--output-dir',str(AUDIT/'generated'/str(index)),'--cache-dir',str(AUDIT/'cache'/str(index)),*flags]
 log=AUDIT/'logs'/f'{index:03}-{mode}.log'
 start=time.monotonic()
 with log.open('wb') as f:
  proc=subprocess.Popen(cmd,cwd=src.parent if case['file'] else src,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  timeout=False
  try:code=proc.wait(timeout=120)
  except subprocess.TimeoutExpired:
   timeout=True
   os.killpg(proc.pid,signal.SIGKILL)
   code=proc.wait()
 if case['kind']=='library':out=out.with_name(out.name+'.a')
 return {'command':cmd,'returncode':code,'timeout':timeout,'seconds':round(time.monotonic()-start,3),'log':str(log),'output':str(out),'output_exists':out.is_file()}

def work(t):
 i,case=t
 r={'index':i,**case}
 r['odin']=build(case,i,'odin')
 r['thor']=build(case,i,'thor')
 with lock:
  results.append(r)
  (AUDIT/'results.json').write_text(json.dumps(sorted(results,key=lambda r:r['index']),indent=2))
  print(f"{len(results):3}/{len(cases)} {case['label']}: Odin={r['odin']['returncode']} Thor={r['thor']['returncode']} ({r['odin']['seconds']}s/{r['thor']['seconds']}s)",flush=True)
 return r

try:
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(work,enumerate(cases)))
finally:
 after=manifest()
 (AUDIT/'original-files-after.json').write_text(json.dumps(after,indent=2))
 changed=[p for p in sorted(before.keys()|after.keys()) if before.get(p)!=after.get(p)]
 (AUDIT/'original-files-changed.json').write_text(json.dumps(changed,indent=2))
 print('ORIGINAL FILES CHANGED',changed,flush=True)
summary={
 'cases':len(results),'odin_success':sum(r['odin']['returncode']==0 for r in results),
 'thor_success':sum(r['thor']['returncode']==0 for r in results),
 'thor_regressions':[r['label'] for r in results if r['odin']['returncode']==0 and r['thor']['returncode']!=0],
 'both_fail':[r['label'] for r in results if r['odin']['returncode']!=0 and r['thor']['returncode']!=0],
 'thor_only_success':[r['label'] for r in results if r['odin']['returncode']!=0 and r['thor']['returncode']==0],
 'original_files_changed':changed,
}
(AUDIT/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2),flush=True)
