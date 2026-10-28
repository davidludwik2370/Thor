"""Runtime-only edits must not reconstruct already verified scalar helpers."""
from unittest.mock import patch
import unittest

from tests import test_compilation as base
from thorlang import driver
from thorlang.cache import EvaluationCache


class EditedBuildTests(unittest.TestCase):
    setUp=base.CompilationTests.setUp
    write=base.CompilationTests.write
    compile=base.CompilationTests.compile

    def test_runtime_edits_before_and_after_run_skip_all_helper_projects(self):
        source='''package main
import "core:math" / round
main::proc(){
    // before
    SIZE::1000
    COUNT::int(#run round(SIZE ** .33333))
    values:[COUNT]int
    #assert(COUNT==10)
    _=values
    // after
}
'''
        self.write(source)
        self.compile(command='build')
        for marker,value in [('before','s:=1;_=s'),('after','t:=2;_=t'),('before','u:=3;_=u')]:
            source=source.replace('// '+marker,value+'\n// '+marker)
            self.source.write_text(source)
            original=driver.Project.__init__
            def without_helpers(project,*args,**kwargs):
                self.assertFalse(kwargs.get('overlays'),'runtime edit reconstructed a compile-time helper')
                return original(project,*args,**kwargs)
            with patch.object(driver.Project,'__init__',without_helpers):
                report,_=self.compile(command='build')
            self.assertGreaterEqual(report['thor_prepared_hits'],1,report)
            self.assertEqual(report['thor_cache']['misses'],0,report)

    def test_dependency_body_and_purity_changes_invalidate_early_results(self):
        source='''package main
work::proc(value:int)->int{return value+1}
VALUE::#run work(2)
#assert(VALUE==3)
main::proc(){}
'''
        self.write(source)
        self.compile()
        self.source.write_text(source.replace('main::proc(){}','main::proc(){s:=1;_=s}'))
        report,_=self.compile()
        self.assertEqual(report['thor_prepared_hits'],1)
        self.source.write_text(source.replace('return value+1','return value+2'))
        report,errors=self.compile(status=1)
        self.assertEqual(report['thor_prepared_hits'],0)
        self.assertIn('VALUE==3',errors)
        self.source.write_text(source.replace('return value+1','_=context;return value+1'))
        report,errors=self.compile(status=1)
        self.assertEqual(report['thor_prepared_hits'],0)
        self.assertIn('ambient context',errors)

    def test_fn_transitive_proc_dependencies_are_revalidated_after_cache_hits(self):
        source='''package main
leaf::proc(value:int)->int{return value+1}
middle::proc(value:int)->int{return leaf(value)}
work::fn(value:int)->int{return middle(value)}
VALUE::work(2)
#assert(VALUE==3)
main::proc(){}
'''
        self.write(source);self.compile()
        report,_=self.compile()
        self.assertGreater(report['thor_cache']['hits'],0)
        self.source.write_text(source.replace('return value+1','return value+2'))
        _,errors=self.compile(status=1)
        self.assertIn('VALUE==3',errors)
        self.source.write_text(source.replace('return value+1','_=context;return value+1'))
        _,errors=self.compile(status=1)
        self.assertIn('ambient context',errors)

    def test_constant_type_and_overload_changes_invalidate(self):
        source='''package main
Item::int
INPUT::2
work::proc(value:Item)->int{return int(value)+1}
VALUE::#run work(INPUT)
#assert(VALUE==3)
main::proc(){}
'''
        self.write(source);self.compile()
        for old,new in [('INPUT::2','INPUT::3'),('Item::int','Item::i32')]:
            self.source.write_text(source.replace(old,new))
            report,_=self.compile(status=1 if old.startswith('INPUT') else 0)
            self.assertEqual(report['thor_prepared_hits'],0)

    def test_unchanged_bytes_after_editor_save_reuse_evaluation(self):
        self.write('package main\nwork::proc()->int{return 7}\nVALUE::#run work()\n#assert(VALUE==7)\nmain::proc(){}\n')
        self.compile()
        self.source.write_bytes(self.source.read_bytes())
        report,_=self.compile()
        self.assertGreater(report['thor_prepared_hits'],0)
        generated=next(self.output.rglob('main.odin'))
        generated.write_bytes(generated.read_bytes())
        report,_=self.compile()
        self.assertGreater(report['thor_prepared_hits'],0)

    def test_clear_removes_early_records_without_a_result_directory(self):
        cache=EvaluationCache(self.root/'cache')
        key='a'*64
        for namespace in ['prepared-v1','identity-v1','scalar-v1']:
            cache.write_record(namespace,key,{'test':True})
        unrelated=cache.directory.parent/'scalar-v1'/'notes.txt'
        unrelated.write_text('keep')
        self.assertFalse(cache.directory.exists())
        cache.clear()
        for namespace in ['prepared-v1','identity-v1','scalar-v1']:
            self.assertIsNone(cache.read_record(namespace,key))
        self.assertEqual(unrelated.read_text(),'keep')

    def test_new_runtime_shadow_cannot_reuse_a_constant_result(self):
        source='package main\nwork::proc(x:int)->int{return x+1}\nmain::proc(){N::2;VALUE::#run work(N);_=VALUE}\n'
        self.write(source);self.compile()
        self.source.write_text(source.replace('N::2','N:=2'))
        report,errors=self.compile(status=1)
        self.assertEqual(report['thor_prepared_hits'],0)
        self.assertIn('runtime storage is not a constant',errors)

    def test_new_library_privacy_cannot_be_hidden_by_a_cached_result(self):
        self.write('package main\nimport "library"\nVALUE::#run library.work()\nmain::proc(){}\n',
                   {'library/main.odin':'package library\nwork::proc()->int{return 7}\n'})
        self.compile()
        library=self.app/'library/main.odin'
        library.write_text('package library\n@private\nwork::proc()->int{return 7}\n')
        report,errors=self.compile(status=1)
        self.assertEqual(report['thor_prepared_hits'],0)
        self.assertIn('private',errors.lower())
        self.compile(status=1)

    def test_global_conditional_implementation_change_invalidates(self):
        source='''package main
when true { work::proc()->int{return 1} } else { work::proc()->int{return 2} }
VALUE::#run work()
#assert(VALUE==1)
main::proc(){}
'''
        self.write(source);self.compile()
        self.source.write_text(source.replace('when true','when false'))
        report,errors=self.compile(status=1)
        self.assertEqual(report['thor_prepared_hits'],0)
        self.assertIn('VALUE==1',errors)

    def test_distinct_scalar_results_preserve_their_nominal_type(self):
        source='''package main
Count::distinct int
work::proc()->Count{return 3}
VALUE::#run work()
#assert(type_of(VALUE)==Count)
main::proc(){}
'''
        self.write(source);self.compile()
        self.source.write_text(source.replace('main::proc(){}','main::proc(){s:=1;_=s}'))
        self.compile()

    def test_primitive_type_shadow_preserves_the_original_result_type(self):
        source='''package main
work::proc()->int{return 3}
main::proc(){VALUE::#run work();#assert(type_of(VALUE)==int)}
'''
        self.write(source);self.compile()
        self.source.write_text(source.replace('VALUE::','int::f64;VALUE::').replace('type_of(VALUE)==int','type_of(VALUE)!=int'))
        self.compile()

    def test_removing_a_local_conditional_binding_cannot_reuse_it(self):
        source='''package main
work::proc(value:int)->int{return value}
main::proc(){when true {INPUT::1};VALUE::#run work(INPUT);#assert(VALUE==1)}
'''
        self.write(source);self.compile()
        self.source.write_text(source.replace('when true','when false'))
        _,errors=self.compile(status=1)
        self.assertIn('INPUT',errors)


if __name__=='__main__':
    unittest.main()
