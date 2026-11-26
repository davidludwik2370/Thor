import unittest
import json
import os
from pathlib import Path
import subprocess
import tempfile
from tests.native_paths import ROOT, file_uri_key, file_uri_path, normalize_lsp_uris, rust_binary, thor_launcher
from tests.native_paths import native_test_compiler
from tests.native_paths import directory_aliases,directory_alias,remove_directory_alias,is_directory_alias,file_reference


class NativePathTests(unittest.TestCase):
    def test_directory_alias_fixtures_retarget_without_touching_targets(self):
        with tempfile.TemporaryDirectory(prefix='Thor SDK Ω & files ') as temporary:
            root=Path(temporary).resolve();first=root/'first';second=root/'second';first.mkdir();second.mkdir()
            (first/'asset').write_text('first');(second/'asset').write_text('second')
            alias=root/'alias';other=root/'other';directory_aliases([(alias,first),(other,second)])
            self.assertTrue(is_directory_alias(alias));self.assertEqual((alias/'asset').read_text(),'first')
            remove_directory_alias(alias);directory_alias(alias,second)
            self.assertEqual((alias/'asset').read_text(),'second')
            remove_directory_alias(alias);remove_directory_alias(other)
            self.assertEqual((first/'asset').read_text(),'first');self.assertEqual((second/'asset').read_text(),'second')
            with self.assertRaisesRegex(ValueError,'ordinary directory'):remove_directory_alias(first)
            reference=root/'reference';file_reference(reference,first/'asset')
            self.assertEqual(reference.read_text(),'first');reference.unlink()
            self.assertEqual((first/'asset').read_text(),'first')

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
