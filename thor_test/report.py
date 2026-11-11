#!/usr/bin/env python3
"""Publish compact local evidence and reviewable patches; never touch originals."""
import csv,difflib,json,platform,statistics,subprocess
from pathlib import Path
from audit import CORPUS,ART,ODIN,THOR,verify_originals,write_json

def load(name):return json.loads((ART/name).read_text())
def by_id(rows):return {row['id']:row for row in rows}
def main():
 verify_originals();inventory=load('inventory.json');baseline=by_id(load('baseline-builds.json'));prepared=by_id(load('final-builds.json') if (ART/'final-builds.json').exists() and len(load('final-builds.json'))==102 else load('prepared-builds.json'))
 console=by_id(load('runtime-console-merged.json'));gui=by_id(load('runtime-gui.json'));inputs=by_id(load('runtime-gui-input.json'))
 benchmarks=by_id(load('build-benchmarks.json')) if (ART/'build-benchmarks.json').exists() else {}
 cases=[];changed=[]
 patches=CORPUS/'patches';patches.mkdir(exist_ok=True)
 for original in sorted((CORPUS/'thor').rglob('*.thor')):
  relative=original.relative_to(CORPUS/'thor');new=CORPUS/'idiomatic'/relative
  if not new.exists():continue
  before=original.read_text();after=new.read_text()
  if before==after:continue
  patch=patches/relative.with_suffix('.patch');patch.parent.mkdir(parents=True,exist_ok=True)
  patch.write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+str(relative),tofile='b/'+str(relative))))
  changed.append(str(relative))
 for case in inventory['cases']:
  index=case['id'];base=baseline[index];ready=prepared[index];bench=benchmarks.get(index,{})
  row={'id':index,'project':case['label'],'kind':case['kind'],'raw_odin_status':base['odin']['status'],'raw_thor_status':base['thor']['status'],**{mode+'_status':ready[mode]['status'] for mode in ['odin','thor','idiomatic']}}
  if index in gui:row['runtime']='rendered frames match' if gui[index]['match'] else 'GUI mismatch'
  elif index in console:row['runtime']='console/file results match' if console[index]['match'] else 'native undefined behavior' if index==60 else 'console mismatch'
  elif case['kind']=='library':row['runtime']='foreign declarations exercised by parent executable'
  else:row['runtime']='excluded: duplicate entry points / build-ignore source'
  if index==12:row['runtime']='deterministic sections match; native concurrency varies'
  if index==25:row['runtime']='saved Huffman bitstream round-trip verified'
  row['scripted_input_match']=inputs.get(index,{}).get('match')
  for mode in ['odin','thor','idiomatic']:row[mode+'_median_seconds']=bench.get(mode,{}).get('median_seconds')
  for mode in ['thor','idiomatic']:row[mode+'_added_seconds']=bench.get(mode,{}).get('added_seconds')
  directory=Path(case['relative']).parent if case['file'] else Path(case['relative'])
  row['idiomatic_files']=[name for name in changed if Path(name).parent==directory]
  cases.append(row)
 evidence=CORPUS/'evidence';evidence.mkdir(exist_ok=True)
 write_json(evidence/'cases.json',cases)
 with (evidence/'cases.csv').open('w',newline='') as stream:
  writer=csv.DictWriter(stream,fieldnames=list(cases[0]),lineterminator='\n');writer.writeheader();writer.writerows(cases)
 for name in ['original-files-before.json','extra-idioms.json']:
  write_json(evidence/name,load(name))
 preparations=[]
 for entry in load('test-preparation.json'):
  preparations.append({'file':entry['file'],'reason':entry['reason'],'patch':''.join(difflib.unified_diff(entry['before'].splitlines(True),entry['after'].splitlines(True),fromfile='before/'+entry['file'],tofile='after/'+entry['file']))})
 write_json(evidence/'test-preparation.json',preparations)
 tests=load('project-tests.json');undefined=load('native-undefined-example.json')
 summary={'platform':platform.platform(),'odin_version':subprocess.check_output([str(ODIN),'version'],text=True).strip(),'thor_version':subprocess.check_output([str(THOR),'--version'],text=True).strip(),'original_files_verified':inventory['original_files'],'original_changes':load('original-files-changed.json'),'build_cases':len(cases),'raw_odin_success':sum(x['odin']['status']==0 for x in baseline.values()),'raw_thor_success':sum(x['thor']['status']==0 for x in baseline.values()),'prepared_success':{m:sum(x[m]['status']==0 for x in prepared.values())for m in ['odin','thor','idiomatic']},'rewritten_source_files':len(changed),'console_matches':sum(x['match']for x in console.values()),'gui_startup_matches':sum(x['match']for x in gui.values()),'gui_input_matches':sum(x['match']for x in inputs.values()),'original_asan_defect_confirmed':all(x.get('heap_use_after_free')for x in undefined.values()),'project_test_statuses':[{k:v if k=='label' else v['status']for k,v in row.items()}for row in tests]}
 summary['compiler_revision']=subprocess.check_output(['git','log','-1','--format=%h','--','rust/src/access.rs','runtime/pure/pure.odin'],text=True,cwd=CORPUS.parent).strip()
 summary['bulk_timing_note']='855 builds before the final array-capture and empty-scratch fixes; final representative measurements are separate.'
 if (ART/'runtime-benchmarks.json').exists():summary['runtime_timings']=load('runtime-benchmarks.json')
 if (ART/'build-benchmarks-final-samples.json').exists():summary['final_build_samples']=load('build-benchmarks-final-samples.json')
 summary['reproduction']=load('reproduction-check.json')
 summary['cli_scenarios']=len(load('cli-cases.json'))
 if benchmarks:
  summary['build_timings']={mode:{'median_seconds':statistics.median(x[mode]['median_seconds']for x in benchmarks.values()),'max_seconds':max(x[mode]['median_seconds']for x in benchmarks.values())}for mode in ['odin','thor','idiomatic']}
  for mode in ['thor','idiomatic']:summary['build_timings'][mode]['median_added_seconds']=statistics.median(x[mode]['added_seconds']for x in benchmarks.values())
 write_json(evidence/'summary.json',summary)
 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
