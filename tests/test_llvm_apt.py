import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipIf(os.name=='nt','POSIX package acquisition')
class LLVMPrerequisiteTests(unittest.TestCase):
    def exercise(self, *, available=False, bad_key=False, existing=False, conflict=False, unsupported=False, package_failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);work=root/'work';work.mkdir();bin=root/'bin';bin.mkdir();repo=root/'repos';repo.mkdir();keys=root/'keys';keys.mkdir()
            os_release=root/'os-release';os_release.write_text('ID=ubuntu\nVERSION_CODENAME='+('unknown' if unsupported else 'jammy')+'\n')
            log=root/'calls'
            def script(name,body):
                p=bin/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
            script('apt-cache','echo "  Candidate: '+('18.1.8' if available else '(none)')+'"')
            fingerprint='WRONG' if bad_key else '6084F3CF814B57C1CF12EFD515CF4D18AF4F7421'
            script('gpg','printf "fpr:::::::::'+fingerprint+':\\n"')
            if existing or conflict:
                (keys/'thor-llvm-18.asc').write_text('key')
                (repo/'thor-llvm-18.list').write_text('changed' if conflict else f'deb [signed-by={keys}/thor-llvm-18.asc] https://apt.llvm.org/jammy/ llvm-toolchain-jammy-18 main\n')
            code='''set -eu
work=$1; log=$2
packages() { printf 'packages %s\\n' "$*" >> "$log"; '''+('return 37;' if package_failure else ':;')+''' }
download() { printf '%s\\n' "$1" >> "$log"; printf key > "$2"; }
elevate() { "$@"; }
. "$3"
install_apt_llvm "$4" "$5" "$6"
'''
            result=subprocess.run(['/bin/sh','-c',code,'fixture',str(work),str(log),str(ROOT/'install/llvm-apt.sh'),str(os_release),str(repo),str(keys)],env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH']},text=True,capture_output=True,timeout=10)
            calls=log.read_text() if log.exists() else ''
            files={str(p.relative_to(root)):p.read_bytes() for d in [repo,keys] for p in d.iterdir()}
            return result,calls,files

    def test_distribution_package_is_preferred_without_repository_changes(self):
        p,calls,files=self.exercise(available=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(calls,'packages llvm-18-dev\n');self.assertEqual(files,{})

    def test_verified_scoped_repository_on_old_distribution(self):
        p,calls,files=self.exercise()
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('packages llvm-18-dev',calls);self.assertIn('https://apt.llvm.org/llvm-snapshot.gpg.key',calls)
        self.assertIn('repos/thor-llvm-18.list',files);self.assertIn(b'signed-by=',files['repos/thor-llvm-18.list'])

    def test_existing_verified_repository_is_reused(self):
        p,_,files=self.exercise(existing=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(len(files),2)

    def test_bad_signing_key_does_not_change_repository(self):
        p,_,files=self.exercise(bad_key=True)
        self.assertNotEqual(p.returncode,0);self.assertIn('fingerprint',p.stderr);self.assertEqual(files,{})

    def test_conflicting_configuration_is_preserved(self):
        p,calls,files=self.exercise(conflict=True)
        self.assertNotEqual(p.returncode,0);self.assertEqual(files['repos/thor-llvm-18.list'],b'changed');self.assertNotIn('packages llvm-18-dev',calls)

    def test_unknown_distribution_is_not_guessed(self):
        p,calls,files=self.exercise(unsupported=True)
        self.assertNotEqual(p.returncode,0);self.assertEqual(calls,'');self.assertEqual(files,{})

    def test_failed_package_install_stops_before_key_download(self):
        p,calls,files=self.exercise(package_failure=True)
        self.assertEqual(p.returncode,37);self.assertNotIn('https:',calls);self.assertEqual(files,{})
