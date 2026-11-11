#!/usr/bin/env python3
"""Run the original projects' own test suites in each copied variant."""
import argparse,json,os,signal,subprocess,time
from audit import CORPUS,ART,ODIN,THOR,write_json
parser=argparse.ArgumentParser();parser.add_argument('--only');options=parser.parse_args()
rows=json.loads((ART/'project-tests.json').read_text()) if options.only else []
rows=[r for r in rows if r['label']!=options.only]
for relative in ['Odin/filter','Odin/poker','Odin/tic-tac-toe','Odin_Projects/tic-tac-toe_gui']:
 if options.only and relative!=options.only:continue
 row={'label':relative}
 for mode in ['odin','thor','idiomatic']:
  source=CORPUS/mode/relative
  args=[str(ODIN),'test',str(source),'-o:speed'] if mode=='odin' else [str(THOR),'test',str(source),'--output-dir',str(ART/'test-generated'/mode/relative),'--cache-dir',str(ART/'test-cache'/mode/relative),'-o:speed']
  start=time.monotonic();p=subprocess.Popen(args,cwd=source,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
  timeout=False
  try:output,_=p.communicate(timeout=90)
  except subprocess.TimeoutExpired:
   timeout=True;os.killpg(p.pid,signal.SIGKILL);output,_=p.communicate()
  log=ART/'logs'/'project-tests'/mode/(relative.replace('/','-')+'.log');log.parent.mkdir(parents=True,exist_ok=True);log.write_bytes(output)
  row[mode]={'status':p.returncode,'timeout':timeout,'seconds':time.monotonic()-start,'log':str(log.relative_to(CORPUS))}
  print(relative,mode,p.returncode,flush=True)
 rows.append(row);write_json(ART/'project-tests.json',rows)
