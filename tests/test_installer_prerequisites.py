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
    def exercise_linux_editor_ssl(self, present=False, editor=True):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bin=root/'bin';bin.mkdir()
            def executable(name,text):
                p=bin/name;p.write_text(text);p.chmod(0o755)
            for name in ('mktemp','rm','dirname','cat','awk','head'):
                (bin/name).symlink_to(shutil.which(name))
            executable('uname','#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo aarch64;; esac\n')
            executable('id','#!/bin/sh\necho 0\n');executable('curl','#!/bin/sh\nexit 0\n')
            executable('clang',f'''#!{sys.executable}
import pathlib,sys
p=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
''')
            executable('code','#!/bin/sh\necho 1.148.0\n')
            executable('ldconfig','#!/bin/sh\necho "libssl.so.4 (libc6,AArch64) => /lib64/libssl.so.4"\nif [ -f "$FIXTURE_SSL_READY" ]; then echo "libssl.so.3 (libc6,AArch64) => /lib64/libssl.so.3"; fi\n')
            executable('dnf',f'''#!{sys.executable}
import json,os,pathlib,sys
with open(os.environ['FIXTURE_LOG'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
pathlib.Path(os.environ['FIXTURE_SSL_READY']).write_text('installed')
''')
            for name in ('vscode-minimum','vscode-macos-version'):shutil.copy2(ROOT/'install'/name,root/name)
            script=root/'prerequisites.sh';script.write_text((ROOT/'install/prerequisites.sh').read_text())
            ready=root/'ssl-ready';log=root/'dnf.jsonl'
            if present:ready.write_text('already installed')
            value=subprocess.run(['/bin/sh',str(script),*(['--vscode'] if editor else [])],capture_output=True,text=True,timeout=20,
                env={**os.environ,'PATH':str(bin),'HOME':str(root),'FIXTURE_SSL_READY':str(ready),'FIXTURE_LOG':str(log)})
            calls=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return value,calls

    def test_missing_ssl3_is_installed_even_when_the_editor_is_already_present(self):
        value,calls=self.exercise_linux_editor_ssl()
        self.assertEqual(value.returncode,0,value.stdout+value.stderr)
        self.assertEqual(calls,[['install','-y','libssl.so.3()(64bit)']])
        self.assertIn('signature-verification runtime',value.stdout)

    def test_existing_ssl3_is_reused_without_package_changes(self):
        value,calls=self.exercise_linux_editor_ssl(present=True)
        self.assertEqual(value.returncode,0,value.stdout+value.stderr);self.assertEqual(calls,[])

    def test_toolchain_only_install_does_not_add_editor_crypto_dependencies(self):
        value,calls=self.exercise_linux_editor_ssl(editor=False)
        self.assertEqual(value.returncode,0,value.stdout+value.stderr);self.assertEqual(calls,[])

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

    def exercise_macos_editor(self, *, installed='1.66.0', running=False, system='13.5.1', app_minimum='12.0', signature_fails=False, relocation_fails=False, native_editor_ack=False):
        with tempfile.TemporaryDirectory(prefix='thor-code-upgrade-') as temporary:
            root=Path(temporary).resolve();bin=root/'bin';bin.mkdir();home=root/'home';home.mkdir()
            target=root/'system/Applications/Visual Studio Code.app'
            cli=target/'Contents/Resources/app/bin/code';cli.parent.mkdir(parents=True)
            guard='[ "$DONT_PROMPT_WSL_INSTALL" = 1 ] || exit 53\n' if native_editor_ack else ''
            cli.write_text('#!/bin/sh\n'+guard+'echo '+installed+'\n');cli.chmod(0o755)
            (target/'previous-marker').write_text('previous editor')
            (bin/'code').symlink_to(cli)
            for name in ('mktemp','rm','mkdir','mv','dirname','cat','awk','head','grep'):
                (bin/name).symlink_to(shutil.which(name))
            def executable(name,body,python=False):
                path=bin/name
                path.write_text(('#!'+sys.executable+'\n' if python else '#!/bin/sh\n')+body+'\n');path.chmod(0o755)
            executable('uname','case "$1" in -s) echo Darwin;; -m) echo arm64;; esac')
            executable('xcrun','exit 0');executable('id','echo 1000')
            executable('sw_vers','echo '+system)
            ps_output=("/Applications/Visual Studio Code.app/Contents/MacOS/"+('Code' if running=='Code' else 'Electron')) if running else ''
            if running=='grep':ps_output='grep -E /Visual Studio Code[.]app/Contents/MacOS/(Electron|Code)([[:space:]]|$)'
            executable('ps','printf \'%s\\n\' '+repr(ps_output))
            if running=='late':
                executable('ps','''import os,pathlib
p=pathlib.Path(os.environ['FIXTURE_LOG']+'.ps')
count=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(count))
if count>1:print('/Applications/Visual Studio Code.app/Contents/MacOS/Code')''',True)
            executable('codesign','exit 7' if signature_fails else 'exit 0')
            executable('PlistBuddy','echo '+app_minimum)
            executable('clang','import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index("-o")+1]);p.write_text("#!/bin/sh\\nexit 0\\n");p.chmod(0o755)',True)
            executable('curl','''import json,os,pathlib,sys
pathlib.Path(os.environ['FIXTURE_LOG']).write_text(json.dumps(sys.argv[1:]))
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(b'archive')''',True)
            executable('ditto','''import os,pathlib,sys
app=pathlib.Path(sys.argv[-1])/'Visual Studio Code.app'
cli=app/'Contents/Resources/app/bin/code';cli.parent.mkdir(parents=True)
cli.write_text('#!'+sys.executable+'\\nimport os,sys\\nfrom pathlib import Path\\n'
    +'if os.environ.get("FAIL_RELOCATION") and str(Path(__file__)).startswith(os.environ["FIXTURE_TARGET"]):sys.exit(51)\\n'
    +'print("1.147.0")\\n');cli.chmod(0o755)''',True)
            for name in ('vscode-minimum','vscode-macos-version'):
                shutil.copy2(ROOT/'install'/name,root/name)
            text=(ROOT/'install/prerequisites.sh').read_text()
            for suffix in ('','/Contents/Resources/app/bin/code'):
                text=text.replace("'/Applications/Visual Studio Code.app"+suffix+"'",repr(str(target)+suffix))
            text=text.replace('/usr/share/code',str(root/'system/usr/share/code'))
            text=text.replace('/usr/libexec/PlistBuddy',str(bin/'PlistBuddy'))
            script=root/'prerequisites.sh';script.write_text(text)
            settings=home/'Library/Application Support/Code/User/settings.json';settings.parent.mkdir(parents=True)
            settings.write_text('{"custom":"preserved"}')
            log=root/'downloads.json'
            result=subprocess.run(['/bin/sh',str(script),'--vscode'],capture_output=True,text=True,timeout=20,
                env={**os.environ,'PATH':str(bin),'HOME':str(home),'CI':'true','FIXTURE_LOG':str(log),
                     'FIXTURE_TARGET':str(target),'FAIL_RELOCATION':'1' if relocation_fails else ''})
            self.assertEqual(settings.read_text(),'{"custom":"preserved"}')
            artifacts=list(target.parent.glob('.thor-code-*'))
            self.assertEqual(artifacts,[],result.stdout+result.stderr)
            return result,json.loads(log.read_text()) if log.exists() else [],(target/'previous-marker').exists()

    def test_macos_old_vscode_is_upgraded_with_a_reviewed_ventura_binary(self):
        result,downloads,previous=self.exercise_macos_editor()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(previous)
        self.assertIn('https://update.code.visualstudio.com/1.147.0/darwin-arm64/stable',downloads)
        self.assertIn('VS Code 1.147.0 installed',result.stdout)

    def test_macos_compatible_existing_code_is_reused_with_numeric_versions(self):
        for version in ('1.96.0','1.100.0','1.147.0-insider'):
            with self.subTest(version=version):
                result,downloads,previous=self.exercise_macos_editor(installed=version,running=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(downloads,[]);self.assertTrue(previous)

    def test_native_editor_probe_acknowledges_the_wsl_launcher_prompt(self):
        result,downloads,previous=self.exercise_macos_editor(installed='1.147.0',native_editor_ack=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(downloads,[]);self.assertTrue(previous)

    def test_macos_old_running_editor_requires_a_normal_quit_before_upgrade(self):
        for running in (True,'Code'):
            with self.subTest(running=running):
                result,downloads,previous=self.exercise_macos_editor(running=running)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Save your work and quit VS Code',result.stderr)
                self.assertEqual(downloads,[]);self.assertTrue(previous)

    def test_macos_process_check_does_not_mistake_its_grep_for_a_running_editor(self):
        result,downloads,previous=self.exercise_macos_editor(running='grep')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(previous)

    def test_editor_opened_during_download_is_preserved(self):
        result,downloads,previous=self.exercise_macos_editor(running='late')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('opened during the download',result.stderr)
        self.assertTrue(downloads);self.assertTrue(previous)

    def test_macos_bad_signature_or_os_minimum_preserves_the_old_application(self):
        for options in ({'signature_fails':True},{'app_minimum':'14.0'}):
            with self.subTest(options=options):
                result,downloads,previous=self.exercise_macos_editor(**options)
                self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertTrue(previous)

    def test_macos_failed_relocation_check_restores_the_previous_application(self):
        result,downloads,previous=self.exercise_macos_editor(relocation_fails=True)
        self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertIn('previous application was restored',result.stderr)
        self.assertTrue(previous)

    def test_newer_macos_uses_current_stable_code_instead_of_the_legacy_pin(self):
        result,downloads,previous=self.exercise_macos_editor(system='15.6')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('https://update.code.visualstudio.com/latest/darwin-arm64/stable',downloads)
