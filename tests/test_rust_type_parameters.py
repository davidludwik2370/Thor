"""Compile-time type arguments retain their lexical identity and value ABI."""
import unittest
from tests import test_rust_checked_calls as checked


class RustTypeParameterTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_explicit_public_scalar_types_and_named_arguments(self):
        self.tracked(self.program('''n:=3;f:=f64(4)
if identity(int,n)!=3||identity(f64,value=f)!=4||!identity(bool,true){panic("explicit types")}
''','''identity::fn($T:typeid,value:T)->T{return value}
'''))

    def test_explicit_helper_type_and_type_only_queries(self):
        self.tracked(self.program('n:=3;if work(n)!=size_of(int)+n{panic("typed helper")}', '''
helper::proc($T:typeid,value:T)->T{return value}
width::fn($T:typeid,n:int)->int{return size_of(T)+n}
work::fn(n:int)->int{return width(int,helper(int,n))}
'''))

    def test_explicit_slice_results_own_their_storage(self):
        self.tracked(self.program('''values :> []int{3,4}
result:=identity([]int,values);defer delete(result)
result[0]=9
if values[0]!=3||result[0]!=9{panic("typed owned result")}
''','''identity::fn($T:typeid,value:T)->T{return value}
'''))

    def test_grouped_type_parameters_and_multiple_owned_results(self):
        self.tracked(self.program('''n:=3;original:=[]int{n}
left,right:=pair(int,[]int,n,original);defer delete(right)
right[0]=9
if left!=3||original[0]!=3||right[0]!=9{panic("typed tuple")}
''','''pair::fn($T,$U:typeid,a:T,b:U)->(T,U){return a,b}
'''))

    def test_type_arguments_and_integer_arguments_select_distinct_overloads(self):
        self.tracked(self.program('n:=3;if work(n)!=9{panic("type/value overloads")}', '''
typed::proc($T:typeid,n:int)->int{_=size_of(T);return n}
valued::proc(value,n:int)->int{return value+n}
choose::proc{typed,valued}
work::fn(n:int)->int{return choose(int,n)+choose(n,n)}
'''))

    def test_explicit_types_keep_contextual_bracket_array_arguments(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("typed array context")}', '''
first::proc($T:typeid,values:[2]T)->T{return values[0]}
work::fn(n:int)->int{return first(int,[n,n+1])}
'''))

    def test_wrong_value_used_as_type_keeps_the_native_argument_diagnostic(self):
        self.compare(self.program('n:=3;_=identity(n,n)', '''
identity::fn($T:typeid,value:T)->T{return value}
'''),command='check',fail='Expected a type to assign to the type parameter')

    def test_runtime_typeid_values_remain_ineligible_pure_inputs(self):
        self.compare(self.program('n:=3;_=work(int,n)', '''
work::fn(T:typeid,n:int)->int{_=T;return n}
'''),command='check',fail='Unsupported pure value type')

    def test_explicit_slice_helpers_cannot_mutate_borrowed_inputs(self):
        self.compare(self.program('values:=[]int{3};_=work(values)', '''
change::proc($T:typeid,values:[]T){values[0]={}}
work::fn(values:[]int)->int{change(int,values);return values[0]}
'''),command='check',fail='borrowed from an input')

    def test_explicit_helpers_may_mutate_locally_created_slices_and_pointers(self):
        self.tracked(self.program('n:=3;if work(n)!=0{panic("typed local mutation")}', '''
clear_slice::proc($T:typeid,values:[]T){values[0]={}}
clear_pointer::proc($T:typeid,out:^T){out^={}}
work::fn(n:int)->int{values:=[]int{n};clear_slice(int,values);local:=n;clear_pointer(int,&local);return values[0]+local}
'''))

    def test_a_local_constant_shadows_the_outer_compile_time_argument(self):
        self.compare(self.program('n:=3;_=work(n)', '''
state:int
bad::proc()->int{return state}
helper::proc($N:int)->int{{N::0;when N==1{return 1}else{return bad()}}}
work::fn(n:int)->int{_=n;return helper(1)}
'''),command='check',fail="outside the function")

    def test_constant_bindings_do_not_leak_into_an_unrelated_callee(self):
        self.compare(self.program('n:=3;_=work(n)', '''
N::0
state:int
other::proc()->int{when N==1{return 1}else{return state}}
helper::proc($N:int)->int{return other()}
work::fn(n:int)->int{_=n;return helper(1)}
'''),command='check',fail="outside the function")

    def test_valid_shadowed_constants_select_the_actual_inactive_branch(self):
        self.tracked(self.program('n:=3;if work(n)!=7{panic("lexical constant")}', '''
state:int
helper::proc($N:int)->int{{N::1;when N==1{return 7}else{return state}}}
work::fn(n:int)->int{_=n;return helper(0)}
'''))

    def test_record_parameters_have_their_own_lexical_bindings(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("record type parameters")}', '''
helper::proc(value:$T)->int{
    Local::struct(T:typeid,N:int){items:[N]T}
    record:Local(f64,2)
    record.items[0]=3
    _=value
    return int(record.items[0])
}
work::fn(n:int)->int{return helper(n)}
'''))
