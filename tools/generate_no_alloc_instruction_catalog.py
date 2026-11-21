#!/usr/bin/env python3
"""Review SDK LLVM instruction leaves using resolved LLVM memory properties.

Run explicitly when reviewing an SDK/LLVM update; never during normal builds.
The compiler also verifies the complete SDK source manifest before using entries.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llvm-include',type=Path,required=True)
    parser.add_argument('--tblgen',type=Path,required=True)
    parser.add_argument('--odin-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'rust/data/noalloc-instructions.json')
    args=parser.parse_args()
    source=args.llvm_include/'llvm/IR/Intrinsics.td'
    with tempfile.TemporaryDirectory(prefix='thor-instruction-review-') as directory:
        packet=Path(directory)/'records.json'
        subprocess.run([str(args.tblgen),'-I',str(args.llvm_include),'--dump-json',str(source),'-o',str(packet)],check=True)
        records=json.loads(packet.read_text())
    definitions={}
    for name,value in records.items():
        if not isinstance(value,dict) or 'Intrinsic' not in value.get('!superclasses',[]):continue
        link=value.get('LLVMName') or 'llvm.'+name.removeprefix('int_').replace('_','.')
        definitions[link]=(name,sorted(p['def'] for p in value.get('IntrProperties',[]) if 'def' in p))
    entries={}
    for path in sorted(p for directory in ['base','core'] for p in (args.odin_root/directory).rglob('*.odin')):
        if path.stem.endswith('_test'):continue
        for link in re.findall(r'"(llvm\.[^"\s]+)"',path.read_text()):
            # Resolve the longest operation name, including UNSAFE definitions,
            # before checking properties. A typed suffix must not hide a more
            # specific memory-affecting intrinsic behind a safe prefix.
            operation=link
            while operation not in definitions and '.' in operation:operation=operation.rsplit('.',1)[0]
            if operation not in definitions:continue
            record,properties=definitions[operation]
            # Memory attributes alone are not a no-allocation contract for TLS,
            # coroutines, managed runtimes or instrumentation. Restrict the
            # catalog to direct register/vector operations and fixed native
            # math/memory primitives whose lowering has no such runtime.
            scalar={'acos','asin','atan','atan2','cos','sin','tan','sinh','cosh','tanh',
                    'exp','exp2','exp10','log','log2','log10','pow','powi','sqrt','fma','fmuladd','fabs','copysign',
                    'minnum','maxnum','minimum','maximum','floor','ceil','round','roundeven','trunc','rint','nearbyint',
                    'canonicalize','ctpop','ctlz','cttz','bswap','bitreverse','abs','smax','smin','umax','umin','fshl','fshr',
                    'memcpy','memmove','memset','ptrmask','prefetch'}
            fixed=operation.removeprefix('llvm.')
            cpu=operation.startswith(('llvm.aarch64.','llvm.arm.','llvm.x86.')) and '.sme.' not in operation
            if not (cpu or fixed in scalar or fixed.startswith(('vector.reduce.','memcpy.','memmove.','memset.',
                    'sadd.with.overflow','ssub.with.overflow','smul.with.overflow','uadd.with.overflow','usub.with.overflow','umul.with.overflow'))):continue
            # Arbitrary CPU/system-register writes can change the environment
            # used by later calls. Pure instruction results do not do that.
            if 'IntrHasSideEffects' in properties:continue
            if not ('IntrNoMem' in properties or 'IntrReadMem' in properties or 'IntrArgMemOnly' in properties):continue
            entry=entries.setdefault(link,{'record':record,'properties':properties,'sources':[]})
            entry['sources'].append(str(path.relative_to(args.odin_root)))
    data={'schema':1,'llvm':subprocess.check_output([str(args.tblgen),'--version'],text=True).strip(),
          'intrinsics_td_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
          'scope':'Exact SDK LLVM links with no memory writes or argument-memory-only effects; ordinary bodies and call arguments remain checked.',
          'entries':dict(sorted(entries.items()))}
    args.output.write_text(json.dumps(data,indent=2)+'\n')
    print('Reviewed instruction links:',len(entries))


if __name__=='__main__':main()
