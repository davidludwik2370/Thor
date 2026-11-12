#!/usr/bin/env python3
"""Quiet warm builds, frontend scaling and separate instrumented profiles."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from audit import ART, COMPILER, THOR, build, entry, verify_originals, write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--ids',default='12,23,38,68,80,82')
    parser.add_argument('--report',default='optimization/measurements.json')
    args=parser.parse_args()
    selected={int(value) for value in args.ids.split(',')}
    cases=[case for case in json.loads((ART/'inventory.json').read_text())['cases']
           if case['id'] in selected]
    report={'compiler_sha256':hashlib.sha256(THOR.read_bytes()).hexdigest(),
            'scope':'Warm sequential -o:speed builds; warm transpile commands at jobs 1 and 4. Profiles are separate and excluded from wall-time samples.',
            'repeats':args.repeats,'projects':[]}
    environment={**os.environ,'THOR_ROOT':str(COMPILER)}
    environment.pop('THOR_PROFILE',None)
    for case in cases:
        source=entry(case,'idiomatic')
        files=[source] if source.is_file() else [p for p in source.rglob('*') if p.suffix in {'.odin','.thor'}]
        hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        row={'id':case['id'],'label':case['label'],'source_sha256':hashes,'build':{},'transpile':{}}
        samples={mode:[] for mode in ['odin','thor','idiomatic']}
        for mode in samples:
            warm=build(case,mode,'optimization-warmup')
            if warm['status']!=0:raise RuntimeError(warm)
        for attempt in range(args.repeats):
            modes=list(samples);modes=modes[attempt%3:]+modes[:attempt%3]
            for mode in modes:
                sample=build(case,mode,'optimization-measure')
                if sample['status']!=0:raise RuntimeError(sample)
                samples[mode].append(sample['seconds'])
        row['build']={mode:{'samples':times,'median_seconds':statistics.median(times)}
                      for mode,times in samples.items()}
        for jobs in [1,4]:
            times=[]
            for attempt in range(args.repeats+1):
                command=[str(THOR),'transpile',str(entry(case,'idiomatic')),
                         '--output-dir',str(ART/'generated/idiomatic'/f"{case['id']:03}"),
                         '--cache-dir',str(ART/'cache/idiomatic'/f"{case['id']:03}"),'-o:speed']
                started=time.monotonic()
                result=subprocess.run(command,capture_output=True,text=True,timeout=90,
                                      env={**environment,'THOR_JOBS':str(jobs)})
                elapsed=time.monotonic()-started
                if result.returncode:raise RuntimeError(result.stdout+result.stderr)
                if attempt:times.append(elapsed)
            row['transpile'][str(jobs)]={'samples':times,'median_seconds':statistics.median(times)}
        profiled=build(case,'idiomatic','optimization-profile',profile=True)
        if profiled['status']!=0:raise RuntimeError(profiled)
        path=ART/'logs/optimization-profile'/f"{case['id']:03}-idiomatic.profile.json"
        row['profile']=json.loads(path.read_text())
        if hashes!={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in hashes}:
            raise RuntimeError('Benchmark input changed: '+case['label'])
        report['projects'].append(row)
        write_json(ART/args.report,report)
        print(case['label'],{mode:round(data['median_seconds'],4) for mode,data in row['build'].items()},
              'transpile',{jobs:round(data['median_seconds'],4) for jobs,data in row['transpile'].items()},flush=True)
    verify_originals()


if __name__=='__main__':main()
