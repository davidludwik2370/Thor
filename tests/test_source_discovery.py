"""Source eligibility follows native file tags, not a _test filename suffix."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests import test_thor as base

UNCHECKED=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']


class SourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-source-discovery-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.app=self.root/'app'
        self.app.mkdir()

    def write(self,name,text):
        path=self.app/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text)
        return path

    def invoke(self,command='run',*,flags=(),success=True):
        result=subprocess.run([str(base.THOR),command,'.',
            '--output-dir',str(self.root/'lowered'),'--cache-dir',str(self.root/'cache'),
            *base.runtime_flags(flags)],cwd=self.app,text=True,capture_output=True,timeout=60)
        if success:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        else:self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def test_sole_test_named_main_builds_in_checked_and_unchecked_modes(self):
        text='package main\nimport "core:fmt"\nmain::proc(){fmt.println("found") }\n'
        for extension in ('thor','odin'):
            path=self.write('hilo_test.'+extension,text)
            for flags in ([],UNCHECKED):
                with self.subTest(extension=extension,flags=flags):
                    self.invoke('build',flags=flags)
                    executable=self.app/(self.app.name+('.exe' if os.name=='nt' else ''))
                    result=subprocess.run([str(executable)],text=True,capture_output=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(result.stdout.strip(),'found')
                    self.assertEqual(path.read_text(),text)
            path.unlink()

    def test_native_odin_includes_an_untagged_test_named_file(self):
        odin=shutil.which(os.environ.get('THOR_ODIN','odin'))
        if not odin:self.skipTest('Odin is required')
        self.write('hilo_test.odin','package main\nimport "core:fmt"\nmain::proc(){fmt.println("native")}\n')
        from tests.native_paths import EXE_SUFFIX
        result=subprocess.run([odin,'run','.', '-out:'+str(self.root/('native-program'+EXE_SUFFIX))],
                              cwd=self.app,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'native')

    def test_test_named_siblings_and_imported_compile_time_dependencies(self):
        self.write('main.thor','''package main
import "helper"
VALUE::#run helper.answer(3)
main::proc(){if VALUE+offset()!=13{panic("discovery")}}
''')
        self.write('offset_test.odin','package main\noffset::proc()->int{return 7}\n')
        self.write('helper/compute_test.thor','package helper\nanswer::proc(n:int)->int{return n*2}\n')
        self.invoke()

    def test_untagged_test_named_bodies_are_checked(self):
        self.write('main.thor','package main\nmain::proc(){}\n')
        self.write('helper_test.thor','package main\nhelper::proc(){missing_in_test_named_source()}\n')
        result=self.invoke('check',success=False)
        self.assertIn('missing_in_test_named_source',result.stderr)
        self.assertIn('helper_test.thor',result.stderr)

    def test_only_explicit_test_tags_exclude_sources_from_normal_builds(self):
        self.write('main.thor','package main\nmain::proc(){}\n')
        for filename in ('helper_test.thor','checks.thor','native_test.odin'):
            self.write(filename,'#+test\npackage main\nhelper_'+filename.replace('.','_')+'::proc(){missing_tagged_name()}\n')
        self.invoke('check')
        result=self.invoke('test',success=False)
        self.assertIn('missing_tagged_name',result.stderr)

    def test_test_runner_includes_tagged_and_untagged_test_named_sources(self):
        self.write('main.thor','package main\n')
        self.write('helpers_test.thor','package main\nanswer::proc()->int{return 7}\n')
        self.write('checks.thor','''#+test
package main
import "core:testing"
@(test) works::proc(t:^testing.T){testing.expect(t,answer()==7)}
''')
        self.invoke('test')

    def test_sole_unsaved_test_named_source_is_a_directory_member(self):
        source=self.app/'hilo_test.thor'
        snapshot=self.root/'snapshot.txt'
        snapshot.write_text('package main\nmain::proc(){}\n')
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(source):str(snapshot)}}))
        self.invoke('check',flags=['--overlay',str(overlay)])
        self.assertFalse(source.exists())
