"""Native-selected generic branches check effects only when instantiated."""
import json
import unittest
from tests import test_rust_compiler as native


class RustConditionalPurityTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def test_pure_generic_function_ignores_inactive_runtime_storage(self):
        prefix='''state:int
work::fn(value:$T)->T{when T==int{return value}else{return T(state)}}
'''
        self.compare(self.program('n:=3;if work(n)!=3{panic("generic branch")}',prefix))
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')
        self.compare(self.program('',prefix),command='check')

    def test_generic_checked_helper_retains_ordinary_behavior(self):
        prefix='''state:int=7
helper::proc(value:$T)->T{when T==int{return value}else{return T(state)}}
work::fn(value:$T)->T{return helper(value)}
'''
        self.compare(self.program('n:=3;f:=f64(4);if work(n)!=3||helper(f)!=7{panic("checked branch")}',prefix))
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')

    def test_transitive_unsafe_callee_is_lazy_within_the_generic_branch(self):
        prefix='''state:int
leaf::proc()->int{return state}
helper::proc(value:$T)->int{when T==int{return value}else{return leaf()}}
work::fn(value:$T)->int{return helper(value)}
'''
        self.compare(self.program('n:=3;if work(n)!=3{panic("transitive lazy branch")}',prefix))
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')

    def test_conditional_expression_uses_the_same_effect_rules(self):
        prefix='state:int\nwork::fn(value:$T)->T{return value when T==int else T(state)}\n'
        self.compare(self.program('n:=3;if work(n)!=3{panic("conditional expression")}',prefix))
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')

    def test_inactive_io_is_not_compiled_as_a_pure_operation(self):
        prefix='''import "core:fmt"
work::fn(value:$T)->int{when T==int{return value}else{fmt.println(value);return 0}}
'''
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('n:=3;if work(n)!=3{panic("inactive I/O")}',prefix),flags=flags)
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='performs I/O')

    def test_captured_type_conditions_keep_their_native_scope(self):
        self.compare(self.program('n:=3;if outer(int,n)!=3{panic("captured type")}', '''
state:int
outer::proc($T:typeid,n:int)->int{
 inner::fn(value:int)->int{when T==int{return value}else{return state}}
 return inner(n)
}
'''))

    def test_unused_default_branch_records_a_reference_false_positive(self):
        prefix='''state:int
helper::proc(value:int=state)->int{return value}
work::fn(value:$T)->int{when T==int{return helper(value)}else{return helper()}}
'''
        # Recorded reference exception: the Python default checker combines
        # both call sites, then checks the unused default on helper(value).
        # Keep the frozen reference intact and require the correct native path.
        self.source.write_text(self.program('n:=3;if work(n)!=3{panic("conditional default")}',prefix))
        reference=self.invoke('python')
        self.assertEqual(reference.returncode,1,reference.stderr)
        self.assertIn('outside the function',reference.stderr)
        result=self.invoke('rust')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'')
        self.assertEqual(result.stderr,'')
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')

    def test_imported_checked_body_keeps_its_conditional_source_scope(self):
        prefix='''import "library"
work::fn(value:$T)->int{return library.helper(T,value)}
'''
        files={'library/helper.thor':'''package library
state:int
helper::proc($T:typeid,value:T)->int{when T==int{return value}else{return state}}
'''}
        self.compare(self.program('n:=3;if work(n)!=3{panic("imported condition")}',prefix),files=files)
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),files=files,
                     command='check',fail='outside the function')

    def test_failed_branch_reports_original_source_and_pure_call_chain(self):
        text=self.program('n:=f64(3);_=work(n)','''state:int
leaf::proc()->int{return state}
helper::proc(value:$T)->int{when T==int{return value}else{return leaf()}}
work::fn(value:$T)->int{return helper(value)}
''')
        self.source.write_text(text)
        result=self.invoke('rust',command='check',flags=['-json-errors'])
        self.assertEqual(result.returncode,1,result.stderr)
        errors=json.loads(result.stderr)['errors']
        primary=next(error for error in errors if 'outside the function' in str(error))
        self.assertEqual(primary['pos']['file'],str(self.source))
        self.assertIn(primary['pos']['offset'],[text.index('leaf()}',text.index('helper::')),
                                                text.index('helper(value)',text.index('work::'))])
        self.assertTrue(primary.get('related'),primary)

    def test_conditional_first_visit_does_not_hide_an_unconditional_call(self):
        self.compare(self.program('n:=3;_=work(n)','''state:int
leaf::proc()->int{return state}
work::fn(value:$T)->int{when T==f64{_=leaf()};return leaf()}
'''),command='check',fail='outside the function')

    def test_separate_unknown_conditions_each_keep_their_callee_check(self):
        prefix='''state:int
leaf::proc()->int{return state}
work::fn(value:$T)->int{
 when T==f64{_=leaf()}
 when T==int{return leaf()}
 return 0
}
'''
        self.compare(self.program('n:=3;_=work(n)',prefix),command='check',fail='outside the function')

    def test_transitive_helpers_do_not_share_checks_between_distinct_conditions(self):
        prefix='''state:int
leaf::proc()->int{return state}
forward::proc()->int{return leaf()}
work::fn(value:$T)->int{
 when T==f64{_=forward()}
 when T==int{return forward()}
 return 0
}
'''
        self.compare(self.program('n:=3;_=work(n)',prefix),command='check',fail='outside the function')


if __name__=='__main__':unittest.main()
