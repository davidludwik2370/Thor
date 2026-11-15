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

    def test_backend_signal_is_one_toolchain_diagnostic_with_conventional_status(self):
        self.write('package main\nmain::proc(){}\n')
        original=subprocess.run
        for stderr in [b'',b'native compiler context\n']:
            for flags in [[],['--diagnostic-format=json'],['--raw-diagnostics']]:
                with self.subTest(stderr=stderr,flags=flags):
                    def crash(command,*args,**kwargs):
                        if len(command)>1 and command[1]=='check':
                            return subprocess.CompletedProcess(command,-11,stdout=b'',stderr=stderr)
                        return original(command,*args,**kwargs)
                    with patch.object(driver.subprocess,'run',crash):
                        _,errors=self.compile(flags=flags,status=139)
                    self.assertIn('Odin compiler terminated by SIGSEGV',errors)
                    if '--diagnostic-format=json' in flags:
                        data=json.loads(errors)
                        self.assertEqual(len(data['errors']),1,data)
                        self.assertIsNone(data['errors'][0]['pos'])
                        self.assertEqual(data['errors'][0]['stage'],'toolchain')

    def test_every_build_runs_analysis_and_keeps_normal_maps_in_memory(self):
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
        self.assertFalse(list(self.output.rglob('*.map.json')))

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
        self.checked(flags=['-debug'])
        self.checked(flags=['-debug'])
        source = next(self.output.rglob('main.odin'))
        for path in [source, Path(str(source)+'.map.json'), self.output/'thor-manifest.json']:
            original = path.read_bytes()
            # A valid ownership manifest with a changed entry is recoverable;
            # a malformed ownership manifest deliberately retains its old error.
            if path.name=='thor-manifest.json':
                manifest=json.loads(original);manifest['entry']='changed';path.write_text(json.dumps(manifest))
            else:
                path.write_text('damaged')
            self.checked(flags=['-debug'])
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

    def test_sdk_overlay_generates_only_augmented_files_and_preserves_linked_inputs(self):
        self.write('''package main
import "core:math"
work::fn(x:f64)->int{return int(math.floor(x))}
VALUE::work(23.5)
main::proc(){x:=4.5;assert(work(x)==4&&VALUE==23&&math.floor(x)==4)}
''')
        self.checked(command='build',flags=['-debug'])
        manifest=json.loads((self.output/'thor-manifest.json').read_text())
        sdk=Path(manifest['odin_environment']['ODIN_ROOT'])
        generated=[p for p in sdk.rglob('*.odin') if not p.is_symlink()]
        self.assertTrue(generated)
        originals=set()
        for path in generated:
            mapping=json.loads(Path(str(path)+'.map.json').read_text())
            originals.update(Path(name) for name in mapping['sources'])
        native=Path(subprocess.check_output([shutil.which('odin'),'root'],text=True).strip()).resolve()
        self.assertTrue((sdk/'core/fmt').is_symlink())
        for original in originals:
            for sibling in original.parent.iterdir():
                if sibling in originals:continue
                linked=sdk/sibling.relative_to(native)
                self.assertTrue(linked.is_symlink(),linked)
                self.assertEqual(linked.resolve(),sibling.resolve())
        self.assertFalse(any('.thor-sdk-maps' in name for name in manifest['generated']))
        run=subprocess.run([str(self.root/self.root.name)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_sdk_overlay_layout_changes_do_not_write_or_delete_installed_inputs(self):
        source='''package main
import "core:math"
work::fn(x:f64)->f64{return math.floor(x)}
main::proc(){x:=4.5;_=work(x)}
'''
        self.write(source);self.checked()
        manifest=json.loads((self.output/'thor-manifest.json').read_text())
        old_sdk=Path(manifest['odin_environment']['ODIN_ROOT'])
        native=(old_sdk/'base').resolve().parent
        originals={p:p.read_bytes() for p in (native/'core/math').glob('*.odin')}
        old_links=[self.output/name for name in manifest['generated'] if (self.output/name).is_symlink()]
        self.source.write_text(source.replace('math.floor(x)','math.sqrt(x)'))
        self.checked()
        self.source.write_text('package main\nmain::proc(){}\n')
        self.checked()
        self.assertTrue(all(not p.is_symlink() for p in old_links))
        self.assertTrue(all(p.read_bytes()==raw for p,raw in originals.items()))
        self.assertEqual(json.loads((self.output/'thor-manifest.json').read_text())['odin_environment'],{})

    def test_sdk_overlay_preserves_unsaved_native_files_and_relative_resources(self):
        self.write('''package main
import "core:math"
import sample "core:thor_overlay_fixture"
work::fn(x:f64)->int{return int(math.floor(x))}
main::proc(){x:=4.5;assert(work(x)==4&&sample.TEXT=="overlay")}
''')
        native=Path(subprocess.check_output([shutil.which('odin'),'root'],text=True).strip()).resolve()
        package=native/'core/thor_overlay_fixture'
        self.assertFalse(package.exists())
        replacements={}
        for name,raw in [('main.odin',b'package sample\nTEXT::#load("data.txt",string)\n'),('data.txt',b'overlay')]:
            replacement=self.root/('replacement-'+name)
            replacement.write_bytes(raw)
            replacements[str(package/name)]=str(replacement)
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':replacements}))
        self.checked(command='build',flags=['--overlay',str(overlay)])
        run=subprocess.run([str(self.root/self.root.name)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertFalse(package.exists())

    def test_linked_sdk_overload_notes_keep_original_library_paths(self):
        self.write('''package main
import "core:math"
work::fn(x:f64)->int{return int(math.floor(x))}
main::proc(){_=math.cos("bad")}
''')
        errors=self.checked(status=1,flags=['--diagnostic-format','json'])
        self.assertIn('No procedures or ambiguous call',errors)
        self.assertIn('math_basic.odin',errors)
        self.assertNotIn('.thor-sdk-',errors)
        self.assertNotIn('__thor_',errors)


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
