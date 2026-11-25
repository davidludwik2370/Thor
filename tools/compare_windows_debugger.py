#!/usr/bin/env python3
"""Build nine matching Odin/Thor controls and retain their native DAP evidence.

Uses a source-mapped Thor session, a plain-Odin direct CodeLLDB session and the
same plain-Odin executable through the identity bridge. Known native mismatches
remain mismatches in JSON; setup/transport errors and Thor-only failures fail.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import platform
import subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.debugger_pairs import CASES,FIXTURES,Probe,prepare,fixture_source,compare_sessions
from tests.test_debugger import backend_path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--compiler',type=Path,default=os.environ.get('THOR_TEST_COMPILER',ROOT/'rust/target/release'/('thor-rust.exe' if os.name=='nt' else 'thor-rust')))
    parser.add_argument('--odin',default=os.environ.get('THOR_ODIN',shutil.which('odin')))
    parser.add_argument('--case',action='append',choices=sorted(CASES))
    parser.add_argument('--repeat',type=int,default=1)
    parser.add_argument('--skip-unchecked',action='store_true')
    args=parser.parse_args()
    if args.repeat<1:parser.error('repeat must be positive')
    args.output=args.output.resolve()
    args.compiler=args.compiler.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    backend=backend_path()
    if backend is None:parser.error('CodeLLDB is required')
    executable=backend/'adapter'/('codelldb.exe' if os.name=='nt' else 'codelldb')
    library=backend/'lldb'/('bin/liblldb.dll' if os.name=='nt' else 'lib/liblldb.dylib' if sys.platform=='darwin' else 'lib/liblldb.so')
    packet={'platform':platform.platform(),'odin_version':subprocess.check_output([str(args.odin),'version'],text=True).strip(),
        'backend_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'lldb_sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
        'cases':[],'compiler_sha256':hashlib.sha256(args.compiler.read_bytes()).hexdigest(),
        'backend':str(backend_path()),'cases_sha256':hashlib.sha256((FIXTURES/'cases.json').read_bytes()).hexdigest(),
        'bridge_sha256':hashlib.sha256((ROOT/'editors/vscode/debug-adapter.js').read_bytes()).hexdigest(),'complete':False}
    def save():
        (args.output/'results.json').write_text(json.dumps(packet,indent=2)+'\n',encoding='utf8')
    save()
    for name in args.case or CASES:
        if (FIXTURES/name/'main.thor').read_text()!=fixture_source(name):raise RuntimeError('Live regression fixture drift: '+name)
        row={'case':name,'regression':CASES[name],'sessions':[]};packet['cases'].append(row);save()
        built=prepare(name,args.output/name/'checked',args.compiler,args.odin,['-debug','-o:none','-thread-count:1'])
        row['builds']=built;save()
        for repeat in range(args.repeat):
            for language,mode in [('thor','thor'),('odin','native'),('odin','identity')]:
                record={'language':language,'mode':mode,'repeat':repeat};row['sessions'].append(record)
                probe=Probe(name,built[language],mode)
                try:record.update(probe.run())
                except Exception as error:record.update(error=repr(error),checks=probe.checks,stops=probe.stops,assembly=probe.assembly,completed=False)
                trace=args.output/name/(f'{language}-{mode}-{repeat}.json')
                trace.write_text(json.dumps(probe.trace,indent=2)+'\n',encoding='utf8');record['protocol']=str(trace);save()
                print(name,language,mode,'failed checks:',[x['key'] for x in record['checks'] if not x['matched']],record.get('error',''),flush=True)
        if not args.skip_unchecked:
            row['unchecked']=prepare(name,args.output/name/'unchecked',args.compiler,args.odin,
                ['-debug','-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','-thread-count:1']);save()
    for row in packet['cases']:row['comparison']=compare_sessions(row['sessions'])
    packet['complete']=True;save()
    errors=[(r['case'],s.get('error')) for r in packet['cases'] for s in r['sessions'] if not s['completed']]
    print('Transport/setup errors:',errors,flush=True)
    differences=[(row['case'],row['comparison']) for row in packet['cases'] if row['comparison']['thor_only'] or row['comparison']['identity_only']]
    print('Thor-specific differences:',differences,flush=True)
    return bool(errors or differences)


if __name__=='__main__':raise SystemExit(main())
