"""Shared toolchain inputs, project-local caches and debug-only map output."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from thorlang import driver
from thorlang.compiler import Source

ROOT=Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-installation-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()

    def build(self,name,body,*,flags=(),command='build'):
        project=self.root/name
        project.mkdir(exist_ok=True)
        (project/'main.thor').write_text('package main\nimport "thor:basic"\nmain::proc(){'+body+'}\n')
        with contextlib.chdir(project),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()) as errors:
            status=driver._main([command,'.',*flags])
        self.assertEqual(status,0,errors.getvalue())
        output=project/'.thor-build'/name
        return project,output,json.loads((output/'thor-manifest.json').read_text())

    def test_projects_share_generic_library_output_but_keep_caches_and_executables_local(self):
        first,out,m=self.build('first','values:=basic.sorted([]int{2,1});assert(values[0]==1);delete(values)')
        collection=Path(next(f.split('=',1)[1] for f in m['odin_flags'] if f.startswith('-collection:thor=')))
        library=collection/'basic/basic.odin'
        before=(library.stat().st_mtime_ns,library.read_bytes())
        original=Source.transpile
        def no_retranslation(source,**kwargs):
            self.assertNotEqual(source.path,ROOT/'runtime/basic/basic.thor')
            return original(source,**kwargs)
        with patch.object(Source,'transpile',no_retranslation):
            second,out2,m2=self.build('second','values:=basic.sorted([]string{"z","a"});assert(values[0]=="a");delete(values)')
        self.assertEqual(next(f for f in m['odin_flags'] if f.startswith('-collection:thor=')),
                         next(f for f in m2['odin_flags'] if f.startswith('-collection:thor=')))
        self.assertEqual(before,(library.stat().st_mtime_ns,library.read_bytes()))
        for project,output in [(first,out),(second,out2)]:
            self.assertTrue((project/project.name).is_file())
            self.assertTrue(list((project/'.thor-cache/parse-v1').glob('*.ast')))
            self.assertEqual([p.name for p in output.rglob('*.odin')],['main.odin'])
            self.assertFalse(list(output.rglob('*.map.json')))
            run=subprocess.run([str(project/project.name)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_debug_maps_reference_shared_library_without_copying_it(self):
        _,output,m=self.build('debugging','basic.print("hello")',flags=['-debug','-o:none'])
        self.assertTrue((output/'main.odin.map.json').is_file())
        self.assertTrue(m['shared_maps'])
        self.assertTrue(all(Path(path).is_relative_to(ROOT/'lib') for path in m['shared_maps']))
        self.assertFalse((output/'runtime').exists())
        self.assertIn('debug_program',m)

    def test_project_specific_checked_library_versions_remain_local(self):
        project=self.root/'checked';project.mkdir()
        (project/'main.thor').write_text('''package main
import "thor:basic"
work::fn(n:int)->bool{return basic.asc(n,n+1,n+2)}
VALUE::work(3)
main::proc(){n:=4;assert(work(n)&&VALUE)}
''')
        result=subprocess.run([str(ROOT/'thor'),'build','.'],cwd=project,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        output=project/'.thor-build/checked'
        self.assertTrue((output/'.thor-runtime/basic/basic.odin').is_file())
        run=subprocess.run([str(project/'checked')],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_clearing_one_project_cache_does_not_clear_another(self):
        first,_,_=self.build('one','basic.print("one")')
        second,_,_=self.build('two','basic.print("two")')
        files={p:p.read_bytes() for p in (second/'.thor-cache/parse-v1').glob('*.ast')}
        self.build('one','basic.print("one")',flags=['--clear-cache'])
        self.assertTrue(files)
        self.assertTrue(all(p.read_bytes()==raw for p,raw in files.items()))

    def test_debug_map_files_are_removed_by_a_later_ordinary_build(self):
        _,output,_=self.build('changing','basic.print("hello")',flags=['-debug'])
        self.assertTrue(list(output.rglob('*.map.json')))
        _,_,manifest=self.build('changing','basic.print("hello")')
        self.assertFalse(list(output.rglob('*.map.json')))
        self.assertEqual(manifest['shared_maps'],[])
        self.assertNotIn('debug_program',manifest)

    def test_aggregate_result_cache_hit_does_not_write_an_evaluator_project(self):
        project=self.root/'aggregate';project.mkdir()
        (project/'main.thor').write_text('package main\nwork::fn()->[2]int{return [3,4]}\nVALUE::#run work()\nmain::proc(){assert(VALUE[1]==4)}\n')
        with contextlib.chdir(project):
            self.assertEqual(driver._main(['build','.']),0)
            original=driver.Project.write
            def only_final_output(unit):
                self.assertFalse(unit.purity.eval_mode,'a cache hit wrote a temporary evaluator project')
                return original(unit)
            with patch.object(driver.Project,'write',only_final_output):
                self.assertEqual(driver._main(['build','.']),0)

    def test_adapted_library_keeps_all_files_in_its_package(self):
        import shutil
        from thorlang.evaluation import Evaluator
        fake=self.root/'toolchain';package=fake/'runtime/basic';package.mkdir(parents=True)
        (package/'basic.thor').write_text('package basic\nwork::proc()->int{return helper()}\n')
        (package/'helper.odin').write_text('package basic\nhelper::proc()->int{return 42}\n')
        shutil.copytree(ROOT/'runtime/sets',fake/'runtime/sets')
        app=self.root/'app';app.mkdir()
        (app/'main.thor').write_text('package main\nimport "thor:basic"\nmain::proc(){assert(basic.work()==42)}\n')
        frontend,odin=driver.frontend_binary('odin')
        with patch.object(driver,'ROOT',fake):
            unit=driver.Project(app,frontend,[])
            Evaluator(unit,odin).evaluate_all()
            target=unit.emit(self.root/'generated')
            result=subprocess.run([odin,'run',str(target),*unit.odin_flags(),'-out:'+str(self.root/'program')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((unit.destination/'.thor-runtime/basic/basic.odin').is_file())
        self.assertTrue((unit.destination/'.thor-runtime/basic/helper.odin').is_file())

    def test_unsaved_entry_parser_cache_uses_the_project_directory(self):
        project=self.root/'unsaved';project.mkdir()
        entry=project/'new.thor'
        frontend,_=driver.frontend_binary('odin')
        unit=driver.Project(entry,frontend,[],overlays={entry:b'package main\nmain::proc(){}\n'})
        self.assertEqual(unit.cache_directory,project/'.thor-cache')
        self.assertFalse(entry.exists())
        self.assertTrue(list((project/'.thor-cache/parse-v1').glob('*.ast')))
