"""Complete-transpilation reuse, dependency invalidation and cached diagnostics."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from thorlang import driver
from thorlang.buildcache import Inputs


class BuildCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-build-cache-')
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

    def hit(self, **kwargs):
        report, errors = self.compile(**kwargs)
        self.assertEqual(report['thor_transpile_cache']['hits'], 1, report)
        self.assertEqual(report['thor_cache']['hits'], 0, report)
        return errors

    def miss(self, **kwargs):
        report, errors = self.compile(**kwargs)
        self.assertEqual(report['thor_transpile_cache']['hits'], 0, report)
        self.assertEqual(report['thor_transpile_cache']['writes'], 1, report)
        return errors

    def test_warm_build_skips_frontend_evaluator_and_emission(self):
        self.write('''package main
import "core:math" / round
COUNT::int(#run round(1000 ** .33333))
main::proc(){values:[COUNT]int;assert(len(values)==10)}
''')
        self.miss(command='build')
        artifacts = {p:p.stat().st_mtime_ns for p in self.output.rglob('*') if p.is_file()}
        with patch.object(driver,'frontend_binary',side_effect=AssertionError('frontend launched on warm build')), \
             patch.object(driver,'Project',side_effect=AssertionError('project rebuilt on warm build')), \
             patch('thorlang.evaluation.Evaluator.evaluate_all',side_effect=AssertionError('evaluator ran on warm build')):
            self.hit(command='build')
        self.assertTrue((self.root/self.root.name).is_file())
        self.assertEqual(artifacts,{p:p.stat().st_mtime_ns for p in self.output.rglob('*') if p.is_file()})

    def test_source_edit_preserving_size_and_mtime_invalidates(self):
        self.write('package main\nVALUE::1\n#assert(VALUE==1)\nmain::proc(){}\n')
        self.miss()
        self.hit()
        before = self.source.stat()
        self.source.write_text(self.source.read_text().replace('VALUE::1','VALUE::2'))
        os.utime(self.source,ns=(before.st_atime_ns,before.st_mtime_ns))
        errors = self.miss(status=1)
        self.assertIn(str(self.source)+'(3:',errors)
        self.assertIn('VALUE==1',errors)

    def test_imported_edits_additions_and_deletions_invalidate(self):
        self.write('package main\nimport "library"\n#assert(library.VALUE==1)\nmain::proc(){}\n',
                   {'library/main.odin':'package library\nVALUE::1\n'})
        self.miss()
        self.hit()
        path = self.app/'library/main.odin'
        path.write_text('package library\nVALUE::2\n')
        self.assertIn('library.VALUE==1',self.miss(status=1))
        path.write_text('package library\nVALUE::1\n')
        self.miss()
        extra = self.app/'library/extra.odin'
        extra.write_text('package library\nVALUE::9\n')
        self.assertIn('redeclaration',self.miss(status=1).lower())
        extra.unlink()
        self.miss()
        self.assertFalse(any(p.name=='extra.odin' for p in self.output.rglob('*.odin')))

    def test_adding_an_entry_file_is_observed(self):
        self.write('package main\nVALUE::1\nmain::proc(){}\n')
        self.miss()
        self.hit()
        (self.app/'extra.thor').write_text('package main\nVALUE::2\n')
        report, errors = self.compile(status=1)
        self.assertEqual(report['thor_transpile_cache']['hits'],0)
        self.assertIn('redeclar',errors.lower())

    def test_flags_and_test_mode_have_distinct_artifacts(self):
        self.write('''package main
MODE::#config(MODE,1)
work::proc()->int{return MODE}
VALUE::#run work()
#assert(VALUE==MODE)
main::proc(){}
''')
        for flags in [(), ('-define:MODE=2',), ('-define:MODE=3',)]:
            self.miss(flags=flags)
            self.hit(flags=flags)
        self.miss(command='test')
        self.hit(command='test')

    def test_static_resource_bytes_invalidate_frozen_values(self):
        self.write('''package main
DATA::#load("data.txt",string)
work::proc()->int{return int(DATA[0])}
VALUE::#run work()
#assert(VALUE==97)
main::proc(){}
''', {'data.txt':'abc'})
        self.miss()
        self.hit()
        data = self.app/'data.txt'
        before = data.stat()
        data.write_text('bbc')
        os.utime(data,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertIn('VALUE==97',self.miss(status=1))

    def test_resource_existence_and_directory_membership_invalidate(self):
        self.write('package main\nEXISTS::#exists("optional.txt")\n#assert(!EXISTS)\nmain::proc(){}\n')
        self.miss()
        self.hit()
        (self.app/'optional.txt').write_text('present')
        self.assertIn('!EXISTS',self.miss(status=1))
        self.write('package main\nmain::proc(){data:=#load_directory("assets");_=data}\n', {'assets/a.txt':'one'})
        self.miss()
        self.hit()
        (self.app/'assets/a.txt').write_text('two')
        self.miss()
        (self.app/'assets/b.txt').write_text('second')
        self.miss()
        (self.app/'assets/a.txt').unlink()
        self.miss()

    def test_dynamic_resources_bypass_transpilation_reuse(self):
        self.write('package main\nPATH::'+json.dumps(str(self.app/'data.txt'))+'\nDATA::#load(PATH,string)\nmain::proc(){assert(len(DATA)==3)}\n', {'data.txt':'abc'})
        for _ in range(2):
            report, _ = self.compile()
            self.assertEqual(report['thor_transpile_cache']['writes'],0)
            self.assertGreater(report['thor_transpile_cache']['bypasses'],0)

    def test_corrupt_artifacts_maps_manifest_and_record_are_rebuilt(self):
        self.write('package main\nmain::proc(){value:=[1,2];assert(value[0]==1)}\n')
        self.miss()
        self.hit()
        source = next(p for p in self.output.rglob('main.odin') if 'app' in p.parts)
        for path in [source, Path(str(source)+'.map.json'), self.output/'thor-manifest.json']:
            original = path.read_bytes()
            # A valid ownership manifest with a changed entry is recoverable;
            # a malformed ownership manifest deliberately retains its old error.
            if path.name=='thor-manifest.json':
                manifest=json.loads(original);manifest['entry']='changed';path.write_text(json.dumps(manifest))
            else:
                path.write_text('damaged')
            self.miss()
            self.assertEqual(path.read_bytes(),original)
        record = next((self.app/'.thor-cache/transpile-v1').glob('*.json'))
        record.write_text('{partial')
        self.miss()
        data=json.loads(record.read_text());data['record']['target']='damaged';record.write_text(json.dumps(data))
        self.miss()

    def test_cached_backend_errors_keep_original_unicode_ranges_and_messages(self):
        self.write('package main\nmain::proc(){/* 😀 */ value:int="wrong";_=value}\n')
        first = self.miss(status=1,flags=['--diagnostic-format','json'])
        second = self.hit(status=1,flags=['--diagnostic-format','json'])
        self.assertEqual(json.loads(first),json.loads(second))
        errors=json.loads(second)['errors']
        self.assertTrue(all(e['pos']['file']==str(self.source) for e in errors))
        self.assertNotIn('__thor_',second)

    def test_cached_support_contract_error_uses_original_operation(self):
        self.write('package main\nmain::proc(){/* 😀 */ values:=set[[]int]{};_=values}\n')
        first = self.miss(status=1,flags=['--diagnostic-format','json'])
        second = self.hit(status=1,flags=['--diagnostic-format','json'])
        self.assertEqual(json.loads(first),json.loads(second))
        self.assertIn('Set elements must be valid Odin map keys',second)
        self.assertNotIn('__thor_',second)

    def test_artifacts_changed_during_preparation_are_not_published(self):
        self.write('package main\nmain::proc(){}\n')
        original=driver.Project.emit
        def modified(project,destination):
            target=original(project,destination)
            source=project.outputs[self.source]
            source.write_bytes(b'// changed after emission\n'+source.read_bytes())
            return target
        with patch.object(driver.Project,'emit',modified):
            report,_=self.compile()
        self.assertEqual(report['thor_transpile_cache']['writes'],0)
        self.assertGreater(report['thor_transpile_cache']['bypasses'],0)
        self.miss()
        self.hit()

    def test_cached_immutable_guard_diagnostic_retains_its_message(self):
        self.write('package main\nmain::proc(){/* 😀 */ value:>1;value=2}\n')
        first=self.miss(status=1,flags=['--diagnostic-format','json'])
        second=self.hit(status=1,flags=['--diagnostic-format','json'])
        self.assertEqual(json.loads(first),json.loads(second))
        self.assertIn('immutable',second.lower())
        self.assertNotIn('__thor_',second)

    def test_overlays_bypass_reuse_and_disk_build_replaces_unsaved_artifacts(self):
        self.write('package main\nmain::proc(){value:int=1;_=value}\n')
        self.miss()
        replacement = self.root/'unsaved.thor'
        replacement.write_text(self.source.read_text().replace('value:int=1','value:int="bad"'))
        overlay = self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        report, errors = self.compile(status=1,flags=['--overlay',str(overlay)])
        self.assertEqual(report['thor_transpile_cache']['hits'],0)
        self.assertGreater(report['thor_transpile_cache']['bypasses'],0)
        self.assertIn(str(self.source),errors)
        self.assertIn('value:int=1',self.source.read_text())
        self.miss()
        self.hit()

    def test_clear_bypass_and_unwritable_cache_are_optional(self):
        self.write('package main\nmain::proc(){}\n')
        self.miss()
        for flags in [('--no-cache',), ('--no-transpile-cache',)]:
            report,_=self.compile(flags=flags)
            self.assertEqual(report['thor_transpile_cache']['hits'],0)
            self.assertGreater(report['thor_transpile_cache']['bypasses'],0)
        self.miss(flags=['--clear-cache'])
        self.hit()
        blocked=self.root/'blocked';blocked.write_text('not a directory')
        report,_=self.compile(flags=['--cache-dir',str(blocked)])
        self.assertEqual(report['thor_transpile_cache']['writes'],0)
        self.assertGreater(report['thor_transpile_cache']['bypasses'],0)

    def test_retargeting_a_collection_symlink_invalidates(self):
        first=self.root/'first';second=self.root/'second'
        (first/'library').mkdir(parents=True);(second/'library').mkdir(parents=True)
        (first/'library/main.odin').write_text('package library\nVALUE::1\n')
        (second/'library/main.odin').write_text('package library\nVALUE::2\n')
        link=self.app/'catalog'
        link.symlink_to(first,target_is_directory=True)
        flags=['-collection:test='+str(link)]
        self.write('package main\nimport "test:library"\n#assert(library.VALUE==1)\nmain::proc(){}\n')
        self.miss(flags=flags)
        self.hit(flags=flags)
        link.unlink()
        link.symlink_to(second,target_is_directory=True)
        self.assertIn('library.VALUE==1',self.miss(status=1,flags=flags))

    def test_compiler_executable_changes_invalidate(self):
        compiler=self.root/'odin-wrapper'
        actual=shutil.which('odin')
        compiler.write_text('#!/usr/bin/env python3\nimport os,sys\n'+f'os.execv({actual!r},[{actual!r},*sys.argv[1:]])\n')
        compiler.chmod(0o755)
        self.write('package main\nmain::proc(){}\n')
        flags=['--odin',str(compiler)]
        self.miss(flags=flags)
        self.hit(flags=flags)
        compiler.write_text(compiler.read_text()+'# rebuilt compiler\n')
        self.miss(flags=flags)

    def test_debug_build_reuses_maps_and_updates_executable_identity(self):
        self.write('package main\nmain::proc(){values:=[1,2];assert(values[0]==1)}\n')
        self.miss(command='build',flags=['-debug'])
        self.hit(command='build',flags=['-debug'])
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
