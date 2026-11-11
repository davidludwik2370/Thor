#!/usr/bin/env python3
"""Check input boundaries and error paths with already-built copied programs."""
import hashlib,json
from audit import CORPUS,ART,write_json
from runtime import run,normalize
cases={r['id']:r for r in json.loads((ART/'runtime-console-merged.json').read_text())};rows=[]
for size in [0,1,55,56,63,64,65,4096]:
 payload=bytes((i*37)%256 for i in range(size));expected=''.join(format(byte,'x')for byte in hashlib.md5(payload).digest())+'\n';row={'case':'md5 stdin','bytes':size}
 for mode in ['odin','thor','idiomatic']:
  directory=CORPUS/cases[30][mode]['source'];result=run(directory/'audit-program',directory,input_data=payload)
  if result['status']!=0 or result['stdout']!=expected:raise RuntimeError((mode,size,result))
  row[mode]={'status':result['status'],'digest':result['stdout'].strip()}
 rows.append(row)
for label,arguments,expected_status,needle in [('md5 file',['audit-payload.bin'],0,None),('md5 missing file',['audit-not-found.bin'],1,'Error opening file:'),('md5 usage',['a','b'],1,'Usage: md5')]:
 row={'case':label};payload=bytes(range(256))*4
 for mode in ['odin','thor','idiomatic']:
  directory=CORPUS/cases[30][mode]['source'];(directory/'audit-payload.bin').write_bytes(payload)
  result=run(directory/'audit-program',directory,arguments)
  if result['status']!=expected_status:raise RuntimeError((label,mode,result))
  if needle and needle not in result['stderr']:raise RuntimeError((label,mode,result))
  if not needle and result['stdout']!=''.join(format(byte,'x')for byte in hashlib.md5(payload).digest())+'\n':raise RuntimeError((label,mode,result))
  row[mode]={'status':result['status'],'stdout':result['stdout'],'stderr':result['stderr']}
 rows.append(row)
for arguments in [[],['0'],['1'],['20'],['invalid'],['1','2']]:
 row={'case':'collatz arguments','arguments':arguments}
 for mode in ['odin','thor','idiomatic']:
  directory=CORPUS/cases[11][mode]['source'];result=run(directory/'audit-program',directory,arguments)
  if result['status']!=0:raise RuntimeError(result)
  row[mode]={'status':result['status'],'stdout':result['stdout']}
 if not row['odin']==row['thor']==row['idiomatic']:raise RuntimeError(row)
 rows.append(row)
write_json(ART/'cli-cases.json',rows);print('Passed',len(rows),'CLI scenarios in all three variants')
