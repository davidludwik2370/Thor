"""Source-install platform dispatch and no-change bootstrap paths."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
from install import from_source as source

class SourceInstallerTests(unittest.TestCase):
    def test_explicit_unsupported_llvm_is_rejected_without_silent_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            config=Path(temporary)/'llvm-config';config.write_text('fixture')
            with patch.object(source.subprocess,'check_output',return_value='15.0.0'):
                with self.assertRaisesRegex(ValueError,'LLVM 17–22'):source.llvm_config({'LLVM_CONFIG':str(config)})
    def test_bsd_source_packaging_does_not_invoke_linux_or_macos_loader_tools(self):
        from tools import build_distribution
        for system in ['FreeBSD','NetBSD','OpenBSD']:
            with self.subTest(system=system),patch.object(build_distribution.platform,'system',return_value=system),patch.object(build_distribution,'capture',side_effect=AssertionError('foreign loader command')):
                build_distribution.relocate_odin(Path('/unused-stage'))
    def test_all_documented_odin_hosts_and_no_unsupported_architectures(self):
        for system,architectures in [('Darwin',['arm64','x86_64']),('Linux',['aarch64','x86_64']),('Windows',['AMD64']),('FreeBSD',['amd64','aarch64']),('NetBSD',['amd64','aarch64']),('OpenBSD',['amd64'])]:
            for arch in architectures:
                with self.subTest(system=system,arch=arch):self.assertIn(source.host(system,arch).split('-')[-1],['aarch64','x86_64'])
        for system,arch in [('Windows','ARM64'),('Windows','x86'),('Linux','i686'),('OpenBSD','aarch64')]:
            with self.assertRaises(ValueError):source.host(system,arch)

    @unittest.skipIf(os.name=='nt','POSIX bootstrap; Windows bootstrap is covered by native CI source installation')
    def test_source_dry_run_needs_no_build_dependencies_or_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            env={**os.environ,'HOME':temporary}
            result=subprocess.run([str(ROOT/'thor-install'),'--from-source','--dry-run','--vscode','--profile','Thor'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('no changes made',result.stdout)
            self.assertEqual(list(Path(temporary).iterdir()),[])

    @unittest.skipIf(os.name=='nt','POSIX source bootstrap')
    def test_editor_preflight_failure_stops_before_source_compilation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'release').mkdir();(root/'install').mkdir()
            shutil.copy2(ROOT/'thor-install',root/'thor-install')
            shutil.copy2(ROOT/'release/version',root/'release/version')
            (root/'install/prerequisites.sh').write_text('''#!/bin/sh
case " $* " in *" --vscode "*) echo 'Editor version is too old' >&2; exit 37;; esac
''')
            marker=root/'build-started'
            (root/'install/from_source.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("started")\n')
            result=subprocess.run([str(root/'thor-install'),'--from-source','--vscode','--profile','Thor'],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,37,result.stdout+result.stderr)
            self.assertIn('Editor version is too old',result.stderr)
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name=='nt','POSIX bootstrap; Windows bootstrap is covered by native CI source installation')
    def test_prerequisite_dry_run_does_not_install_anything(self):
        result=subprocess.run(['sh',str(ROOT/'install/prerequisites.sh'),'--source','--vscode','--dry-run'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('source build=true',result.stdout)

    def test_source_build_uses_disposable_targets_pinned_sdk_and_two_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary);odin=work/'odin';odin.write_text('fixture')
            calls=[]
            def run(args,**kwargs):
                calls.append((list(map(str,args)),kwargs))
            def private_rust(work,env,target):
                env.update(RUSTUP_HOME=str(work/'private-rustup'),CARGO_HOME=str(work/'private-cargo'))
                env['PATH']=str(work/'private-bin')+os.pathsep+env['PATH']
            caller={'NPM_CONFIG_CACHE':'/user/cache','NODE_COMPILE_CACHE':'/user/node-cache',
                    'RUSTUP_HOME':'/user/rustup','CARGO_HOME':'/user/cargo','CARGO_TARGET_DIR':'/user/target'}
            with patch.dict(os.environ,caller),patch.object(source,'run',side_effect=run),patch.object(source.source_tools,'ensure_rust',side_effect=private_rust),patch.object(source,'ensure_node'),patch.object(source,'llvm_config',side_effect=AssertionError('reused Odin does not need LLVM')):
                source.build(work,[],odin)
            cargo=[(a,k) for a,k in calls if a[0]=='cargo']
            self.assertEqual(len(cargo),2)
            for args,kwargs in cargo:
                self.assertIn('--locked',args);self.assertIn('--release',args)
                self.assertEqual(args[-2:],['-j','2'])
                self.assertEqual(kwargs['env']['CARGO_TARGET_DIR'],str(work/'target'))
                self.assertEqual(kwargs['env']['CARGO_INCREMENTAL'],'0')
                self.assertEqual(kwargs['env']['NODE_COMPILE_CACHE'],str(work/'node-compile-cache'))
                self.assertNotIn('NPM_CONFIG_CACHE',kwargs['env'])
            self.assertTrue(any('build_distribution.py' in ' '.join(a) for a,k in calls))
            self.assertEqual(calls[-1][0][-2],'--bundle')
            npm_ci=next(a for a,k in calls if a[0] in ('npm','npm.cmd') and a[1]=='ci')
            self.assertIn('--omit=optional',npm_ci);self.assertIn('--ignore-scripts',npm_ci)
            npm_exec=next(a for a,k in calls if a[0] in ('npm','npm.cmd') and a[1]=='exec')
            self.assertIn('--offline',npm_exec)
            installer_env=calls[-1][1]['env']
            for key,value in caller.items():self.assertEqual(installer_env[key],value)
            self.assertNotIn(str(work/'private-bin'),installer_env['PATH'])

    @unittest.skipIf(os.name=='nt','POSIX bootstrap; Windows bootstrap is covered by native CI source installation')
    def test_maintenance_never_downloads_or_requires_build_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            env={**os.environ,'HOME':temporary}
            result=subprocess.run([str(ROOT/'thor-install'),'--uninstall'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('not installed',result.stdout)
            self.assertEqual(list(Path(temporary).iterdir()),[])

    @unittest.skipIf(os.name=='nt','POSIX bootstrap; Windows bootstrap is covered by native CI source installation')
    def test_help_documents_new_operations(self):
        result=subprocess.run([str(ROOT/'thor-install'),'--help'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        for option in ['--from-source','--uninstall','--doctor','--no-install-deps']:
            self.assertIn(option,result.stdout)

    @unittest.skipIf(os.name=='nt','POSIX source bootstrap')
    def test_reusing_odin_skips_sdk_prerequisite_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'release').mkdir();(root/'install').mkdir()
            shutil.copy2(ROOT/'thor-install',root/'thor-install')
            shutil.copy2(ROOT/'release/version',root/'release/version')
            (root/'install/prerequisites.sh').write_text('#!/bin/sh\nprintf "PREREQUISITES %s %s\\n" "$1" "$2"\n')
            (root/'install/from_source.py').write_text('import sys; print("FORWARDED",repr(sys.argv[1:]))')
            result=subprocess.run([str(root/'thor-install'),'--from-source','--odin','/selected/odin'],
                                  text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PREREQUISITES --source --reuse-odin',result.stdout)
            self.assertIn("'--odin', '/selected/odin'",result.stdout)

    @unittest.skipUnless(sys.platform=='darwin','Apple Python bootstrap')
    def test_apple_python_can_run_source_installer_without_new_python(self):
        result=subprocess.run(['/usr/bin/python3',str(ROOT/'install/from_source.py'),'--dry-run'],
                              text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('No changes made',result.stdout)

@unittest.skipIf(os.name=='nt','POSIX source fallback bootstrap')
class SourceFallbackTests(unittest.TestCase):
    def exercise_macos_bootstrap(self, version, *options, minimum=None):
        with tempfile.TemporaryDirectory(prefix='thor-bootstrap-macos-') as temporary:
            root=Path(temporary);(root/'release').mkdir();(root/'install').mkdir();(root/'.git').mkdir()
            shutil.copy2(ROOT/'thor-install',root/'thor-install')
            shutil.copy2(ROOT/'release/version',root/'release/version')
            (root/'release/macos-minimum').write_text(minimum or (ROOT/'release/macos-minimum').read_text())
            (root/'install/from_source.py').write_text('import sys; print("SOURCE-FALLBACK",repr(sys.argv[1:]))')
            bin=root/'bin';bin.mkdir()
            for name,body in {
                'uname':'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac',
                'sw_vers':f'echo {version}',
                'git':'echo https://github.com/davidludwik2370/Thor.git',
                'curl':f'touch "{root}/download-called"; printf 404',
            }.items():
                path=bin/name;path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o755)
            (bin/'python3').symlink_to(sys.executable)
            home=root/'home';home.mkdir()
            result=subprocess.run([str(root/'thor-install'),'--no-install-deps',*options],capture_output=True,text=True,timeout=15,
                                  env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'HOME':str(home),'THOR_RELEASE_BASE':''})
            self.assertEqual(list(home.iterdir()),[])
            return result,(root/'download-called').exists()

    def test_older_macos_uses_source_without_downloading_incompatible_sdk(self):
        result,downloaded=self.exercise_macos_bootstrap('12.7.6','--vscode','--profile','Thor','--path',minimum='13.0')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(downloaded)
        self.assertIn('this Mac: 12.7.6',result.stdout)
        self.assertIn('SOURCE-FALLBACK',result.stdout)
        for option in ('--vscode','--profile','Thor','--path'):self.assertIn(repr(option),result.stdout)

    def test_macos_release_floor_uses_numeric_versions_and_dry_run_is_inert(self):
        for current,minimum,downloaded in [('14.9','15.0',False),('15.0','15.0',True),
                                           ('15.0.1','15.0',True),('26.0','15.0',True),
                                           ('15.9','15.10',False),('15.10','15.9',True)]:
            with self.subTest(current=current,minimum=minimum):
                result,actual=self.exercise_macos_bootstrap(current,'--dry-run',minimum=minimum)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(actual,downloaded)
                self.assertIn('no changes made',result.stdout.lower())

    def test_custom_release_and_maintenance_bypass_official_macos_floor(self):
        result,downloaded=self.exercise_macos_bootstrap('13.5.1','--release-base','http://localhost:1/releases','--dry-run')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(downloaded)
        self.assertNotIn('Published macOS bundles require',result.stdout)
        result,downloaded=self.exercise_macos_bootstrap('13.5.1','--uninstall')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(downloaded)
        self.assertIn('not installed',result.stdout)

    def test_macos_floor_does_not_replace_an_explicitly_selected_release(self):
        result,downloaded=self.exercise_macos_bootstrap('13.5.1','--version','0.0.0')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(downloaded)
        self.assertIn('Check out that release',result.stderr)
        self.assertNotIn('SOURCE-FALLBACK',result.stdout)

    def test_only_confirmed_missing_assets_trigger_source_fallback(self):
        import functools
        import http.server
        import threading
        for status in [404,503]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);(root/'release').mkdir();(root/'install').mkdir()
                shutil.copy2(ROOT/'thor-install',root/'thor-install')
                shutil.copy2(ROOT/'release/version',root/'release/version')
                (root/'install/from_source.py').write_text('import sys; print("SOURCE-FALLBACK",repr(sys.argv[1:]))')
                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):self.send_response(status);self.end_headers()
                    def log_message(self,*args):pass
                server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    result=subprocess.run([str(root/'thor-install'),'--release-base',f'http://127.0.0.1:{server.server_port}','--no-install-deps','--prefix',str(root/'installed')],capture_output=True,text=True,timeout=15)
                finally:server.shutdown();server.server_close();thread.join()
                if status==404:
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('SOURCE-FALLBACK',result.stdout)
                    self.assertIn(str(root/'installed'),result.stdout)
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertNotIn('SOURCE-FALLBACK',result.stdout)

@unittest.skipIf(os.name=='nt','POSIX official Node archive')
class SourceNodeArchiveTests(unittest.TestCase):
    def test_official_archive_top_directory_and_safe_links_are_accepted(self):
        import hashlib,io,tarfile
        name='node-v22.14.0-linux-arm64'
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as tar:
            root=tarfile.TarInfo(name);root.type=tarfile.DIRTYPE;tar.addfile(root)
            data=b'fixture';entry=tarfile.TarInfo(name+'/bin/node');entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        data=stream.getvalue()
        hashes=hashlib.sha256(data).hexdigest()+'  '+name+'.tar.gz\n'
        with tempfile.TemporaryDirectory() as temporary:
            env={'PATH':'/usr/bin'}
            def download(url,path,expected):
                self.assertEqual(expected,hashlib.sha256(data).hexdigest())
                path.write_bytes(data)
            with patch.object(source,'host',return_value='linux-aarch64'),patch.object(source.subprocess,'check_output',side_effect=[FileNotFoundError(), 'v22.14.0', '10.9.2']),patch.object(source.source_tools,'official_hash',return_value=hashlib.sha256(data).hexdigest()),patch.object(source.source_tools,'download',side_effect=download):
                source.ensure_node(Path(temporary),env)
            self.assertEqual((Path(temporary)/'node'/name/'bin/node').read_bytes(),b'fixture')
            self.assertTrue(env['PATH'].startswith(str(Path(temporary).resolve()/'node'/name/'bin')))

class SourceVsixTests(unittest.TestCase):
    def test_missing_runtime_dependency_is_rejected_before_distribution(self):
        from tools.build_distribution import verify_vsix
        import json,zipfile
        with tempfile.TemporaryDirectory() as temporary:
            vsix=Path(temporary)/'thor.vsix'
            version=json.loads((ROOT/'editors/vscode/package.json').read_text())['version']
            package={'publisher':'thor-local','name':'thor-language','version':version,'dependencies':{'vscode-languageclient':'9.0.1'}}
            with zipfile.ZipFile(vsix,'w') as archive:archive.writestr('extension/package.json',json.dumps(package))
            with self.assertRaisesRegex(ValueError,'runtime dependency missing'):verify_vsix(vsix)
            with zipfile.ZipFile(vsix,'a') as archive:
                archive.writestr('extension/node_modules/vscode-languageclient/package.json','{}')
                archive.writestr('extension/node_modules/vscode-languageclient/node.js','fixture')
            verify_vsix(vsix)

    def test_stale_editor_payload_is_rejected_before_distribution(self):
        from tools.build_distribution import verify_vsix
        import json,zipfile
        with tempfile.TemporaryDirectory() as temporary:
            vsix=Path(temporary)/'stale.vsix'
            package={'publisher':'thor-local','name':'thor-language','version':'0.0.0'}
            with zipfile.ZipFile(vsix,'w') as archive:archive.writestr('extension/package.json',json.dumps(package))
            with self.assertRaisesRegex(ValueError,'Stale Thor VSIX'):verify_vsix(vsix)

class SourceSdkComponentsTests(unittest.TestCase):
    def test_runtime_linkers_and_their_licenses_are_staged_without_build_history(self):
        from tools.build_distribution import sdk_components
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);sdk=root/'sdk';stage=root/'stage'
            (sdk/'bin').mkdir(parents=True)
            (sdk/'bin/radlink.exe').write_bytes(b'linker payload')
            (sdk/'bin/RAD-LICENSE').write_text('runtime helper license')
            (sdk/'obj').mkdir();(sdk/'obj/debug.pdb').write_bytes(b'not runtime')
            sdk_components(sdk,stage)
            self.assertEqual((stage/'odin/bin/radlink.exe').read_bytes(),b'linker payload')
            self.assertEqual((stage/'odin/bin/RAD-LICENSE').read_text(),'runtime helper license')
            self.assertFalse((stage/'odin/obj').exists())

@unittest.skipIf(os.name=='nt','POSIX Linux loader preflight')
class LinuxBaselineTests(unittest.TestCase):
    def exercise(self,libc,*args):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'release').mkdir();(root/'install').mkdir();(root/'.git').mkdir();bin=root/'bin';bin.mkdir()
            shutil.copy2(ROOT/'thor-install',root/'thor-install')
            for name in ['version','linux-compatibility.json']:shutil.copy2(ROOT/'release'/name,root/'release'/name)
            (root/'install/from_source.py').write_text('print("source fallback")')
            for name,body in [('uname','case "$1" in -s) echo Linux;; -m) echo x86_64;; esac'),('getconf','printf "%s\\n" '+repr(libc)),('git','echo https://github.com/davidludwik2370/Thor.git'),('curl','touch "'+str(root/'download')+'"; printf 404')]:
                p=bin/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
            result=subprocess.run([root/'thor-install','--dry-run','--no-install-deps',*args],env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'THOR_RELEASE_BASE':''},capture_output=True,text=True,timeout=10)
            return result,(root/'download').exists()

    def test_floor_and_newer_glibc_reach_acquisition(self):
        for libc in ['glibc 2.35','glibc 2.39','glibc 2.100']:
            with self.subTest(libc=libc):
                p,downloaded=self.exercise(libc)
                self.assertEqual(p.returncode,0,p.stderr);self.assertTrue(downloaded)

    def test_old_or_non_gnu_libc_stops_before_download(self):
        for libc in ['glibc 2.34','glibc 2.9','musl','']:
            with self.subTest(libc=libc):
                p,downloaded=self.exercise(libc)
                self.assertNotEqual(p.returncode,0);self.assertFalse(downloaded);self.assertIn('require glibc 2.35',p.stderr)

    def test_custom_bundle_policy_and_explicit_source_build_are_preserved(self):
        p,downloaded=self.exercise('glibc 2.34','--release-base','http://localhost:1/releases')
        self.assertEqual(p.returncode,0,p.stderr);self.assertTrue(downloaded)
        p,downloaded=self.exercise('glibc 2.34','--from-source')
        self.assertEqual(p.returncode,0,p.stderr);self.assertFalse(downloaded)

class SourceSDKAcquisitionTests(unittest.TestCase):
    def test_portable_macos_tls_uses_an_explicit_odin_deployment_target(self):
        import contextlib
        for system,portable in [('Darwin',True),('Darwin',False),('Linux',True)]:
            with self.subTest(system=system,portable=portable),tempfile.TemporaryDirectory() as t,contextlib.ExitStack() as stack:
                calls=[]
                def run(args,**kwargs):
                    args=list(map(str,args));calls.append(args)
                    if args[-2:]==['rev-parse','HEAD']:
                        return next(a[-1] for a in calls if a[:1]==['git'] and 'fetch' in a)
                for obj,name in [(source.source_tools,'ensure_rust'),(source.source_tools,'ensure_git_lfs'),(source,'ensure_node'),(source.source_tools,'retain_llvm_notices')]:stack.enter_context(patch.object(obj,name))
                stack.enter_context(patch.object(source,'llvm_config',return_value='/llvm/bin/llvm-config'))
                stack.enter_context(patch.object(source,'host',return_value='macos-aarch64' if system=='Darwin' else 'linux-aarch64'))
                stack.enter_context(patch.object(source.platform,'system',return_value=system))
                stack.enter_context(patch.object(source,'run',side_effect=run))
                source.build(Path(t),[],portable=portable)
                tls=next(a for a in calls if str(ROOT/'tls/src') in a)
                flags=[a for a in tls if a.startswith('-minimum-os-version:')]
                expected=['-minimum-os-version:'+(ROOT/'release/macos-minimum').read_text().strip()] if system=='Darwin' and portable else []
                self.assertEqual(flags,expected)
                if os.name!='nt':
                    compiler_build=next(a for a in calls if a[0] in ('make','gmake'))
                    self.assertEqual(compiler_build[-1],'release' if portable else 'release-native')

    def test_lfs_filters_are_scoped_to_the_disposable_checkout(self):
        import contextlib,json
        profile=json.loads((ROOT/'rust/data'/('noalloc-io-sdk-windows.json' if os.name=='nt' else 'noalloc-io-sdk.json')).read_text())
        revision=profile.get('revision',profile.get('reviewed_sdk_revision'))
        calls=[]
        def run(args,**kwargs):
            args=list(map(str,args));calls.append((args,kwargs))
            if args[-2:]==['rev-parse','HEAD']:
                return next(a[-1] for a,k in calls if a[:1]==['git'] and 'fetch' in a)
        with tempfile.TemporaryDirectory() as t,contextlib.ExitStack() as stack:
            for obj,name in [(source.source_tools,'ensure_rust'),(source.source_tools,'ensure_git_lfs'),(source,'ensure_node'),(source.source_tools,'retain_llvm_notices')]:stack.enter_context(patch.object(obj,name))
            stack.enter_context(patch.object(source,'llvm_config',return_value='/llvm/bin/llvm-config'))
            stack.enter_context(patch.object(source,'run',side_effect=run))
            source.build(Path(t),[])
        local=[a for a,k in calls if 'lfs' in a and 'install' in a]
        self.assertEqual(len(local),1);self.assertIn('--local',local[0]);self.assertIn('--skip-repo',local[0])
        pull=next((a,k) for a,k in calls if 'lfs' in a and 'pull' in a)
        self.assertIn('lfs.concurrenttransfers=2',pull[0]);self.assertEqual(pull[1]['env']['GIT_LFS_SKIP_SMUDGE'],'0')
        self.assertFalse(any('--global' in a for a,k in calls))


@unittest.skipUnless(os.name == 'nt', 'Native Windows launcher checks')
class WindowsLauncherPolicyTests(unittest.TestCase):
    def launch(self, policy, *, downloaded=False):
        with tempfile.TemporaryDirectory(prefix='thor launcher ') as temporary:
            root=Path(temporary)
            shutil.copy2(ROOT/'thor-install.cmd',root/'thor-install.cmd')
            script=root/'thor-install.ps1'
            script.write_text("Write-Output ('EXECUTED: '+($args -join '|')); exit 17\n")
            if downloaded:
                Path(str(script)+':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n')
            env=dict(os.environ)
            env.pop('PSExecutionPolicyPreference',None)
            if policy is not None:env['PSExecutionPolicyPreference']=policy
            command=subprocess.list2cmdline([str(root/'thor-install.cmd'),'--prefix',str(root/'space path'),'--path'])
            return subprocess.run(command,shell=True,env=env,capture_output=True,text=True,timeout=30)

    def test_local_script_and_quoted_arguments_with_remote_signed(self):
        result=self.launch('RemoteSigned')
        self.assertEqual(result.returncode,17,result.stdout+result.stderr)
        self.assertIn('EXECUTED: --prefix|',result.stdout)
        self.assertIn('space path|--path',result.stdout)

    def test_explicit_restricted_policy_is_preserved(self):
        result=self.launch('Restricted')
        self.assertNotEqual(result.returncode,17)
        self.assertNotIn('EXECUTED:',result.stdout)

    def test_explicit_all_signed_policy_is_preserved(self):
        result=self.launch('AllSigned')
        self.assertNotEqual(result.returncode,17)
        self.assertNotIn('EXECUTED:',result.stdout)

    def test_downloaded_unsigned_script_remains_blocked(self):
        result=self.launch('RemoteSigned',downloaded=True)
        self.assertNotEqual(result.returncode,17)
        self.assertNotIn('EXECUTED:',result.stdout)

    def test_unconfigured_windows_client_can_run_local_checkout(self):
        env=dict(os.environ);env.pop('PSExecutionPolicyPreference',None)
        # Query Windows PowerShell's built-in module, even when unittest was
        # launched by PowerShell 7 with its own PSModulePath in the environment.
        env.pop('PSModulePath',None)
        configured=subprocess.check_output(['powershell.exe','-NoProfile','-Command',
            "@((Get-ExecutionPolicy -List).ExecutionPolicy -ne 'Undefined').Count"],env=env,text=True).strip()
        if configured!='0':self.skipTest('Host already has an explicit execution policy')
        result=self.launch(None)
        self.assertEqual(result.returncode,17,result.stdout+result.stderr)


class BootstrapRepositoryTests(unittest.TestCase):
    """Archive downloads must work without Git and must not borrow a parent remote."""
    def resolve(self, metadata='davidludwik2370/Thor', *, checkout=False,
                origin='https://github.com/fixture/Fork.git', parent=False,
                worktree=False, base=None):
        with tempfile.TemporaryDirectory(prefix='thor-release-origin-') as temporary:
            directory=Path(temporary);root=directory/'archive';root.mkdir()
            (root/'release').mkdir();(directory/'home').mkdir();(directory/'temp').mkdir()
            for name in ('version','macos-minimum','linux-compatibility.json'):
                shutil.copy2(ROOT/'release'/name,root/'release'/name)
            if metadata is not None:(root/'release/repository').write_text(metadata+'\n')
            if checkout:
                if worktree:(root/'.git').write_text('gitdir: /fixture/worktree\n')
                else:(root/'.git').mkdir()
            if parent:(directory/'.git').mkdir()
            binary=directory/'bin';binary.mkdir();capture=directory/'url';called=directory/'git-called'
            env={**os.environ,'HOME':str(directory/'home'),'THOR_RELEASE_BASE':'',
                 'THOR_TEST_URL':str(capture),'THOR_TEST_GIT_CALLED':str(called),
                 'THOR_TEST_ORIGIN':origin,'TEMP':str(directory/'temp'),'TMP':str(directory/'temp')}
            options=['--dry-run','--no-install-deps']
            if base:options+=['--release-base',base]
            if os.name=='nt':
                bootstrap=root/'thor-install.ps1';shutil.copy2(ROOT/'thor-install.ps1',bootstrap)
                (binary/'git.cmd').write_text('@echo off\necho called>"%THOR_TEST_GIT_CALLED%"\necho %THOR_TEST_ORIGIN%\n')
                env['PATH']=str(binary)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')
                quote=lambda value:"'"+str(value).replace("'","''")+"'"
                script="""function Invoke-WebRequest {
param([switch]$UseBasicParsing,[string]$Uri,[string]$OutFile)
[IO.File]::WriteAllText($env:THOR_TEST_URL,$Uri)
throw 'STOP_AFTER_RELEASE_RESOLUTION'
}
try { & """+quote(bootstrap)+' '+ ' '.join(map(quote,options))+" } catch {Write-Output $_.Exception.Message; exit 41}\nexit 90"
                command=[str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
                         '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',script]
            else:
                bootstrap=root/'thor-install';shutil.copy2(ROOT/'thor-install',bootstrap)
                scripts={'git':'printf called > "$THOR_TEST_GIT_CALLED"; printf "%s\\n" "$THOR_TEST_ORIGIN"',
                         'curl':'printf "%s\\n" "$@" > "$THOR_TEST_URL"; exit 42'}
                for name,body in scripts.items():
                    file=binary/name;file.write_text('#!/bin/sh\n'+body+'\n');file.chmod(0o755)
                env['PATH']=str(binary)+os.pathsep+os.environ['PATH']
                command=[str(bootstrap),*options]
            result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
            return result,capture.read_text() if capture.exists() else '',called.exists()

    def test_archive_release_resolution_does_not_run_git(self):
        result,url,git=self.resolve()
        self.assertIn('https://github.com/davidludwik2370/Thor/releases/download/v',url,result.stdout+result.stderr)
        self.assertFalse(git)
        self.assertNotEqual(result.returncode,0)  # Download is deliberately intercepted.

    def test_shipped_archive_metadata_resolves_to_its_declared_repository(self):
        repository=(ROOT/'release/repository').read_text().strip()
        result,url,git=self.resolve(repository)
        self.assertIn('https://github.com/'+repository+'/releases/download/v',url,result.stdout+result.stderr)
        self.assertFalse(git)

    def test_archive_does_not_inherit_an_unrelated_parent_git_remote(self):
        result,url,git=self.resolve(parent=True)
        self.assertIn('https://github.com/davidludwik2370/Thor/releases/download/v',url,result.stdout+result.stderr)
        self.assertNotIn('fixture/Fork',url);self.assertFalse(git)

    def test_checkout_origin_overrides_archive_metadata(self):
        for repository in ('Fork','.Thor','_Thor','-Thor'):
            for origin in ('https://github.com/fixture/'+repository+'.git','git@github.com:fixture/'+repository+'.git'):
                with self.subTest(origin=origin):
                    result,url,git=self.resolve(checkout=True,origin=origin)
                    self.assertIn('https://github.com/fixture/'+repository+'/releases/download/v',url,result.stdout+result.stderr)
                    self.assertTrue(git)

    def test_worktree_git_file_uses_its_own_remote(self):
        result,url,git=self.resolve(checkout=True,worktree=True)
        self.assertIn('https://github.com/fixture/Fork/releases/download/v',url,result.stdout+result.stderr)
        self.assertTrue(git)

    def test_invalid_metadata_is_rejected_before_download(self):
        for metadata in (None,'','../repo','owner/.','owner/..','owner/repo/extra','owner/repo\njunk',
                         'https://github.com/owner/repo','owner/repo?x','owner/repo;echo bad'):
            with self.subTest(metadata=metadata):
                result,url,git=self.resolve(metadata)
                self.assertFalse(url);self.assertFalse(git)
                self.assertIn('release location',result.stdout+result.stderr)

    def test_unknown_checkout_remote_does_not_silently_use_archive_metadata(self):
        result,url,git=self.resolve(checkout=True,origin='https://example.invalid/fixture/Fork.git')
        self.assertFalse(url);self.assertTrue(git)
        self.assertIn('release location',result.stdout+result.stderr)

    def test_explicit_release_base_does_not_need_git_or_metadata(self):
        base='https://releases.example.invalid/Thor'
        result,url,git=self.resolve(None,checkout=True,base=base)
        self.assertIn(base+'/thor-',url,result.stdout+result.stderr)
        self.assertFalse(git)
