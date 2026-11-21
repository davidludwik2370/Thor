#!/usr/bin/env python3
"""Reproduce native SDK acceptance separately from Thor allocation proofs."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',type=Path,default=ROOT/'rust/target/release/thor-rust')
    parser.add_argument('--cases',type=Path,default=ROOT/'tests/fixtures/no_alloc_sdk_cases.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--match',default='')
    args=parser.parse_args()
    report=[]
    with tempfile.TemporaryDirectory(prefix='thor-sdk-allocation-audit-') as temporary:
        root=Path(temporary)
        for case in json.loads(args.cases.read_text()):
            if args.match and not re.search(args.match,case['name']):continue
            if case.get('platforms') and sys.platform not in case['platforms']:
                print(case['name'],'not applicable on '+sys.platform,flush=True)
                continue
            imports='\n'.join('import '+json.dumps('core:'+p) for p in case.get('imports',[]))
            text='package main\n'+imports+'\n'+case.get('declarations','')+'\nMARKER\nmain::proc(){'+case['body']+'}\n'
            source=root/'main.odin';source.write_text(text.replace('MARKER',''))
            baseline=subprocess.run([os.environ.get('THOR_ODIN','odin'),'check',str(source),'-file','-thread-count:1',*case.get('flags',[])],capture_output=True,text=True,timeout=60)
            source=root/'main.thor';source.write_text(text.replace('MARKER','@(no_alloc)'))
            started=time.perf_counter()
            result=subprocess.run([str(args.compiler.resolve()),'check',str(source),'--output-dir',str(root/'output'),'--cache-dir',str(root/'cache'),'-thread-count:1','-json-errors',*case.get('flags',[])],cwd=ROOT,capture_output=True,text=True,timeout=60)
            item={**case,'native_status':baseline.returncode,'status':result.returncode,'seconds':time.perf_counter()-started,
                  'native_diagnostic':baseline.stderr,'diagnostic':result.stderr}
            report.append(item)
            if result.returncode:
                try:
                    errors=json.loads(result.stderr)['errors'];error=errors[0]
                    pos=error.get('related',[{}])[-1].get('pos') or error.get('pos')
                    if pos:
                        item['operation']={**pos,'source_line':Path(pos['file']).read_text().splitlines()[pos['line']-1]}
                except (ValueError,KeyError,IndexError,OSError):pass
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2)+'\n')
            print(case['name'],'native='+str(baseline.returncode),'contract='+str(result.returncode),flush=True)
    unexpected=[c['name'] for c in report if c['native_status'] or bool(c['status'])!=(c.get('expect','accept')=='reject')]
    print('Unexpected:',json.dumps(unexpected),flush=True)
    return bool(unexpected)


if __name__=='__main__':raise SystemExit(main())
