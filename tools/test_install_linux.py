#!/usr/bin/env python3
"""Run a real clean Linux install/editor/uninstall check in one bounded container."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',required=True,help='distribution image, e.g. ubuntu:22.04 or debian:12')
    p.add_argument('--bundle',type=Path,help='candidate bundle; omit to download the public release through GitHub')
    p.add_argument('--output',type=Path,required=True)
    o=p.parse_args();o.output=o.output.resolve();o.output.mkdir(parents=True,exist_ok=True)
    if o.bundle:
        o.bundle=o.bundle.resolve(strict=True)
        if not o.bundle.with_name(o.bundle.name+'.sha256').is_file():raise RuntimeError('Missing adjacent bundle checksum')
    name='thor-compat-'+uuid.uuid4().hex[:12];created=False;start=time.monotonic();result={'image':o.image,'success':False}
    previous=subprocess.run(['docker','image','inspect',o.image],capture_output=True).returncode==0
    def run(args,label,timeout=900):
        with (o.output/(label+'.log')).open('wb') as log:
            r=subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        if r.returncode:raise RuntimeError(label+' failed; see '+str(o.output/(label+'.log')))
    try:
        args=['docker','run','-d','--name',name,'--label','org.thor.validation=installer-compatibility',
              '--memory','4g','--memory-swap','4g','--cpus','2','--shm-size','512m','-v',str(ROOT)+':/source:ro']
        if o.bundle:args+=['-v',str(o.bundle.parent)+':/artifacts:ro']
        run([*args,o.image,'sleep','infinity'],'container');created=True
        result['image_id']=subprocess.check_output(['docker','inspect','--format','{{.Image}}',name],text=True).strip()
        run(['docker','exec',name,'sh','/source/tools/install-linux/prepare.sh'],'prepare')
        run(['docker','exec','-d','--user','thor','-e','HOME=/home/thor',name,'Xvfb',':99','-screen','0','1280x800x24'],'display')
        env=['-e','HOME=/home/thor','-e','DISPLAY=:99','-e','SHELL=/bin/bash']
        if o.bundle:env+=['-e','THOR_INSTALL_BUNDLE=/artifacts/'+o.bundle.name]
        run(['docker','exec','--user','thor',*env,name,'sh','/source/tools/install-linux/validate.sh'],'validation',timeout=1200)
        result['success']=True
    except (OSError,subprocess.SubprocessError,RuntimeError) as e:
        result['error']=str(e)
    finally:
        if not created:
            probe=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "org.thor.validation"}}',name],capture_output=True,text=True)
            created=probe.returncode==0 and probe.stdout.strip()=='installer-compatibility'
        if created:
            copied=subprocess.run(['docker','cp',name+':/home/thor/logs/.',str(o.output)],capture_output=True,text=True)
            if copied.returncode:result['log_copy_error']=copied.stderr
            removed=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True)
            if removed.returncode:result['cleanup_error']=removed.stderr
        if not previous and result.get('image_id'):
            users=subprocess.run(['docker','ps','-aq','--filter','ancestor='+result['image_id']],capture_output=True,text=True)
            if users.returncode==0 and not users.stdout.strip():
                cleaned=subprocess.run(['docker','image','rm',o.image],capture_output=True,text=True)
                result['temporary_image_removed']=cleaned.returncode==0
        result['seconds']=round(time.monotonic()-start,2)
        (o.output/'host-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['success'] and not result.get('cleanup_error') else 1

if __name__=='__main__':raise SystemExit(main())
