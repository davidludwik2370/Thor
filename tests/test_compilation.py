"""Every build performs fresh analysis, emission and diagnostic source mapping."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from thorlang import driver
from thorlang.dependencies import Inputs


class CompilationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-compilation-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root/'app'
        self.app.mkdir()
        self.source = self.app/'main.thor'
        self.output = self.root/'generated'

    def write(self, text, files=None):
        self.source.write_text(text)
        for name, text in (files or {}).items():
            path = self.app/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def compile(self, *, command='check', flags=(), status=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.chdir(self.root), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = driver._main([command,str(self.app),'--output-dir',str(self.output),'--cache-report',*flags])
        self.assertEqual(result, status, stdout.getvalue()+stderr.getvalue())
        report = next(json.loads(line) for line in stdout.getvalue().splitlines() if line.startswith('{"thor_cache"'))
        return report, stderr.getvalue()

    def checked(self, **kwargs):
        report, errors = self.compile(**kwargs)
        self.assertNotIn('thor_transpile_cache',report)
        self.assertFalse((self.app/'.thor-cache/transpile-v1').exists())
        return errors

    def test_every_build_runs_analysis_and_emits_fresh_sources_and_maps(self):
        self.write('package main\nmain::proc(){values:=[1,2];assert(values[0]==1)}\n')
        self.checked(command='build')
        from thorlang.evaluation import Evaluator
        original_emit=driver.Project.emit
        original_evaluate=Evaluator.evaluate_all
        with patch.object(driver.Project,'emit',autospec=True,side_effect=original_emit) as emitted, \
             patch.object(Evaluator,'evaluate_all',autospec=True,side_effect=original_evaluate) as analyzed:
            self.checked(command='build')
        self.assertEqual(emitted.call_count,1)
        self.assertEqual(analyzed.call_count,1)
        self.assertTrue((self.root/self.root.name).is_file())
        self.assertTrue(all(Path(str(path)+'.map.json').is_file() for path in self.output.rglob('*.odin')))

    def test_edit_parses_only_the_changed_file_and_bypass_reparses_imports(self):
        self.write('package main\nimport "library"\nmain::proc(){x:=library.VALUE;_=x}\n',
                   {'library/main.odin':'package library\nVALUE::1\n'})
        self.checked()
        self.source.write_text(self.source.read_text().replace('x:=','s:=2;_=s;x:='))
        from thorlang import parsecache
        original=subprocess.run
        def parsed_files(flags=()):
            with patch.object(parsecache.subprocess,'run',wraps=original) as launched:
                self.checked(flags=flags)
            return sorted(Path(call.args[0][1]) for call in launched.call_args_list
                          if Path(call.args[0][0]).name.startswith('frontend-'))
        self.assertEqual(parsed_files(),[self.source])
        self.assertEqual(parsed_files(),[])
        self.assertEqual(parsed_files(['--no-cache']),sorted([self.source,self.app/'library/main.odin']))

    def test_obsolete_project_records_are_ignored(self):
        self.write('package main\nmain::proc(){}\n')
        legacy=self.app/'.thor-cache/transpile-v1'/('a'*64+'.json')
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{obsolete project record')
        self.compile()
        self.source.write_text('package main\nmain::proc(){value:int="wrong";_=value}\n')
        _,errors=self.compile(status=1)
        self.assertIn(str(self.source),errors)
        self.assertEqual(legacy.read_text(),'{obsolete project record')
        self.assertEqual(list(legacy.parent.iterdir()),[legacy])

    def test_source_edit_preserving_size_and_mtime_invalidates(self):
        self.write('package main\nVALUE::1\n#assert(VALUE==1)\nmain::proc(){}\n')
        self.checked()
        self.checked()
        before = self.source.stat()
        self.source.write_text(self.source.read_text().replace('VALUE::1','VALUE::2'))
        os.utime(self.source,ns=(before.st_atime_ns,before.st_mtime_ns))
        errors = self.checked(status=1)
        self.assertIn(str(self.source)+'(3:',errors)
        self.assertIn('VALUE==1',errors)

    def test_imported_edits_additions_and_deletions_invalidate(self):
        self.write('package main\nimport "library"\n#assert(library.VALUE==1)\nmain::proc(){}\n',
                   {'library/main.odin':'package library\nVALUE::1\n'})
        self.checked()
        self.checked()
        path = self.app/'library/main.odin'
        path.write_text('package library\nVALUE::2\n')
        self.assertIn('library.VALUE==1',self.checked(status=1))
        path.write_text('package library\nVALUE::1\n')
        self.checked()
        extra = self.app/'library/extra.odin'
        extra.write_text('package library\nVALUE::9\n')
        self.assertIn('redeclaration',self.checked(status=1).lower())
        extra.unlink()
        self.checked()
        self.assertFalse(any(p.name=='extra.odin' for p in self.output.rglob('*.odin')))

    def test_adding_an_entry_file_is_observed(self):
        self.write('package main\nVALUE::1\nmain::proc(){}\n')
        self.checked()
        self.checked()
        (self.app/'extra.thor').write_text('package main\nVALUE::2\n')
        report, errors = self.compile(status=1)
        self.assertIn('redeclar',errors.lower())

    def test_flags_and_test_mode_remain_independent(self):
        self.write('''package main
MODE::#config(MODE,1)
work::proc()->int{return MODE}
VALUE::#run work()
#assert(VALUE==MODE)
main::proc(){}
''')
        for flags in [(), ('-define:MODE=2',), ('-define:MODE=3',)]:
            self.checked(flags=flags)
            self.checked(flags=flags)
        self.checked(command='test')
        self.checked(command='test')

    def test_static_resource_bytes_invalidate_frozen_values(self):
        self.write('''package main
DATA::#load("data.txt",string)
work::proc()->int{return int(DATA[0])}
VALUE::#run work()
#assert(VALUE==97)
main::proc(){}
''', {'data.txt':'abc'})
        self.checked()
        self.checked()
        data = self.app/'data.txt'
        before = data.stat()
        data.write_text('bbc')
        os.utime(data,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertIn('VALUE==97',self.checked(status=1))

    def test_resource_existence_and_directory_membership_invalidate(self):
        self.write('package main\nEXISTS::#exists("optional.txt")\n#assert(!EXISTS)\nmain::proc(){}\n')
        self.checked()
        self.checked()
        (self.app/'optional.txt').write_text('present')
        self.assertIn('!EXISTS',self.checked(status=1))
        self.write('package main\nmain::proc(){data:=#load_directory("assets");_=data}\n', {'assets/a.txt':'one'})
        self.checked()
        self.checked()
        (self.app/'assets/a.txt').write_text('two')
        self.checked()
        (self.app/'assets/b.txt').write_text('second')
        self.checked()
        (self.app/'assets/a.txt').unlink()
        self.checked()

    def test_dynamic_resources_are_checked_fresh(self):
        self.write('package main\nPATH::'+json.dumps(str(self.app/'data.txt'))+'\nDATA::#load(PATH,string)\nmain::proc(){assert(len(DATA)==3)}\n', {'data.txt':'abc'})
        self.checked()
        self.checked()
        (self.app/'data.txt').write_text('abcd')
        self.source.write_text(self.source.read_text().replace('len(DATA)==3','len(DATA)==4'))
        self.checked()

    def test_generated_sources_maps_and_manifest_are_rebuilt(self):
        self.write('package main\nmain::proc(){value:=[1,2];assert(value[0]==1)}\n')
        self.checked()
        self.checked()
        source = next(p for p in self.output.rglob('main.odin') if 'app' in p.parts)
        for path in [source, Path(str(source)+'.map.json'), self.output/'thor-manifest.json']:
            original = path.read_bytes()
            # A valid ownership manifest with a changed entry is recoverable;
            # a malformed ownership manifest deliberately retains its old error.
            if path.name=='thor-manifest.json':
                manifest=json.loads(original);manifest['entry']='changed';path.write_text(json.dumps(manifest))
            else:
                path.write_text('damaged')
            self.checked()
            self.assertEqual(path.read_bytes(),original)

    def test_repeated_backend_errors_keep_original_unicode_ranges_and_messages(self):
        self.write('package main\nmain::proc(){/* 😀 */ value:int="wrong";_=value}\n')
        first = self.checked(status=1,flags=['--diagnostic-format','json'])
        second = self.checked(status=1,flags=['--diagnostic-format','json'])
        self.assertEqual(json.loads(first),json.loads(second))
        errors=json.loads(second)['errors']
        self.assertTrue(all(e['pos']['file']==str(self.source) for e in errors))
        self.assertNotIn('__thor_',second)

    def test_repeated_support_contract_error_uses_original_operation(self):
        self.write('package main\nmain::proc(){/* 😀 */ values:=set[[]int]{};_=values}\n')
        first = self.checked(status=1,flags=['--diagnostic-format','json'])
        second = self.checked(status=1,flags=['--diagnostic-format','json'])
        self.assertEqual(json.loads(first),json.loads(second))
        self.assertIn('Set elements must be valid Odin map keys',second)
        self.assertNotIn('__thor_',second)

    def test_repeated_immutable_guard_diagnostic_retains_its_message(self):
        self.write('package main\nmain::proc(){/* 😀 */ value:>1;value=2}\n')
        first=self.checked(status=1,flags=['--diagnostic-format','json'])
        second=self.checked(status=1,flags=['--diagnostic-format','json'])
        self.assertEqual(json.loads(first),json.loads(second))
        self.assertIn('immutable',second.lower())
        self.assertNotIn('__thor_',second)

    def test_disk_build_replaces_unsaved_overlay_sources_and_maps(self):
        self.write('package main\nmain::proc(){value:int=1;_=value}\n')
        self.checked()
        replacement = self.root/'unsaved.thor'
        replacement.write_text(self.source.read_text().replace('value:int=1','value:int="bad"'))
        overlay = self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        report, errors = self.compile(status=1,flags=['--overlay',str(overlay)])
        self.assertIn(str(self.source),errors)
        self.assertIn('value:int=1',self.source.read_text())
        self.checked()
        self.checked()

    def test_bypass_and_unwritable_evaluation_cache_are_optional(self):
        self.write('package main\nwork::proc()->int{return 7}\nVALUE::#run work()\n#assert(VALUE==7)\nmain::proc(){}\n')
        self.checked()
        report,_=self.compile(flags=['--no-cache'])
        self.assertEqual(report['thor_cache']['hits'],0)
        self.assertGreater(report['thor_cache']['bypasses'],0)
        report,_=self.compile(flags=['--clear-cache'])
        self.assertGreater(report['thor_cache']['writes'],0)
        blocked=self.root/'blocked';blocked.write_text('not a directory')
        report,_=self.compile(flags=['--cache-dir',str(blocked)])
        self.assertEqual(report['thor_cache']['writes'],0)

    def test_retargeting_a_collection_symlink_invalidates(self):
        first=self.root/'first';second=self.root/'second'
        (first/'library').mkdir(parents=True);(second/'library').mkdir(parents=True)
        (first/'library/main.odin').write_text('package library\nVALUE::1\n')
        (second/'library/main.odin').write_text('package library\nVALUE::2\n')
        link=self.app/'catalog'
        link.symlink_to(first,target_is_directory=True)
        flags=['-collection:test='+str(link)]
        self.write('package main\nimport "test:library"\n#assert(library.VALUE==1)\nmain::proc(){}\n')
        self.checked(flags=flags)
        self.checked(flags=flags)
        link.unlink()
        link.symlink_to(second,target_is_directory=True)
        self.assertIn('library.VALUE==1',self.checked(status=1,flags=flags))

    def test_compiler_executable_changes_invalidate_evaluation(self):
        compiler=self.root/'odin-wrapper'
        actual=shutil.which('odin')
        compiler.write_text('#!/usr/bin/env python3\nimport os,sys\n'+f'os.execv({actual!r},[{actual!r},*sys.argv[1:]])\n')
        compiler.chmod(0o755)
        self.write('package main\nwork::proc()->int{return 7}\nVALUE::#run work()\n#assert(VALUE==7)\nmain::proc(){}\n')
        flags=['--odin',str(compiler)]
        self.checked(flags=flags)
        self.checked(flags=flags)
        compiler.write_text(compiler.read_text()+'# rebuilt compiler\n')
        report,_=self.compile(flags=flags)
        self.assertGreater(report['thor_cache']['writes'],0)

    def test_debug_build_refreshes_maps_and_executable_identity(self):
        self.write('package main\nmain::proc(){values:=[1,2];assert(values[0]==1)}\n')
        self.checked(command='build',flags=['-debug'])
        self.checked(command='build',flags=['-debug'])
        manifest=json.loads((self.output/'thor-manifest.json').read_bytes())
        program=manifest['debug_program']
        self.assertEqual(program['sha256'],hashlib.sha256(Path(program['path']).read_bytes()).hexdigest())
        self.assertTrue(all(Path(str(path)+'.map.json').is_file() for path in self.output.rglob('*.odin')))


class InputSnapshotTests(unittest.TestCase):
    def test_sdk_and_support_edits_additions_deletions_and_preserved_times(self):
        with tempfile.TemporaryDirectory(prefix='thor-sdk-cache-') as temporary:
            root=Path(temporary)
            (root/'core/math').mkdir(parents=True)
            file=root/'core/math/math.odin'
            file.write_text('package math\nVALUE::1\n')
            for mutation in ['edit','add','delete']:
                snapshot=Inputs();snapshot.tree(root)
                self.assertTrue(snapshot.valid())
                if mutation=='edit':
                    before=file.stat();file.write_text(file.read_text().replace('1','2'))
                    os.utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
                elif mutation=='add':
                    (file.parent/'new.odin').write_text('package math\n')
                else:
                    file.unlink()
                self.assertFalse(snapshot.valid(),mutation)

    def test_source_change_during_preparation_prevents_publication(self):
        with tempfile.TemporaryDirectory(prefix='thor-input-race-') as temporary:
            path=Path(temporary)/'main.thor'
            path.write_bytes(b'new')
            snapshot=Inputs();snapshot.file(path,b'old')
            self.assertFalse(snapshot.valid())


if __name__=='__main__':
    unittest.main()
