"""Apply compiler-verified Thor conventions only in the idiomatic copies."""
import argparse,concurrent.futures,json,os,subprocess,time
from pathlib import Path
from audit import ART,CORPUS,THOR,COMPILER,entry,write_json
parser=argparse.ArgumentParser();parser.add_argument('--standalone',action='store_true');parser.add_argument('--ids');options=parser.parse_args()
tag='vet-selected' if options.ids else 'vet-standalone' if options.standalone else 'vet'
cases=json.loads((ART/'inventory.json').read_text())['cases']
before={str(p.relative_to(CORPUS/'idiomatic')):p.read_text() for p in (CORPUS/'idiomatic').rglob('*.thor')}
write_json(ART/('before-'+tag+'.json'),before)
rows=[]
# File variants share directories, so edits are intentionally sequential.
for case in cases:
 if options.ids and case['id'] not in {int(n)for n in options.ids.split(',')}:continue
 if case['file']!=options.standalone:continue
 source=entry(case,'idiomatic')
 args=[str(THOR),'vet',str(source),'--apply','--vet=immutable,pure','--output-dir',str(ART/'vet-generated'/str(case['id'])),'--cache-dir',str(ART/'vet-cache'/str(case['id'])),'-json-errors']
 start=time.monotonic()
 try:
  done=subprocess.run(args,cwd=source.parent if source.is_file() else source,capture_output=True,text=True,env={**os.environ,'THOR_ROOT':str(COMPILER)},timeout=60)
  status=done.returncode;text=done.stdout+done.stderr
 except subprocess.TimeoutExpired as error:status='timeout';text=str(error)
 log=ART/'logs'/tag/f"{case['id']:03}.log";log.parent.mkdir(parents=True,exist_ok=True);log.write_text(text)
 row={'id':case['id'],'label':case['label'],'status':status,'seconds':time.monotonic()-start,'log':str(log.relative_to(CORPUS))}
 rows.append(row);write_json(ART/(tag+'-results.json'),rows)
 print(len(rows),case['label'],status,flush=True)
after={str(p.relative_to(CORPUS/'idiomatic')):p.read_text() for p in (CORPUS/'idiomatic').rglob('*.thor')}
changes=[]
for name,text in after.items():
 if before[name]!=text:
  changes.append({'file':name,'immutable_added':text.count(':>')-before[name].count(':>'),'pure_added':text.count('fn(')+text.count('fn (')-before[name].count('fn(')-before[name].count('fn (')})
write_json(ART/(tag+'-changes.json'),changes)
print('Changed files',len(changes),flush=True)
