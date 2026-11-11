#!/usr/bin/env python3
"""Repeat real seeded CPU workloads after correctness checks, without compilers."""
import json,statistics
from pathlib import Path
from audit import CORPUS,ART,write_json
from runtime import run,normalize
cases={r['id']:r for r in json.loads((ART/'runtime-console-merged.json').read_text())}
rows=[]
for index in [23,24,38,41]:
 case=cases[index];row={'id':index,'label':case['label']};samples={m:[]for m in ['odin','thor','idiomatic']}
 for attempt in range(5):
  modes=['odin','thor','idiomatic'];modes=modes[attempt%3:]+modes[:attempt%3]
  for mode in modes:
   source=CORPUS/case[mode]['source'];result=run(source/'audit-program',source)
   if result['status']!=0 or normalize(result['stdout'],case)!=case[mode]['normalized_stdout']:
    raise RuntimeError(f'Workload result changed: {case["label"]} {mode}')
   samples[mode].append(result['seconds'])
 for mode,times in samples.items():row[mode]={'seconds':times,'median_seconds':statistics.median(times)}
 for mode in ['thor','idiomatic']:row[mode]['ratio_to_odin']=row[mode]['median_seconds']/row['odin']['median_seconds']
 rows.append(row);write_json(ART/'runtime-benchmarks.json',rows)
 print(case['label'],*(f'{m}:{row[m]["median_seconds"]:.4f}'for m in samples),flush=True)
