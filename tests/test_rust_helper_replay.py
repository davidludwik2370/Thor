"""Installed results use the declarations and type identities of each helper."""
import unittest
from tests import test_rust_scheduling as scheduling
from tests import test_rust_compiler as native

class RustHelperReplayTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(scheduling.RustSchedulingTests.setUpClass.__func__)
    compare=scheduling.RustSchedulingTests.compare
    program=scheduling.RustSchedulingTests.program

    scalar_prefix=('import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
                   'generic::proc($T:typeid)->int{VALUE::#run work(size_of(T));return VALUE}')
    reference_local_error='requires resolved generic types and constant parameters'

    def test_unchecked_generic_branch_needs_no_local_type_identity(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('Local::struct{x:int};fmt.println(generic(Local,false))',
                    'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
                    'generic::proc($T:typeid,$B:bool)->int{when B{return #run work(size_of(T))}else{return 0}}'),
                    flags=flags,output='0\n',reference_failure='Illegal character NUL')

    def test_inactive_runtime_branch_can_probe_an_unexecuted_local_type(self):
        values=self.compare(self.program('Local::struct{x:int};fmt.println(generic(Local,false))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid,$B:bool)->int{if B{return #run work(size_of(T))};return 0}'),
            output='0\n',reference_failure='Illegal character NUL')
        self.assertEqual(values,[])

    def test_active_generic_runtime_condition_keeps_its_local_binding(self):
        self.compare(self.program('Local::struct{x:int};fmt.println(generic(Local,true))',
            'import "core:fmt"\nwork::proc(n:int)->int{return n+1}\n'
            'generic::proc($T:typeid,$B:bool)->int{if B{return #run work(size_of(T))};return 0}'),
            output='9\n',reference_failure='Illegal character NUL')

    def test_identity_probe_preserves_an_object_build_context(self):
        self.compare(self.program('Local::struct{x:int};_=generic(Local)',
            'work::proc(n:int)->int{when ODIN_BUILD_MODE==.Object{return n+10}else{return n+1}}\n'
            'generic::proc($T:typeid)->int{return #run work(size_of(T))}'),flags=['-debug','-build-mode:obj'])

    def test_nested_local_type_request_is_rebound_in_each_helper(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                values=self.compare(self.program('Local::struct{x:int};fmt.println(#run generic(Local))',self.scalar_prefix),
                    flags=flags,output='9\n',reference_failure=self.reference_local_error)
                self.assertEqual([packet for _,packet in values],[{'kind':'integer','value':'9'}])

    def test_scope_identity_mapping_settles_after_position_digit_changes(self):
        padding='//' + '.'*8400 + '\n'
        values=self.compare(self.program('Local::struct{x:int};fmt.println(#run generic(Local))',padding+self.scalar_prefix),
            output='9\n',reference_failure=self.reference_local_error)
        self.assertEqual([packet for _,packet in values],[{'kind':'integer','value':'9'}])

    def test_unselected_global_type_case_does_not_need_a_pruned_definition(self):
        self.compare(self.program('a:=generic(A);b:=generic(B);c:=#run generic(A);fmt.println(a,b,c)',
            'A::struct{x:int}\nB::struct{x,y:int}\n'+self.scalar_prefix),output='9 17 9\n')

    def test_nested_owned_results_remain_independent_for_local_elements(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                values=self.compare(self.program('Local::struct{x:int};a:=#run generic(Local);b:=#run generic(Local);a[0].x=7;fmt.println(a[0].x,b[0].x,len(b));delete(a);delete(b)',
                    'import "core:fmt"\nwork::proc($T:typeid)->[]T{a:=make([]T,2);return a}\n'
                    'generic::proc($T:typeid)->[]T{return #run work(T)}'),flags=flags,output='7 0 2\n',
                    reference_failure=self.reference_local_error)
                self.assertEqual(len(values),2)
                self.assertTrue(all(packet['kind']=='sequence' and len(packet['values'])==2 for _,packet in values))

    def test_unevaluated_runtime_value_supplies_only_its_local_type(self):
        values=self.compare(self.program('Local::struct{x:int};value:=Local{x=5};fmt.println(#run generic(type_of(value)))',self.scalar_prefix),
            output='9\n',reference_failure=self.reference_local_error)
        self.assertEqual([packet for _,packet in values],[{'kind':'integer','value':'9'}])

if __name__=='__main__':
    unittest.main()
