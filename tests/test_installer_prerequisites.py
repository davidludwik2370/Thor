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
    def test_apt_acquisition_waits_for_the_frontend_lock(self):
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
if 'install' in sys.argv:pathlib.Path(os.environ['FIXTURE_READY']).write_text('installed')
''')
            log=directory/'apt.jsonl'
            env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'FIXTURE_LOG':str(log),'FIXTURE_READY':str(directory/'ready')}
            result=subprocess.run(['sh',str(ROOT/'install/prerequisites.sh')],env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            commands=[json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(commands),2)
            self.assertIn('update',commands[0]);self.assertIn('install',commands[1])
            self.assertTrue(all('DPkg::Lock::Timeout=300' in args for args in commands))
            self.assertIn('native C compiler and linker',result.stdout)
