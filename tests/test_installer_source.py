"""Source-install platform dispatch and no-change bootstrap paths."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('thor_source_installer',ROOT/'install/from_source.py')
source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)

class SourceInstallerTests(unittest.TestCase):
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
            with patch.object(source,'run',side_effect=run):source.build(work,[],odin)
            cargo=[(a,k) for a,k in calls if a[0]=='cargo']
            self.assertEqual(len(cargo),2)
            for args,kwargs in cargo:
                self.assertIn('--locked',args);self.assertIn('--release',args)
                self.assertEqual(args[-2:],['-j','2'])
                self.assertEqual(kwargs['env']['CARGO_TARGET_DIR'],str(work/'target'))
                self.assertEqual(kwargs['env']['CARGO_INCREMENTAL'],'0')
            self.assertTrue(any('build_distribution.py' in ' '.join(a) for a,k in calls))
            self.assertEqual(calls[-1][0][-2],'--bundle')

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

@unittest.skipIf(os.name=='nt','POSIX source fallback bootstrap')
class SourceFallbackTests(unittest.TestCase):
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
            with patch.object(source.platform,'system',return_value='Linux'),patch.object(source,'host',return_value='linux-aarch64'),patch.object(source.subprocess,'check_output',side_effect=FileNotFoundError),patch.object(source.urllib.request,'urlopen',side_effect=[io.BytesIO(hashes.encode()),io.BytesIO(data)]):
                source.ensure_node(Path(temporary),env)
            self.assertEqual((Path(temporary)/name/'bin/node').read_bytes(),b'fixture')
            self.assertTrue(env['PATH'].startswith(str(Path(temporary)/name/'bin')))
