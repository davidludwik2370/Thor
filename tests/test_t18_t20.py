"""Pure runtime calls and native, contents-preserving explicit #run acceptance."""
import json
import unittest
from tests import test_thor as base
from thorlang import values


class PureTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def memory(self,body,prefix=''):
        return self.invoke('package main\nimport "core:testing"\n'+prefix+
            '\n@(test) lifetime::proc(t:^testing.T){\n'+body+'\n}\n',command='test',
            flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_fn_runtime_and_run_constant_array_size(self):
        self.invoke(self.program('a:[SIZE]int;assert(len(a)==64&&square(3)==9)',
            'square::fn(x:int)->int{return x*x}\nSIZE::#run square(8)\n'))

    def test_run_ordinary_transitive_and_mutual_recursion(self):
        self.invoke(self.program('assert(VALUE==13&&EVEN)', '''
fib::proc(n:int)->int{if n<2{return n};return fib(n-1)+fib(n-2)}
even::proc(n:int)->bool{return n==0 ? true : odd(n-1)}
odd::proc(n:int)->bool{return n==0 ? false : even(n-1)}
VALUE::#run fib(7)
EVEN::#run even(10)
'''))
        self.invoke(self.program('assert(VALUE==9)','square::proc "contextless"(n:int)->int{return n*n}\nVALUE::#run square(3)\n'))

    def test_fn_recursive_generic_and_overload(self):
        self.invoke(self.program('assert(VALUE==25&&recursive(5)==120)', '''
square_int::fn(x:int)->int{return x*x}
square_float::fn(x:f64)->f64{return x*x}
square::proc{square_int,square_float}
generic::fn(x:$T)->T{return x*x}
recursive::fn(n:int)->int{if n<=1{return 1};return n*recursive(n-1)}
VALUE::#run generic(5)
OTHER::#run square(f64(2))
'''))

    def test_local_constants_named_arguments_and_dependent_runs(self):
        self.invoke(self.program('''
N::7
FIRST::#run add(a=N,b=3)
SECOND::#run add(FIRST,2)
assert(FIRST==10&&SECOND==12)
''','add::fn(a,b:int)->int{return a+b}\n'))

    def test_fixed_arrays_and_records_round_trip(self):
        self.invoke(self.program('''
pair:=#run pair_of(7)
record:=#run make_record(5)
assert(pair==[7,8]&&record.value==5&&record.pair[1]==6)
''','''
Record::struct{value:int,pair:[2]int}
pair_of::fn(x:int)->[2]int{return [x,x+1]}
make_record::fn(x:int)->Record{return Record{x,[x,x+1]}}
'''))
        self.invoke(self.program('values:=VALUES;assert(values==[i16(7),7,7])','''
repeat::fn($N:int,value:$T)->[N]T{result:[N]T;for i in 0..<N{result[i]=value};return result}
VALUES::#run repeat(3,i16(7))
'''))

    def test_wide_integer_enum_and_float_bits(self):
        self.invoke(self.program('''
assert(WIDE==(u128(1)<<120)+17)
assert(COLOR==.Blue)
assert(transmute(u64)ZERO==0x8000000000000000)
''','''
Color::enum u8{Red,Blue}
wide::fn()->u128{return (u128(1)<<120)+17}
color::fn()->Color{return .Blue}
negative_zero::proc()->f64{return -f64(0)}
WIDE::#run wide()
COLOR::#run color()
ZERO::#run negative_zero()
'''))

    def test_string_bytes_and_slice_inputs(self):
        self.invoke(self.program('assert(TEXT==3&&TOTAL==6)', '''
byte_sum::fn(text:string)->int{n:=0;for byte in text{n+=int(byte)};return n}
sum::fn(data:[3]int)->int{n:=0;for value in data{n+=value};return n}
TEXT::#run byte_sum("\\x00\\x01\\x02")
TOTAL::#run sum([1,2,3])
'''))

    def test_runtime_scratch_allocation_and_early_exits(self):
        self.memory('for i in 0..<8{testing.expect(t,work("input",i)==i+5)}', '''
work::fn(input:string,n:int)->int{
 data:=make([]int,5)
 original:=[1,2,3,4,5]
 copy(data,original[:])
 data[0]=n
 text:=f"value {n}"
 if n==0{return len(input)}
 return data[0]+len(input)
}
''')

    def test_local_array_copy_can_mutate_without_changing_input(self):
        self.invoke(self.program('a:=[1,2];assert(work(a)==9&&a[0]==1)',
            'work::fn(input:[2]int)->int{copy:=input;copy[0]=9;return copy[0]}\n'))

    def test_borrowed_aliases_fields_and_subslices_rejected(self):
        cases=[
            ('bad::fn(input:[]int)->int{input[0]=9;return 0}', 'a:=[1,2];_=bad(a[:])'),
            ('bad::fn(input:[]int)->int{alias:=input;alias[0]=9;return 0}', 'a:=[1,2];_=bad(a[:])'),
            ('bad::fn(input:[]int)->int{alias:=input[1:];alias[0]+=1;return 0}', 'a:=[1,2];_=bad(a[:])'),
            ('R::struct{data:[]int}\nbad::fn(input:R)->int{copy:=input;copy.data[0]=9;return 0}', 'a:=[1,2];_=bad(R{a[:]})'),
            ('bad::fn(input:[]int)->int{alias:[]int;alias=input;alias[0]=9;return 0}', 'a:=[1,2];_=bad(a[:])'),
        ]
        for prefix,body in cases:
            with self.subTest(prefix=prefix):self.invoke(self.program(body,prefix+'\n'),command='check',fail='borrowed from an input')

    def test_pure_calls_cannot_hide_mutation_through_callee(self):
        self.invoke(self.program('a:=[1,2];_=outer(a[:])','''
inner::fn(input:[]int)->int{input[0]=9;return 0}
outer::fn(input:[]int)->int{return inner(input)}
'''),command='check',fail='borrowed from an input')

    def test_runtime_globals_context_foreign_and_indirect_calls_rejected(self):
        cases=[
            ('global:int\nbad::fn()->int{return global}', 'outside the function'),
            ('global:>7\nbad::fn()->int{return global}', 'outside the function'),
            ('bad::fn()->int{_=context;return 0}', 'ambient context'),
            ('ordinary::proc()->int{return 7}\nbad::fn()->int{return ordinary()}', 'only declared fn'),
            ('bad::fn()->int{p:=ordinary;return p()}\nordinary::proc()->int{return 7}', 'indirect'),
            ('bad::fn()->int{x:=7;_= &x;return x}', 'raw addresses'),
            ('bad::fn()->u64{return transmute(u64)f64(1)}', 'transmute'),
            ('bad::fn()->int{value:int=---;return value}', 'uninitialized storage'),
            ('global:int\nnotify::proc(){global+=1}\n@(deferred_none=notify) bad::fn()->int{return 7}', 'implicit deferred procedure callbacks'),
            ('bad::fn()->int{return len(#file)}', 'Source-location-dependent constants'),
            ('FILE::#file\nbad::fn()->int{return len(FILE)}', 'Source-location-dependent constants'),
        ]
        for prefix,message in cases:
            with self.subTest(prefix=prefix):self.invoke(self.program('_=bad()',prefix+'\n'),command='check',fail=message)

    def test_contextless_results_reject_strings_and_nested_slices(self):
        for prefix in [
            'bad::proc "contextless"()->string{return "hello"}',
            'R::struct{data:[]int}\nbad::proc "contextless"()->R{return {}}',
        ]:
            with self.subTest(prefix=prefix):self.invoke(self.program('_=VALUE',prefix+'\nVALUE::#run bad()\n'),command='check',fail='Contextless compile-time procedures cannot return strings/slices')

    def test_input_types_reject_pointers_and_any_recursively(self):
        for prefix,body in [
            ('bad::fn(p:^int)->int{return 0}', 'x:=1;_=bad(&x)'),
            ('bad::fn(p:any)->int{return 0}', '_=bad(7)'),
            ('R::struct{p:^int}\nbad::fn(p:R)->int{return 0}', '_=bad(R{})'),
        ]:
            with self.subTest(prefix=prefix):self.invoke(self.program(body,prefix+'\n'),command='check',fail='Unsupported pure value type')

    def test_local_boxing_pointer_records_and_maps_cannot_bypass_purity(self):
        cases=[
            'bad::fn()->int{x:any=7;_=x;return 0}',
            'Box::struct{value:any}\nbad::fn()->int{x:=Box{7};_=x;return 0}',
            'bad::fn()->int{x:=map[int]int{1=7};return len(x)}',
            'bad::fn()->int{x:=set[int]{1,2};return len(x)}',
            'Box::struct{pointer:rawptr}\nbad::fn()->int{x:Box;_=x;return 0}',
        ]
        for prefix in cases:
            with self.subTest(prefix=prefix):self.invoke(self.program('_=bad()',prefix+'\n'),command='check',fail='Pure local storage')
        self.invoke(self.program('_=bad(any)','bad::fn($T:typeid)->int{x:T;_=x;return 0}\n'),command='check',fail='Unsupported pure value type')

    def test_run_rejects_runtime_arguments(self):
        self.invoke(self.program('n:=7;_= #run square(n)','square::fn(x:int)->int{return x*x}\n'),command='check',fail='compile-time known')

    def test_run_rejects_impure_ordinary_graph(self):
        self.invoke(self.program('_=VALUE','global:int\nread::proc()->int{return global}\nVALUE::#run read()\n'),command='check',fail='outside the function')

    def test_inactive_and_lazy_paths_do_not_execute(self):
        self.invoke(self.program('''
when false {BAD::#run explode()}
when false {ALSO_BAD::#run missing_procedure(missing_argument)}
VALUE::7 when true else #run explode()
OTHER::9 when true else #run missing_procedure()
_=false&&#run explode_bool()
assert(VALUE==7)
''','''
explode::fn()->int{assert(false);return 0}
explode_bool::fn()->bool{assert(false);return false}
'''))

    def test_compile_switch_selection_and_defines(self):
        source=self.program('''
VALUE::#switch MODE{case 1:#run square(3);case:#run square(4)}
assert(VALUE==(MODE==1 ? 9 : 16))
''','MODE::#config(MODE,1)\nsquare::fn(x:int)->int{return x*x}\n')
        for mode in [1,2]:
            with self.subTest(mode=mode):self.invoke(source,flags=[f'-define:MODE={mode}'])
        self.invoke(source.replace('#config(MODE,1)','#config(ODIN_TEST_USER,1)'),flags=['-define:ODIN_TEST_USER=2'])

    def test_run_panic_and_timeout_are_compiler_errors(self):
        self.invoke(self.program('_=VALUE','bad::fn()->int{assert(false,"oops");return 0}\nVALUE::#run bad()\n'),command='check',fail='#run execution failed')
        self.invoke(self.program('_=VALUE','spin::fn()->int{for{};return 0}\nVALUE::#run spin()\n'),command='check',flags=['--run-timeout','0.05'],fail='timeout')

    def test_failed_scratch_allocation_cannot_become_a_successful_result(self):
        self.invoke(self.program('context.allocator={procedure=fail_allocate};_=work()', '''
import "core:mem"
fail_allocate::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old:rawptr,old_size:int,loc:=#caller_location)->([]byte,mem.Allocator_Error){return nil,.Out_Of_Memory}
work::fn()->int{data:=make([]int,5);return len(data)}
'''),fail='Pure scratch allocation failed')

    def test_run_cycles_cross_target_and_timeout_configuration(self):
        self.invoke(self.program('_=FIRST','work::fn(n:int)->int{return n}\nFIRST::#run work(SECOND)\nSECOND::#run work(FIRST)\n'),command='check',fail='Cyclic #run')
        self.invoke(self.program('_=VALUE','work::fn()->int{return 7}\nVALUE::#run work()\n'),command='check',flags=['-target:linux_amd64'],fail='native targets only')
        self.invoke(self.program(''),command='check',flags=['--run-timeout','0'],fail='--run-timeout must be')

    def test_run_imports_and_selective_aliases(self):
        self.invoke(self.program('assert(VALUE==25)','import "lib" / square\nVALUE::#run square(5)\n'),
            files={'lib/main.thor':'package lib\nsquare::fn(x:int)->int{return x*x}\n'})

    def test_unrelated_initializers_are_not_executed_by_helper(self):
        self.invoke(self.program('assert(VALUE==9)','''
@(init) initialize::proc "contextless"(){assert_contextless(false,"must not run while checking")}
square::proc(x:int)->int{return x*x}
VALUE::#run square(3)
'''),command='check')

    def test_actual_slice_inputs_and_nil_empty_distinction(self):
        self.invoke('#+feature dynamic-literals\n'+self.program('assert(TOTAL==6&&NIL&&EMPTY)', '''
sum::fn(data:[]int)->int{n:=0;for value in data{n+=value};return n}
is_nil::fn(data:[]int)->bool{return data==nil}
TOTAL::#run sum([]int{1,2,3})
NIL::#run is_nil(cast([]int)nil)
EMPTY::#run is_nil(make([]int,0))
'''))

    def test_borrowed_nested_aggregate_loop_and_union_aliases(self):
        cases=[
            ('bad::fn(input:[][]int)->int{for row in input{row[0]=9};return 0}', 'a:=[1,2];rows:=[a[:]];_=bad(rows[:])'),
            ('R::struct{data:[]int}\nbad::fn(input:[]R)->int{for row in input{row.data[0]=9};return 0}', 'a:=[1,2];rows:=[R{a[:]}];_=bad(rows[:])'),
            ('R::struct{data:[]int}\nOuter::struct{inner:R}\nbad::fn(input:[]int)->int{local:Outer={{input}};local.inner.data[0]=9;return 0}', 'a:=[1,2];_=bad(a[:])'),
            ('U::union{[]int,int}\nbad::fn(input:U)->int{switch value in input{case []int:value[0]=9};return 0}', 'a:=[1,2];_=bad(U(a[:]))'),
            ('U::union{[]int,int}\nbad::fn(input:U)->int{value,ok:=input.([]int);if ok{value[0]=9};return 0}', 'a:=[1,2];_=bad(U(a[:]))'),
            ('U::union{[]int,int}\nbad::fn(input:U)->int{value:=input.([]int) or_else nil;value[0]=9;return 0}', 'a:=[1,2];_=bad(U(a[:]))'),
        ]
        for prefix,body in cases:
            with self.subTest(prefix=prefix):self.invoke(self.program(body,prefix+'\n'),command='check',fail='borrowed from an input')

    def test_generic_borrow_guards_and_loop_post(self):
        prefix='work::fn(input:$T)->int{local:=input;for i:=0;i<1;local[0]+=1{i+=1};return local[0]}\n'
        self.invoke(self.program('a:=[1,2];assert(work(a)==2&&a[0]==1)',prefix))
        self.invoke(self.program('a:=[1,2];_=work(a[:])',prefix),command='check',fail='borrowed from an input')
        prefix='work::fn(input:$T)->int{when T==int{return input+1}else{return len(input)}}\n'
        self.invoke(self.program('assert(VALUE==8)',prefix+'VALUE::#run work(7)\n'))

    def test_fn_is_contextual_and_void_functions_work_at_runtime(self):
        self.invoke(self.program('fn:=ordinary;fn();pure_void();assert(VALUE==9)', '''
ordinary::proc(){}
pure_void::fn(){data:=make([]int,1);data[0]=1}
square::fn /* signature comment */ (
 value:int,
) -> int {return value*value}
VALUE::#run square(3)
'''))

    def test_defaults_and_explicit_allocator_overrides_rejected(self):
        cases=[
            ('bad::fn(n:int=context.user_index)->int{return n}', 'ambient context'),
            ('global:int\nbad::fn(n:int=global)->int{return n}', 'outside the function'),
            ('bad::fn(n:int=7)->int{_=make([]int,n,context.allocator);return n}', 'allocator'),
            ('import "core:strings"\nbad::fn(n:int=7)->int{_=strings.clone("a",allocator=context.allocator);return n}', 'allocator'),
            ('R::struct{value:int}\nbad::fn(n:int=7)->int{_=f"{R{n}}";return n}', 'deterministic scalar/string'),
        ]
        for prefix,message in cases:
            with self.subTest(prefix=prefix):self.invoke(self.program('_=bad()',prefix+'\n'),command='check',fail=message)

    def test_fresh_copies_preserve_nested_readonly_aliases(self):
        self.memory('a:=[1,2];testing.expect(t,work(a[:])==9&&a[0]==1)', '''
import "core:slice"
work::fn(input:[]int)->int{local:=slice.clone(input);local[0]=9;return local[0]}
''')
        self.invoke(self.program('a:=[1,2];rows:=[a[:]];_=bad(rows[:])', '''
import "core:slice"
bad::fn(input:[][]int)->int{local:=slice.clone(input);local[0][0]=9;return 0}
'''),command='check',fail='borrowed from an input')

    def test_raw_union_storage_is_rejected(self):
        self.invoke(self.program('_=bad(Raw{})', '''
Raw::struct #raw_union{data:[]int,bits:[2]uintptr}
bad::fn(input:Raw)->uintptr{return input.bits[0]}
'''),command='check',fail='Raw-union storage')
        self.invoke(self.program('a:=[1,2];_=bad(a[:])', '''
Raw::struct #raw_union{data:[]int,bits:[2]uintptr}
bad::fn(input:[]int)->uintptr{local:=Raw{data=input};return local.bits[0]}
'''),command='check',fail='Pure local storage')

    def test_using_projection_cannot_hide_borrowed_slice(self):
        self.invoke(self.program('a:=[1,2];_=bad(R{a[:]})', '''
R::struct{data:[]int}
bad::fn(input:R)->int{local:=input;using local;data[0]=9;return 0}
'''),command='check',fail='borrowed from an input')
        self.invoke('#+feature using-stmt\n'+self.program('a:=[1,2];_=bad(R{a[:]})', '''
R::struct{data:[]int}
bad::fn(using input:R)->int{data[0]=9;return 0}
'''),command='check',fail='borrowed from an input')
        self.invoke('#+feature using-stmt\n'+self.program('a:=[1,2];_=bad(R{a[:]})', '''
R::struct{data:[]int}
bad::fn(using input:$T)->int{data[0]=9;return 0}
'''),command='check',fail='resolved record schema')

    def test_imported_and_native_runtime_storage_cannot_bypass_purity(self):
        self.invoke(self.program('_=bad()','import "library"\nbad::fn()->int{return library.global}\n'),
            files={'library/main.odin':'package library\nglobal:int\n'},command='check',fail='outside the function')
        self.invoke(self.program('_=bad()','import "core:os"\nbad::fn()->int{return len(os.args)}\n'),command='check',fail='unverified native package')
        self.invoke(self.program('_=bad()','import "core:os"\nbad::fn()->int{using os;return len(args)}\n'),command='check',fail='unverified namespaces')
        self.invoke(self.program('assert(VALUE>3&&VALUE<4)','import "core:math"\nidentity::fn(value:f64)->f64{return value}\nVALUE::#run identity(math.PI)\n'))

    def test_opaque_native_packages_are_not_executed_by_run_helpers(self):
        self.invoke(self.program('_=VALUE','''
import "core:os"
work::proc()->int{return int(os.ERROR_NONE)}
VALUE::#run work()
'''),command='check',fail='cannot verify native package')

    def test_inactive_impure_generic_branch_is_left_to_native_selection(self):
        self.invoke(self.program('assert(VALUE==8)','''
work::fn(value:$T)->int{when T==int{return value+1}else{_=context;return 0}}
VALUE::#run work(7)
'''))

    def test_specialization_dependent_nested_run_is_diagnosed_when_selected(self):
        prefix='square::fn(value:int)->int{return value*value}\nwork::proc(value:$T)->int{when T==int{return #run square(value)}else{return 7}}\n'
        self.invoke(self.program('assert(work("text")==7)',prefix))
        self.invoke(self.program('_=work(3)',prefix),command='check',fail='compile-time-known arguments and types at this source location')

    def test_special_floats_complex_quaternion_and_endian_values(self):
        self.invoke(self.program('''
assert(transmute(u16)HALF==0x8000)
assert(transmute(u32)SINGLE==0x7fc00042)
assert(transmute(u64)DOUBLE==0x7ff0000000000000)
assert(real(COMPLEX)==1.5&&imag(COMPLEX)==-2.5)
assert(real(QUAT)==1&&imag(QUAT)==2&&jmag(QUAT)==3&&kmag(QUAT)==4)
assert(BIG==1.25&&SMALL==-345)
''','''
half::fn()->f16{return 0h8000}
single::fn()->f32{return 0h7fc00042}
double::fn()->f64{return 0h7ff0000000000000}
comp::fn()->complex128{return complex(f64(1.5),f64(-2.5))}
quat::fn()->quaternion256{return quaternion(real=f64(1),imag=f64(2),jmag=f64(3),kmag=f64(4))}
big::fn()->f64be{return 1.25}
small::fn()->i32be{return -345}
HALF::#run half()
SINGLE::#run single()
DOUBLE::#run double()
COMPLEX::#run comp()
QUAT::#run quat()
BIG::#run big()
SMALL::#run small()
'''))

    def test_nested_records_bitsets_enumerated_arrays_and_unions(self):
        self.invoke(self.program('''
assert(RECORD.pair[0].x==-7&&RECORD.pair[1].x==8&&RECORD.flag)
assert(.A in FLAGS&&.B not_in FLAGS&&.C in FLAGS)
assert(TABLE[.A]==10&&TABLE[.C]==30)
selected:=CHOICE;value,ok:=selected.(Pair);assert(ok&&value.x==19)
assert(NONE==nil)
''','''
E::enum{A,B,C}
Flags::bit_set[E]
Pair::struct{x:int}
Record::struct{pair:[2]Pair,flag:bool}
Choice::union{int,Pair}
record::fn()->Record{return {[Pair{-7},Pair{8}],true}}
flags::fn()->Flags{return {.A,.C}}
table::fn()->[E]int{return {.A=10,.B=20,.C=30}}
choice::fn()->Choice{return Pair{19}}
none::fn()->Choice{return nil}
RECORD::#run record()
FLAGS::#run flags()
TABLE::#run table()
CHOICE::#run choice()
NONE::#run none()
'''))


class ValueProtocolTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_native_protocol_preserves_string_bytes_and_slice_descriptors(self):
        _,result=self.invoke(self.program('''
evaluation.emit("\\x00\\xff\\x22\\x5c")
nil_slice:[]int
evaluation.emit(nil_slice)
backing:=[7]
evaluation.emit(backing[:0])
evaluation.emit(backing[:])
''','import evaluation "thor:eval"\n'))
        packets=[values.decode(line.encode()) for line in result.stdout.splitlines()]
        self.assertEqual(packets[0],{'kind':'string','bytes':'00ff225c'})
        self.assertEqual(packets[1],{'kind':'sequence','storage':'slice','nil':True,'values':[]})
        self.assertEqual(packets[2],{'kind':'sequence','storage':'slice','nil':False,'values':[]})
        self.assertEqual(packets[3]['values'],[{'kind':'integer','value':'7'}])

    def test_protocol_version_and_invalid_packets(self):
        for data in [b'',b'{}',b'{"thor_value":2,"value":{}}',b'not-json']:
            with self.subTest(data=data):
                with self.assertRaises(values.ValueErrorProtocol):values.decode(data)

    def test_integer_bytes_nil_and_aggregate_reconstruction(self):
        self.assertEqual(values.expression({'kind':'integer','value':str((1<<127)+7)},'T','meta'),f'T({(1<<127)+7})')
        self.assertEqual(values.expression({'kind':'string','bytes':'00ff225c'},'string','meta'),'string("\\x00\\xff\\x22\\x5c")')
        self.assertEqual(values.expression({'kind':'sequence','nil':True,'values':[]},'T','meta'),'T(nil)')
        self.assertEqual(values.expression({'kind':'sequence','nil':False,'values':[]},'T','meta'),'T{}')

    def test_invalid_values_are_rejected(self):
        for packet in [{'kind':'bool','value':1},{'kind':'integer','value':'1);panic()'},
                       {'kind':'float','bytes':'00' * 3},{'kind':'sequence','nil':True,'values':[1]},
                       {'kind':'record','fields':[['x;bad',{}]]},{'kind':'pointer','value':123}]:
            with self.subTest(packet=packet):
                with self.assertRaises(values.ValueErrorProtocol):values.expression(packet,'T','meta')
