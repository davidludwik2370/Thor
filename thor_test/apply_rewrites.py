#!/usr/bin/env python3
"""Apply the reviewed final rewrites to a prepared Thor copy, without fuzz."""
import argparse,subprocess
from pathlib import Path
from audit import CORPUS,ART
p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,default=CORPUS/'idiomatic');options=p.parse_args()
destination=options.destination.resolve()
if destination!=(CORPUS/'idiomatic').resolve() and not destination.is_relative_to(ART.resolve()):
 raise SystemExit('Rewrites are restricted to the idiomatic copy or its artifact verification copies.')
patches=sorted((CORPUS/'patches').rglob('*.patch'))
for dry_run in [True,False]:
 for patch in patches:
  command=['patch','--batch','--forward','--fuzz=0','-p1','-d',str(destination),'-i',str(patch)]
  if dry_run:command.append('--dry-run')
  result=subprocess.run(command,capture_output=True,text=True)
  if result.returncode:raise SystemExit(result.stdout+result.stderr)
print('Applied',len(patches),'reviewed source patches to',destination)
