"""Caller-local nominal types retain native identity after isolated evaluation."""
import unittest
from tests import test_rust_scheduling as scheduling
from tests import test_rust_compiler as native

class RustLocalEvaluationTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(scheduling.RustSchedulingTests.setUpClass.__func__)
    compare=scheduling.RustSchedulingTests.compare
    program=scheduling.RustSchedulingTests.program

    def test_two_local_nominal_types_in_checked_and_unchecked_programs(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('A::struct{x:int};B::struct{x,y:int};fmt.println(generic(A),generic(B))',
                    'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
                    'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),flags=flags,output='9 17\n',
                    reference_failure='Cannot resolve the nominal type' if flags==native.UNCHECKED else None)

    def test_distinct_local_types_with_the_same_shape(self):
        self.compare(self.program('A::distinct int;B::distinct int;fmt.println(generic(A),generic(B))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),output='9 9\n',
            reference_failure='Undeclared name: A',reference_specialized_gap=[{'kind':'integer','value':'9'},{'kind':'integer','value':'9'}])

    def test_equal_shaped_local_records_keep_distinct_native_identity(self):
        self.compare(self.program('A::struct{x:int};B::struct{x:int};fmt.println(generic(A,A),generic(A,B))',
            'import "core:fmt"\nwork::proc($T:typeid,$U:typeid)->int{when T==U{return 1}else{return 2}}\n'
            'generic::proc($T:typeid,$U:typeid)->int{return #run work(T,U)}'),output='1 2\n')

    def test_nested_scopes_can_shadow_the_local_type_name(self):
        self.compare(self.program('{Local::struct{x:int};fmt.println(generic(Local))};{Local::struct{x,y:int};fmt.println(generic(Local))}',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),output='9\n17\n')

    def test_private_caller_type_does_not_require_a_user_reverse_import(self):
        self.compare(self.program('Local::struct{x:int};fmt.println(lib.generic(Local))',
            'import "core:fmt"\nimport lib "lib"'),files={
                'lib/lib.thor':'package lib\nwork::proc(n:int)->int{return n+1}\ngeneric::proc($T:typeid)->int{return #run work(size_of(T))}\n'
            },output='9\n')

    def test_local_type_captures_the_enclosing_constant_specialization(self):
        self.compare(self.program('fmt.println(outer(2),outer(4))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}\n'
            'outer::proc($N:int)->int{Local::struct{values:[N]int};return generic(Local)}'),output='17 33\n',
            reference_specialized_gap={'reference':[{'kind':'integer','value':'17'}],
                                       'rust':[{'kind':'integer','value':'17'},{'kind':'integer','value':'33'}]})

    def test_structural_sequence_of_local_nominal_elements(self):
        self.compare(self.program('Local::struct{x:int};fmt.println(generic([3]Local))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),output='25\n')

    def test_local_generic_record_type(self):
        self.compare(self.program('Local::struct($E:typeid){value:E};fmt.println(generic(Local(int)),generic(Local(i16)))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),output='9 3\n')

    def test_local_type_with_unicode_names_and_neighboring_declarations(self):
        self.compare(self.program('text:="é🙂";_=text;型::struct{値:int};fmt.println(generic(型))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),output='9\n')

if __name__=='__main__':
    unittest.main()
