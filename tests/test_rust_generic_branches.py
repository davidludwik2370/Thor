"""Generic native conditions whose arms can all be verified safely."""
import unittest
from tests import test_rust_checked_calls as checked


class RustGenericBranchTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_public_generic_conditions_keep_checked_callees_and_scalar_types(self):
        self.tracked(self.program('n:=3;f:=f64(4);if work(n)!=4||work(f)!=4{panic("generic when")}', '''
identity::proc(value:$T)->T{return value}
work::fn(value:$T)->T{when T==int{return identity(value)+1}else{return value}}
'''))

    def test_generic_helpers_can_have_all_eligible_arms(self):
        self.tracked(self.program('n:=3;f:=f64(4);if work(n)!=4||work(f)!=6{panic("checked generic when")}', '''
helper::proc(value:$T)->T{when T==int{return value+1}else{return value+2}}
work::fn(value:$T)->T{return helper(value)}
'''))

    def test_owned_results_remain_independent_across_generic_branches(self):
        self.tracked(self.program('''values :> []int{3};copied:=work(values);defer delete(copied)
copied[0]=9
n:=3;if values[0]!=3||copied[0]!=9||work(n)!=3{panic("conditional ownership")}
''', '''
import "thor:pure"
identity::proc(value:$T)->T{return pure.clone(value)}
work::fn(value:$T)->T{when T==[]int{return identity(value)}else{return value}}
'''))

    def test_compile_time_value_conditions_are_checked_without_value_capture(self):
        self.tracked(self.program('n:=3;if work(2,n)!=4||work(3,n)!=5{panic("value condition")}', '''
work::fn($N:int,value:int)->int{when N%2==0{return value+1}else{return value+2}}
'''))

    def test_conditional_expressions_keep_native_constant_selection(self):
        self.tracked(self.program('n:=3;f:=f64(4);if work(n)!=4||work(f)!=4{panic("when expression")}', '''
work::fn(value:$T)->T{return value+1 when T==int else value}
'''))

    def test_type_comparisons_do_not_observe_runtime_pointer_identity(self):
        self.tracked(self.program('n:=3;if outer(^int,n)!=3||outer(int,n)!=0{panic("type comparison")}', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{when T==^int{return value}else{return 0}}
    return inner(n)
}
'''))

    def test_inactive_type_specific_code_is_left_to_native_selection(self):
        self.tracked(self.program('n:=3;if work(n)!=4{panic("inactive type-specific arm")}', '''
work::fn(value:$T)->int{when T==int{return value+1}else{return int(value.amount)}}
'''))
        self.compare(self.program('v:=Holder{7};if work(v)!=7{panic("record arm")}', '''
Holder::struct{amount:i16}
work::fn(value:$T)->int{when T==int{return value+1}else{return int(value.amount)}}
'''))

    def test_runtime_state_in_the_condition_is_an_actual_purity_error(self):
        self.compare(self.program('n:=3;_=work(n)', '''
state:int
work::fn(value:$T)->T{when state==0{return value}else{return value}}
'''),command='check',fail='outside the function')

    def test_native_selection_defers_inactive_purity_violations(self):
        prefix='''
state:int
work::fn(value:$T)->T{when T==int{return value}else{return T(state)}}
'''
        self.compare(self.program('n:=3;_=work(n)',prefix),command='check')
        self.compare(self.program('n:=f64(3);_=work(n)',prefix),command='check',fail='outside the function')


if __name__=='__main__':unittest.main()
