#!/usr/bin/env python3
"""Compare owned Thor f-strings with equivalent Odin aprintf, without terminal I/O in timed loops."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=r'''package main
import "core:fmt"
import "core:mem"
import "core:time"
LONG :: "@LONG@"
thor_int::proc(i:int)->string{return f"value {i}"}
odin_int::proc(i:int)->string{return fmt.aprintf("value %d",i)}
thor_mixed::proc(i:int)->string{return f"{i:08d}|{f64(i)/7:.2f}"}
odin_mixed::proc(i:int)->string{return fmt.aprintf("%08d|%.2f",i,f64(i)/7)}
thor_fields::proc(i:int)->string{return f"{i}|{i+1}|{i+2}|{i+3}"}
odin_fields::proc(i:int)->string{return fmt.aprintf("%d|%d|%d|%d",i,i+1,i+2,i+3)}
thor_large::proc(i:int)->string{return f"prefix {LONG} {i}"}
odin_large::proc(i:int)->string{return fmt.aprintf("prefix %s %d",LONG,i)}
Case::struct{name:string,call:proc(int)->string}
main::proc(){
 cases:=[?]Case{{"integer.thor",thor_int},{"integer.odin",odin_int},{"mixed.thor",thor_mixed},{"mixed.odin",odin_mixed},
                {"fields.thor",thor_fields},{"fields.odin",odin_fields},{"large.thor",thor_large},{"large.odin",odin_large}}
 for index in 0..<len(cases)/2 {
  for i in [0,1,17,1234] {
   a:=cases[2*index].call(i);b:=cases[2*index+1].call(i)
   assert(a==b);delete(a);delete(b)
  }
 }
 for c in cases {
  checksum:=0
  for i in 0..<1000{s:=c.call(i);checksum+=len(s);delete(s)}
  for sample in 0..<@SAMPLES@ {
   start:=time.tick_now()
   for i in 0..<@ITERATIONS@ {s:=c.call(i);checksum+=len(s)+int(s[0])+int(s[len(s)-1]);delete(s)}
   ns:=f64(time.tick_since(start))/f64(@ITERATIONS@)
   fmt.printf("time\t%s\t%.3f\t%d\n",c.name,ns,checksum)
  }
  backing:=context.allocator
  tracker:mem.Tracking_Allocator;mem.tracking_allocator_init(&tracker,backing)
  context.allocator=mem.tracking_allocator(&tracker)
  for i in 0..<100 {s:=c.call(i);checksum+=len(s);delete(s)}
  context.allocator=backing
  assert(tracker.current_memory_allocated==0)
  fmt.printf("alloc\t%s\t%d\t%d\n",c.name,tracker.total_allocation_count,tracker.total_free_count)
  mem.tracking_allocator_destroy(&tracker)
 }
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations',type=int,default=20000)
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--save',type=Path)
    parser.add_argument('--thor',type=Path,default=ROOT/'thor',help='compiler executable, including an older checkout for comparison')
    options=parser.parse_args()
    if options.iterations<1 or options.samples<1:parser.error('counts must be positive')
    source=SOURCE.replace('@LONG@','x'*4096).replace('@ITERATIONS@',str(options.iterations)).replace('@SAMPLES@',str(options.samples))
    with tempfile.TemporaryDirectory(prefix='thor-format-bench-') as temporary:
        folder=Path(temporary);entry=folder/'main.thor';entry.write_text(source)
        program=folder/'benchmark'
        subprocess.run([str(options.thor.resolve()),'build',str(entry),'-o:speed',f'-out:{program}','--output-dir',str(folder/'generated')],check=True,cwd=folder)
        output=subprocess.check_output([str(program)],text=True)
    values={}
    for line in output.splitlines():
        kind,name,value,*rest=line.split('\t')
        case=values.setdefault(name,{})
        if kind=='time':case.setdefault('samples_ns',[]).append(float(value))
        else:case['allocations_per_call']=int(value)/100;case['frees_per_call']=int(rest[0])/100
    for case in values.values():case['median_ns']=statistics.median(case['samples_ns'])
    version=subprocess.check_output([str(options.thor.resolve()),'--version'],text=True).strip()
    report={'compiler':version,'optimization':'-o:speed','iterations':options.iterations,'samples':options.samples,'cases':values}
    result=json.dumps(report,indent=2)
    if options.save:options.save.write_text(result+'\n')
    print(result)


if __name__=='__main__':main()
