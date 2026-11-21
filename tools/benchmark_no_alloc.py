#!/usr/bin/env python3
"""Paired, reproducible no_alloc cost checks; no source project is modified.

Keep the pre-change compiler outside the checkout, pass it with --baseline,
and build the candidate in release mode before running this script.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,default=ROOT/'rust/target/release/thor-rust')
    parser.add_argument('--runs',type=int,default=11)
    parser.add_argument('--include-io',action='store_true',help='include fixed-buffer input and fmt/basic output proofs')
    parser.add_argument('--include-rng',action='store_true',help='include newly supported default, seeded, and custom RNG proofs')
    parser.add_argument('--compare-enabled',action='store_true',help='also measure the baseline compiler with the contract enabled')
    parser.add_argument('--build-runs',type=int,default=3)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.runs<3 or args.build_runs<1:parser.error('use at least three transpile runs and one build run')
    binaries={'before':args.baseline.resolve(),'after':args.candidate.resolve()}
    for binary in binaries.values():
        if not binary.is_file():parser.error('compiler does not exist: '+str(binary))
    cases={
        'small':'package main\nOUTPUT:int\nwork::proc(value:int)->int{return value*2+1}\nmain::proc(){OUTPUT=work(7)}\n',
        'shared_call_graph':'package main\nOUTPUT:int\nleaf::proc(value:int)->int{return value+1}\n'+
            '\n'.join(f'work_{i}::proc(value:int)->int{{return leaf(value)+{i}}}' for i in range(1000))+
            '\nmain::proc(){'+''.join(f'OUTPUT+=work_{i}({i});' for i in range(1000))+'}\n',
        'sdk_reader':'package main\nimport "core:math"\nOUTPUT:f64\nalias::math.sqrt\nmain::proc(){OUTPUT=alias(1.5)}\n',
    }
    if args.include_io:
        cases.update({
            'fmt_io':'package main\nimport "core:os"\nimport "core:fmt"\nmain::proc(){buffer:[32]byte;n,e:=os.read(os.stdin,buffer[:]);_=e;fmt.printf("%d bytes: %s",n,string(buffer[:n]))}\n',
            'basic_io':'package main\nimport * "thor:basic"\nmain::proc(){print(1,true,"hello")}\n',
        })
    rng_cases={
        'default_rng':'package main\nimport "core:math/rand"\nOUTPUT:u64\nmain::proc(){'+
                      'OUTPUT=rand.uint64();'*100+'}\n',
        'seeded_rng':'package main\nimport "core:math/rand"\nOUTPUT:u64\nmain::proc(){'+
                     'state:=rand.create(u64(42));generator:=rand.default_random_generator(&state);'+
                     'OUTPUT=rand.uint64(generator);'*100+'}\n',
        'custom_rng':'package main\nimport "core:math/rand"\nimport "base:runtime"\nOUTPUT:u64\n'+
                     'fill::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){if mode==.Read {for &byte in buffer {byte=7}}}\n'+
                     'main::proc(){generator:=rand.Generator{procedure=fill};'+
                     'OUTPUT=rand.uint64(generator);'*100+'}\n',
    }
    if args.include_rng:cases.update(rng_cases)
    environment={**os.environ,'THOR_ROOT':str(ROOT)}
    report={'platform':platform.platform(),'scope':'paired warm process wall times; shared parser/evaluation cache; isolated output directories; source fixed between engines',
            'normalization':'blank lines and proven-unused generated base:intrinsics imports only; original hashes and ignored declarations retained per sample',
            'binaries':{k:{'path':str(v),'sha256':hashlib.sha256(v.read_bytes()).hexdigest()} for k,v in binaries.items()},'cases':{}}
    with tempfile.TemporaryDirectory(prefix='thor-noalloc-bench-') as temporary:
        root=Path(temporary)
        for name,body in cases.items():
            source=root/name/'main.thor';source.parent.mkdir()
            marker='#+vet no_alloc'
            def invoke(engine,enabled,command,index):
                source.write_text((marker if enabled else ' '*len(marker))+'\n'+body)
                label=engine+('-enabled' if enabled else '')
                profile=root/f'{name}-{label}-{command}-{index}.json'
                output=root/'output'/name/label
                argv=[str(binaries[engine]),command,str(source),'--cache-dir',str(root/'cache'),
                      '--output-dir',str(output),'-thread-count:1']
                if command=='build':argv.extend(['-o:speed',f'-out:{root/(name+"-program")}'])
                started=time.perf_counter()
                result=subprocess.run(argv,cwd=ROOT,env={**environment,'THOR_PROFILE':str(profile)},capture_output=True,text=True,timeout=120)
                elapsed=time.perf_counter()-started
                if result.returncode:raise RuntimeError(' '.join(argv)+'\n'+result.stdout+result.stderr)
                info=json.loads(profile.read_text())
                code=next(output.rglob('main.odin')).read_text()
                lines=[line for line in code.splitlines() if line.strip()]
                ignored=[]
                for line in lines:
                    match=re.fullmatch(r'import (__thor_[0-9a-f]+_meta) "base:intrinsics"',line.strip())
                    if match and len(re.findall(r'\b'+re.escape(match[1])+r'\b',code))==1:ignored.append(line)
                # Intrinsics are a compiler namespace with no runtime initializer.
                # Preserve all actual code and every other import; retain the
                # original digest so this normalization remains inspectable.
                normalized='\n'.join(line for line in lines if line not in ignored)
                return {'seconds':elapsed,'profile':info,'ignored_unused_intrinsic_imports':ignored,
                        'full_emitted_sha256':hashlib.sha256('\n'.join(lines).encode()).hexdigest()},hashlib.sha256(normalized.encode()).hexdigest()
            variants=[('before',False),('after',False),('after',True)]
            if args.compare_enabled and name not in rng_cases:variants.insert(1,('before',True))
            item={'transpile':{},'build':{},'emitted_sha256':{}}
            for variant in variants:invoke(*variant,'transpile','warmup')
            for command,count in [('transpile',args.runs),('build',args.build_runs)]:
                for i in range(count):
                    for engine,enabled in variants if i%2==0 else reversed(variants):
                        result,digest=invoke(engine,enabled,command,i)
                        label=engine+('-enabled' if enabled else '')
                        item[command].setdefault(label,[]).append(result)
                        item['emitted_sha256'][label]=digest
            item['emitted_identical']=len(set(item['emitted_sha256'].values()))==1
            if not item['emitted_identical']:raise RuntimeError('Generated native code changed: '+name)
            item['medians']={command:{label:statistics.median(r['seconds'] for r in runs) for label,runs in results.items()}
                             for command,results in item.items() if command in {'build','transpile'}}
            report['cases'][name]=item
            print(name,json.dumps(item['medians']),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
