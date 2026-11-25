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
            root=Path(temporary);(root/'release').mkdir();(root/'install').mkdir()
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
        result,downloaded=self.exercise_macos_bootstrap('13.5.1','--vscode','--profile','Thor','--path')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(downloaded)
        self.assertIn('this Mac: 13.5.1',result.stdout)
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
            package={'publisher':'thor-local','name':'thor-language','dependencies':{'vscode-languageclient':'9.0.1'}}
            with zipfile.ZipFile(vsix,'w') as archive:archive.writestr('extension/package.json',json.dumps(package))
            with self.assertRaisesRegex(ValueError,'runtime dependency missing'):verify_vsix(vsix)
            with zipfile.ZipFile(vsix,'a') as archive:
                archive.writestr('extension/node_modules/vscode-languageclient/package.json','{}')
                archive.writestr('extension/node_modules/vscode-languageclient/node.js','fixture')
            verify_vsix(vsix)

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
