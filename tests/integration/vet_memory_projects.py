"""Read-only vet audit of every pinned idiomatic upstream test/build recipe.

Uses run_odin_projects' baseline matching, SDK selection, process-tree memory
limits and project locks. It neither reclones sources nor applies corrections.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_odin_projects as runner

def source_digest(tree):
    digest=hashlib.sha256()
    for path in sorted(tree.rglob('*.thor')):
        relative=path.relative_to(tree)
        if any(part in('.git','.thor-build','.thor-cache','.thor-rust-build')for part in relative.parts):continue
        digest.update(str(relative).encode());digest.update(b'\0');digest.update(path.read_bytes());digest.update(b'\0')
    return digest.hexdigest()

def audit(project,args):
    slug=project['repo'].replace('/','__')
    source=runner.WORK/'odin'/slug
    tree=runner.WORK/'vetted'/slug
    original=runner.WORK/'results'/slug
    directory=args.results/slug
    directory.mkdir(parents=True,exist_ok=True)
    report_path=directory/'report.json'
    baseline=json.loads((original/'report.json').read_text())
    env={**os.environ,'THOR_FRONTEND_ODIN':args.odin}
    env.setdefault('THOR_INTEGRATION_JOBS','1')
    env.update({k:v.format(source=str(source),work=str(runner.WORK),root=str(runner.ROOT))for k,v in project.get('environment',{}).items()})
    native=project.get('odin',args.odin).format(work=str(runner.WORK),root=str(runner.ROOT))
    native=str(Path(shutil.which(native)or native).resolve())
    env['PATH']=str(Path(native).parent)+os.pathsep+env['PATH']
    values={'tree':str(tree),'source':str(source),'work':str(runner.WORK),'root':str(runner.ROOT)}
    with runner.project_lock(original):
        configuration=hashlib.sha256(json.dumps({'project':project,'checks':args.checks,'frontend':args.odin,'source_sha256':source_digest(tree)},sort_keys=True).encode()).hexdigest()
        previous=json.loads(report_path.read_text()) if args.resume and report_path.exists() else {}
        report=previous if previous.get('compiler_sha256')==args.compiler_sha256 and previous.get('configuration_sha256')==configuration else {
            'repo':project['repo'],'commit':project['commit'],'compiler_sha256':args.compiler_sha256,'configuration_sha256':configuration,'recipes':[]}
        for i,recipe in enumerate(project.get('vet',[])):
            if any(r['index']==i and r.get('status')==0 for r in report['recipes']):continue
            tokens=[v.format(**values)for v in recipe]
            matching=runner.vet_baselines(tokens,project['commands'],values)
            if matching and not any(runner.usable_baseline(next((b for b in baseline.get('baseline',[])if b['name']==c['name']),None),c)for c in matching):
                result={'index':i,'entry':tokens[0],'status':'baseline-unavailable'}
            else:
                profile=directory/f'{i}-profile.json'
                command=[str(args.thor),'vet',*tokens,'--odin-compatible','--odin',native,
                    '--comptime=required','-json-errors','--output-dir',str(directory/'generated')]
                if not any(t.split(':',1)[0]=='-thread-count'for t in tokens):command+=['-thread-count:2']
                if args.checks:command+=['--vet='+args.checks]
                result={'index':i,'entry':tokens[0],'test_mode':'-build-mode:test'in tokens,
                    **runner.execute(command,tree,directory/f'vet-{i}',{**env,'THOR_PROFILE':str(profile)},args.timeout)}
                stderr=Path(result['stderr']).read_text(errors='replace')
                try:result['diagnostics']=json.loads(stderr).get('errors',[]) if stderr.strip() else []
                except ValueError:result['parse_error']=stderr[-4000:]
                if profile.exists():result['profile']=json.loads(profile.read_text())
            report['recipes']=[r for r in report['recipes']if r['index']!=i]+[result]
            report_path.write_text(json.dumps(report,indent=2)+'\n')
            print(slug,i,result['status'],len(result.get('diagnostics',[])),result.get('seconds',0),flush=True)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--thor',type=Path,default=runner.ROOT/'rust/target/release/thor-rust')
    p.add_argument('--odin',default=shutil.which('odin'))
    p.add_argument('--results',type=Path,required=True)
    p.add_argument('--projects',default='.*')
    p.add_argument('--checks',default='',help='Default: every Thor vet check')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--timeout',type=int,default=900)
    args=p.parse_args();args.thor=args.thor.resolve();args.results=args.results.resolve();args.results.mkdir(parents=True,exist_ok=True)
    args.compiler_sha256=hashlib.sha256(args.thor.read_bytes()).hexdigest()
    reports=[]
    for project in json.loads(runner.MANIFEST.read_text()):
        if re.search(args.projects,project['repo']):
            reports.append(audit(project,args))
            (args.results/'audit.json').write_text(json.dumps({'reports':reports},indent=2)+'\n')
    bad=[(p['repo'],r['index'],r['status'])for p in reports for r in p['recipes']if r['status']not in(0,'baseline-unavailable')]
    print('Completed',len(reports),'projects;',len(bad),'command failures',flush=True)
    if bad:raise SystemExit(1)
if __name__=='__main__':main()
