#!/usr/bin/env python3
"""Matched flags, sequential builds, rotating order and warm compile-time caches."""
import argparse,json,statistics,time
from pathlib import Path
from audit import ART,build,write_json,verify_originals

def main():
 p=argparse.ArgumentParser();p.add_argument('--repeats',type=int,default=3);p.add_argument('--ids');p.add_argument('--report',default='build-benchmarks.json');args=p.parse_args()
 cases=json.loads((ART/'inventory.json').read_text())['cases']
 valid={r['id']for r in json.loads((ART/'prepared-builds.json').read_text()) if r['odin']['status']==0}
 if args.ids:valid&={int(n)for n in args.ids.split(',')}
 result=[]
 for case in cases:
  if case['id'] not in valid:continue
  row={'id':case['id'],'label':case['label']};samples={m:[]for m in ['odin','thor','idiomatic']}
  for attempt in range(args.repeats):
   modes=['odin','thor','idiomatic'];modes=modes[attempt%3:]+modes[:attempt%3]
   for mode in modes:
    measured=build(case,mode,f'{Path(args.report).stem}-{attempt}')
    samples[mode].append(measured)
    if measured['status']!=0:raise RuntimeError(f'Benchmark build failed: {case["label"]}: {measured}')
  for mode in samples:row[mode]={'median_seconds':statistics.median(x['seconds']for x in samples[mode]),'samples':samples[mode]}
  for mode in ['thor','idiomatic']:
   row[mode]['added_seconds']=row[mode]['median_seconds']-row['odin']['median_seconds']
   row[mode]['ratio']=row[mode]['median_seconds']/row['odin']['median_seconds']
  result.append(row);write_json(ART/args.report,result)
  print(case['id'],case['label'],*(f'{m}:{row[m]["median_seconds"]:.3f}'for m in samples),flush=True)
 verify_originals()
if __name__=='__main__':main()
