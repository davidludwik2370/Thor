"""Exercise prerequisite acquisition with all privileged commands replaced."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipIf(os.name=='nt','POSIX prerequisite script')
class PrerequisiteTests(unittest.TestCase):
    def exercise_apt(self,failure=None):
        with tempfile.TemporaryDirectory(prefix='thor-prerequisite-test-') as temp:
            directory=Path(temp);bin=directory/'bin';bin.mkdir()
            def executable(name,text):
                path=bin/name;path.write_text(text);path.chmod(0o755)
            executable('uname','#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo aarch64;; esac\n')
            executable('id','#!/bin/sh\necho 0\n')
            executable('curl','#!/bin/sh\nexit 0\n')
            executable('clang','''#!/usr/bin/env python3
import os,pathlib,sys
if not pathlib.Path(os.environ['FIXTURE_READY']).exists():sys.exit(1)
p=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
''')
            executable('apt-get','''#!/usr/bin/env python3
import json,os,pathlib,sys
with open(os.environ['FIXTURE_LOG'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if 'DPkg::Lock::Timeout=300' not in sys.argv:sys.exit(100)
marker=pathlib.Path(os.environ['FIXTURE_READY']+'.updated')
if 'update' in sys.argv and not marker.exists() and os.environ.get('FIXTURE_FAILURE'):
 marker.write_text('attempted')
 print('E: Could not get lock /var/lib/apt/lists/lock' if os.environ['FIXTURE_FAILURE']=='lock' else 'E: repository failure',file=sys.stderr);sys.exit(100)
if 'install' in sys.argv:pathlib.Path(os.environ['FIXTURE_READY']).write_text('installed')
''')
            log=directory/'apt.jsonl'
            env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'FIXTURE_LOG':str(log),'FIXTURE_READY':str(directory/'ready'),'FIXTURE_FAILURE':failure or ''}
            result=subprocess.run(['sh',str(ROOT/'install/prerequisites.sh')],env=env,text=True,capture_output=True,timeout=15)
            if failure=='network':
                self.assertEqual(result.returncode,100,result.stdout+result.stderr)
                self.assertEqual(len(log.read_text().splitlines()),1)
                return
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            commands=[json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(commands),3 if failure=='lock' else 2)
            self.assertIn('update',commands[0]);self.assertIn('install',commands[-1])
            self.assertTrue(all('DPkg::Lock::Timeout=300' in args for args in commands))
            self.assertIn('native C compiler and linker',result.stdout)

    def test_apt_acquisition_waits_for_the_frontend_lock(self):
        self.exercise_apt()

    def test_package_list_lock_retries_before_installing(self):
        self.exercise_apt('lock')

    def test_other_package_failures_are_not_retried(self):
        self.exercise_apt('network')
