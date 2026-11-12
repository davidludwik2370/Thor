#!/usr/bin/env python3
"""Compare readonly checking on copied projects and generated stress workloads."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from benchmark_lowering import ROOT, project_benchmark


def fixtures(directory):
    scalar=directory/'scalar_roots';scalar.mkdir()
    leaves='\n'.join(f'leaf_{n}::proc(x:int)->int{{return x+{n}}}' for n in range(300))
    (scalar/'main.thor').write_text('package main\n'+leaves+'''
main::proc(){items:=[1]int{7};view:>items[:];if view[0]!=7{panic("scalar roots")}}
''')
    chain=directory/'alias_chain';chain.mkdir()
    independent='\n'.join(f'fixed_{n}:=len(values)+{n};_=fixed_{n}' for n in range(48))
    names=', '.join(f'a{n}' for n in range(8))
    assignments=';'.join(f'a{n}=a{n+1}' for n in range(7))+';a7=values'
    (chain/'main.thor').write_text(f'''package main
propagate::proc(values:[]int)->[]int{{
{independent}
{names}:[]int
for _ in 0..<9 {{{assignments}}}
if a0!=nil{{a0[0]=9}}
return a0
}}
main::proc(){{seed:>0;_=seed;items:=[1]int{{7}};result:=propagate(items[:])
if result[0]!=9||items[0]!=9{{panic("alias propagation")}}}}
''')
    return [scalar,chain]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',required=True,type=Path)
    parser.add_argument('--after',type=Path,default=ROOT/'rust/target/release/thor-rust')
    parser.add_argument('--project',action='append',default=[],type=Path)
    parser.add_argument('--runs',type=int,default=7)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.runs<1:parser.error('--runs must be positive')
    engines={'before':args.before.resolve(),'after':args.after.resolve()}
    with tempfile.TemporaryDirectory(prefix='thor-slice-benchmark-') as temporary:
        directory=Path(temporary)
        generated=fixtures(directory)
        # Validate execution before timing, using checks that survive assert
        # elision. Benchmark projects are copies; original projects are not run.
        environment={**os.environ,'THOR_ROOT':str(ROOT)}
        environment.pop('THOR_PROFILE',None)
        for name,binary in engines.items():
            for project in generated:
                validation=directory/'validation'/name/project.name
                validation.mkdir(parents=True)
                command=[str(binary),'run',str(project),'--output-dir',str(validation/'output'),
                    '--cache-dir',str(validation/'cache'),'-out:'+str(validation/'program'),
                    '-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']
                result=subprocess.run(command,capture_output=True,text=True,timeout=120,
                    env=environment)
                if result.returncode:
                    raise RuntimeError(name+' synthetic execution failed:\n'+result.stdout+result.stderr)
        return project_benchmark(SimpleNamespace(project=args.project+generated,runs=args.runs,
            timeout=120,profile_dir=None,report=args.report),engines)


if __name__=='__main__':raise SystemExit(main())
