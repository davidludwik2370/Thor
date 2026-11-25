import unittest
import json
import os
from pathlib import Path
import subprocess
import tempfile
from tests.native_paths import ROOT, file_uri_key, file_uri_path, normalize_lsp_uris, rust_binary, thor_launcher
from tests.native_paths import native_test_compiler


class NativePathTests(unittest.TestCase):
    def test_diagnostic_uri_identity_only_normalizes_the_windows_drive(self):
        self.assertEqual(file_uri_key('file:///C%3A/h%C3%A9llo/Name.thor',platform='nt'),
                         file_uri_key('file:///c:/h%C3%A9llo/Name.thor',platform='nt'))
        self.assertNotEqual(file_uri_key('file:///C:/Name.thor',platform='nt'),
                            file_uri_key('file:///C:/name.thor',platform='nt'))
        self.assertEqual(file_uri_key('file:///C:/Name.thor',platform='posix'),'file:///C:/Name.thor')

    def test_file_uri_paths_decode_spaces_unicode_and_windows_drives(self):
        self.assertEqual(file_uri_path('file:///C:/my%20project/%E2%98%83.thor',platform='nt'),
                         Path('C:/my project/☃.thor'))
        self.assertEqual(file_uri_path('file:///tmp/my%20project/%E2%98%83.thor',platform='posix'),
                         Path('/tmp/my project/☃.thor'))
        self.assertEqual(file_uri_path('file://server/share/one%20two.thor',platform='nt'),
                         Path('//server/share/one two.thor'))

    def test_native_mock_reads_unsaved_overlay_with_editor_path_spelling(self):
        with tempfile.TemporaryDirectory(prefix='thor mock héllo ') as directory:
            root=Path(directory);package=root/'source';package.mkdir()
            source=package/'main.thor';source.write_text('// BROKEN on disk\n')
            replacement=root/'buffer';replacement.write_text('// clean unsaved buffer\n')
            key=source.as_posix()
            if os.name=='nt':key=key[0].lower()+key[1:]
            overlay=root/'overlay.json'
            overlay.write_text(json.dumps({'Replace':{key:str(replacement)}}))
            compiler=native_test_compiler(root,'locationless')
            command=[str(compiler),'check',str(package),'--overlay',str(overlay)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stderr)['errors'],[])
            overlay.write_text(json.dumps({'Replace':{}}))
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(json.loads(result.stderr)['errors']),1)

    def test_windows_executables_include_the_suffix_for_existence_checks(self):
        self.assertEqual(rust_binary(platform='nt'), ROOT / 'rust/target/release/thor-rust.exe')
        self.assertEqual(rust_binary('value_protocol', example=True, platform='nt'),
                         ROOT / 'rust/target/release/examples/value_protocol.exe')

    def test_unix_release_names_and_launcher_are_preserved(self):
        self.assertEqual(rust_binary(platform='posix'), ROOT / 'rust/target/release/thor-rust')
        self.assertEqual(thor_launcher(platform='posix'), ROOT / 'thor')

    def test_windows_tests_use_the_native_executable(self):
        self.assertEqual(thor_launcher(platform='nt'), rust_binary(platform='nt'))

    def test_windows_lsp_drive_uris_preserve_other_escaping_and_workspace_edit_keys(self):
        encoded='file:///C%3A/project%20folder/%E2%98%83.thor'
        normal='file:///C:/project%20folder/%E2%98%83.thor'
        packet={'uri':encoded,'changes':{encoded:[{'targetUri':encoded}]},'other':[
            'file://server/share/name%3Avalue.thor','file:///C:/name%3Avalue.thor',7,None]}
        expected={'uri':normal,'changes':{normal:[{'targetUri':normal}]},'other':packet['other']}
        self.assertEqual(normalize_lsp_uris(packet,platform='nt'),expected)
        self.assertEqual(packet['uri'],encoded)
        self.assertIs(normalize_lsp_uris(packet,platform='posix'),packet)
