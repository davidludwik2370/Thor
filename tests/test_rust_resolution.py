"""Type-resolution parity: overload effects, substitutions and conditional symbols."""
import unittest
from tests import test_rust_compiler as native


class RustResolutionTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare
    program = native.RustCompilerTests.program

    def test_slice_overloads_ignore_mutating_other_element_type(self):
        self.compare(self.program('values :> []int{7};if use(values)!=7{panic("overload")}', '''
read::proc(values:[]int)->int{return values[0]}
write::proc(values:[]f64)->int{values[0]=3;return 0}
use::proc{read,write}
'''))

    def test_nested_container_and_array_length_overloads(self):
        for read_type, other_type, literal, result in [
            ('[1][]int','[2][]int','[1][]int{[]int{7}}','values[0][0]'),
            ('[][]int','[][]f64','[][]int{[]int{7}}','values[0][0]'),
        ]:
            with self.subTest(read_type=read_type):
                self.compare(self.program('values :> '+literal+';if use(values)!=7{panic("overload")}',
                    'read::proc(values:'+read_type+')->int{return '+result+'}\n'+
                    'write::proc(values:'+other_type+')->int{values[0][0]=3;return 0}\nuse::proc{read,write}\n'))

    def test_record_overloads_keep_nominal_identity(self):
        self.compare(self.program('values :> A{[]int{7}};if use(values)!=7{panic("record overload")}', '''
A::struct{data:[]int}
B::struct{data:[]int}
read::proc(values:A)->int{return values.data[0]}
write::proc(values:B)->int{values.data[0]=3;return 0}
use::proc{read,write}
'''))

    def test_named_arguments_select_overload_and_preserve_pure_effects(self):
        source=self.program('n:=7;if work(n)!=7{panic("named overload")}', '''
import "core:fmt"
read::proc(value:int, label:string)->int{_=label;return value}
write::proc(value:string, label:int)->int{fmt.println(value);return label}
use::proc{read,write}
work::fn(n:int)->int{return use(label="ok",value=n)}
''')
        self.compare(source,flags=['-debug'])

    def test_arity_selects_pure_overload_before_checking_effects(self):
        self.compare(self.program('n:=7;if work(n)!=7{panic("arity")}', '''
import "core:fmt"
read::proc(value:int)->int{return value}
write::proc(value:int,other:int)->int{fmt.println(other);return value}
use::proc{read,write}
work::fn(n:int)->int{return use(n)}
'''),flags=['-debug'])

    def test_concrete_slice_overload_takes_priority_over_generic_effects(self):
        self.compare(self.program('n:=7;if work(n)!=7{panic("concrete overload")}', '''
import "core:fmt"
read::proc(values:[]int)->int{return values[0]}
generic::proc(values:[]$T)->int{fmt.println(values);return 0}
use::proc{read,generic}
work::fn(n:int)->int{values:=[n];return use(values[:])}
'''),flags=['-debug'])

    def test_generic_result_shapes_survive_nested_calls(self):
        code=self.program('n:=3;values:=build(n);defer delete(values);if values[-1]!=9{panic("generic result")}', '''
identity::proc(value:$T)->T{return value}
build::proc(n:int)->[]int{values:=make([]int,n);identity(values)[-1]=9;return identity(values)}
''')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags)

    def test_local_records_capture_only_their_lexical_generic_parameters(self):
        code=self.program('''numbers:=[3];floats:=[f64(4)]
if read(numbers[:])!=3||.5**read(floats[:])!=.0625{panic("lexical generic record")}
''','''read::proc(values:[]$T)->T{
Local::struct{value:T}
original :> Local{values[0]}
copy:=original.value
return copy
}
''')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags)

    def test_record_type_and_count_arguments_flow_into_result_dimensions(self):
        code=self.program('box:=Box(2,int){values={1,2}};values:=get(box);if values[-1]!=2{panic("record dimensions")}', '''
Box::struct($N:int,$T:typeid){values:[N]T}
get::proc(box:Box($N,$T))->[N]T{return box.values}
''')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags)

    def test_constant_alias_selects_conditional_definition(self):
        for flag in ('true','false'):
            with self.subTest(flag=flag):
                self.compare(self.program('values :> []int{7};_=read(values)', '''
CHOICE::#config(READ,true)
when CHOICE {read::proc(values:[]int)->int{return values[0]}}
else {read::proc(values:[]int)->int{return len(values)}}
'''),flags=['-define:READ='+flag])

    def test_promoted_generic_members_keep_element_type(self):
        self.compare(self.program('n:=3;box:=Box(int){Base(int){make([]int,n)}};defer delete(box.data);box.data[-1]=9;if box.data[-1]!=9{panic("using field")}', '''
Base::struct($T:typeid){data:[]T}
Box::struct($T:typeid){using base:Base(T)}
'''))

    def test_promoted_member_storage_paths_keep_readonly_origins(self):
        for member in ('data','base.data'):
            with self.subTest(member=member):
                self.compare(self.program('box :> Box(int){Base(int){[]int{7}}};box.'+member+'[0]=9', '''
Base::struct($T:typeid){data:[]T}
Box::struct($T:typeid){using base:Base(T)}
'''),command='check',fail='read-only slice')

    def test_selected_mutating_overload_is_still_rejected(self):
        self.compare(self.program('values :> []f64{7};_=use(values)', '''
read::proc(values:[]int)->int{return values[0]}
write::proc(values:[]f64)->int{values[0]=3;return 0}
use::proc{read,write}
'''),command='check',fail='read-only slice')

    def test_generic_enum_constants_select_effects(self):
        prefix='''Mode::enum{read,write}
use::proc(values:[]int,$mode:Mode){when mode==.write {values[0]=9}else{_=values[0]}}
'''
        self.compare(self.program('values :> []int{7};use(values,.read)',prefix))
        self.compare(self.program('values :> []int{7};use(values,.write)',prefix),command='check',fail='read-only slice')

    def test_pure_generic_conditions_follow_concrete_call_arguments(self):
        self.compare(self.program('n:=3;if work(n)!=3{panic("generic condition")}', '''
import "core:fmt"
helper::proc(value:$T)->int{when T==int{return value}else{fmt.println(value);return 0}}
work::fn(n:int)->int{return helper(n)}
'''),flags=['-debug'])

    def test_generic_power_result_has_concrete_operand_type(self):
        code=self.program('n:=f64(3);if .5**identity(n)!=.125{panic("generic result type")}',
                          'identity::proc(value:$T)->T{return value}\n')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags)

    def test_context_witnesses_preserve_shadowed_types_and_do_not_execute_arguments(self):
        code=self.program('''Color:=42;calls:=0
value:=choose([.A,.B],hint(&calls))
if value!=Alias.B||Color!=42||calls!=1{panic("context witness evaluation")}
''','''Color::enum{A,B}
Alias::Color
hint::proc(calls:^int)->Alias{calls^+=1;return .A}
choose::proc(values:[2]$T,other:T)->T{_=other;return values[1]}
''')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.compare(code,flags=flags)

    def test_native_local_package_can_supply_private_contextual_type(self):
        self.compare(self.program('if library.choose([.Red,.Blue])!=1||ANSWER!=9{panic("native context")}',
                                  'import "library"\nsquare::proc(n:int)->int{return n*n}\nANSWER::#run square(3)\n'),files={'library/main.odin':'''package library
@(private="file") Color::enum{Red,Blue}
choose::proc(values:[2]Color)->int{return int(values[1])}
'''})

    def test_inactive_first_file_does_not_hide_active_conditional_symbol(self):
        self.compare(self.program('if choose([.A,.B])!=1{panic("conditional package")}',
                                  ''),directory=True,files={
            'a.thor':'package main\nwhen false {choose::proc(values:[2]int)->int{return values[0]}}\n',
            'b.thor':'package main\nColor::enum{A,B}\nwhen true {choose::proc(values:[2]Color)->int{return int(values[1])}}\n',
        })

    def test_multiple_wildcard_imports_find_the_package_containing_the_name(self):
        self.compare(self.program('values :> []int{7};if read(values)!=7{panic("wildcards")}',
                                  'import * "first"\nimport * "second"\n'),files={
            'first/main.thor':'package first\nother::proc()->int{return 0}\n',
            'second/main.thor':'package second\nread::proc(values:[]int)->int{return values[0]}\n',
        })
