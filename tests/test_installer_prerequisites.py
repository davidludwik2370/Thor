"""Exercise prerequisite acquisition with all privileged commands replaced."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
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

    def exercise_macos(self, shell, *, missing=(), llvm='22.1.8', brew_off_path=False, no_brew=False, reuse_odin=False):
        with tempfile.TemporaryDirectory(prefix='thor-macos-prerequisites-') as temp:
            directory=Path(temp);bin=directory/'bin';bin.mkdir()
            home=directory/'home';home.mkdir()
            def executable(name, text):
                path=bin/name;path.write_text(text);path.chmod(0o755)
            for name in ('mktemp','rm'):
                (bin/name).symlink_to(shutil.which(name))
            executable('uname','#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo arm64;; esac\n')
            executable('xcrun','#!/bin/sh\nexit 0\n')
            executable('curl','#!/bin/sh\necho "Unexpected download" >&2; exit 99\n')
            executable('python3','#!/bin/sh\nexit 0\n')
            executable('cargo','#!/bin/sh\nexit 0\n')
            executable('llvm-config',f'#!/bin/sh\nprintf "%s\\n" "{llvm}"\n')
            for name in ('node','npm','git'):
                if name not in missing:executable(name,'#!/bin/sh\nexit 0\n')
            compiler=f'''#!{sys.executable}
import pathlib,sys
p=pathlib.Path(sys.argv[sys.argv.index('-o')+1])
p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
'''
            executable('clang',compiler);executable('clang++',compiler)
            executable('brew',f'''#!{sys.executable}
import json,os,pathlib,sys
with open(os.environ['FIXTURE_LOG'],'a') as output:
 output.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:]==['install','node']:
 for name in ('node','npm'):
  p=pathlib.Path(__file__).parent/name;p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
elif sys.argv[1:]==['install','git']:
 p=pathlib.Path(__file__).parent/'git';p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
elif sys.argv[1:]==['install','llvm@22']:pass
else:sys.exit('Unexpected Homebrew invocation: '+repr(sys.argv[1:]))
''')
            if brew_off_path:
                destination=directory/'system/opt/homebrew/bin/brew'
                destination.parent.mkdir(parents=True);(bin/'brew').rename(destination)
            if no_brew:(bin/'brew').unlink()
            # Relocate only fixed system search roots into the fixture, so an
            # installed host Node/LLVM cannot hide a missing-tool branch. The
            # prerequisite logic and Apple /bin/sh execution remain unchanged.
            text=(ROOT/'install/prerequisites.sh').read_text()
            for prefix in ('/usr/local','/usr/pkg','/opt/homebrew'):
                text=text.replace(prefix,str(directory/'system'/prefix.lstrip('/')))
            script=directory/'prerequisites.sh';script.write_text(text)
            log=directory/'brew.jsonl'
            options=['--source',*(['--reuse-odin'] if reuse_odin else [])]
            result=subprocess.run([shell,str(script),*options],capture_output=True,text=True,timeout=15,
                                  env={**os.environ,'PATH':str(bin),'HOME':str(home),'CI':'true','FIXTURE_LOG':str(log)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS: source-build prerequisites',result.stdout)
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_macos_missing_node_npm_and_git_do_not_trigger_homebrew(self):
        # On macOS these are the real Apple Bash 3.2 shells. Merely using -n
        # misses the command-substitution bug; execute the missing-tool paths.
        for shell in ('/bin/sh','/bin/bash'):
            if not Path(shell).is_file():continue
            for missing in [('node','npm','git'),('npm',),('node','npm'),()]:
                with self.subTest(shell=shell,missing=missing):
                    commands=self.exercise_macos(shell,missing=missing,reuse_odin='git' in missing)
                    self.assertEqual(commands,[])

    def test_macos_unsupported_installed_llvm_does_not_trigger_homebrew(self):
        for llvm in ('16.0.0','23.1.0','invalid'):
            with self.subTest(llvm=llvm):
                commands=self.exercise_macos('/bin/sh',llvm=llvm)
                self.assertEqual(commands,[])

    def test_macos_supported_llvm_is_reused(self):
        for llvm in ('17.0.6','18.1.8','22.1.8'):
            with self.subTest(llvm=llvm):
                commands=self.exercise_macos('/bin/sh',llvm=llvm)
                self.assertEqual(commands,[])

    def test_macos_existing_homebrew_outside_login_path_is_reused(self):
        commands=self.exercise_macos('/bin/sh',brew_off_path=True)
        self.assertEqual(commands,[])

    def test_macos_does_not_bootstrap_missing_homebrew(self):
        commands=self.exercise_macos('/bin/sh',missing=('node','npm'),no_brew=True)
        self.assertEqual(commands,[])
