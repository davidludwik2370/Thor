"""Differential acceptance checks for the independent native compiler.

An unsupported-feature status is a migration gap, never a successful comparison.
Use real Odin runs and output checks that survive -disable-assert.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / 'rust/target/release/thor-rust'
UNCHECKED = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']


class RustCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('cargo') is None:
            raise unittest.SkipTest('Rust compiler tests require Cargo')
        result = subprocess.run(['cargo', 'build', '--release', '--locked', '--offline',
                                 '--manifest-path', str(ROOT/'rust/Cargo.toml')],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-rust-differential-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root/'input'
        self.app.mkdir()
        self.source = self.app/'main.thor'

    def invoke(self, engine, command='run', flags=(), directory=False, args=(), env=None):
        compiler = ROOT/'thor' if engine == 'python' else RUST
        argv = [str(compiler), command, str(self.app if directory else self.source),
                '--output-dir', str(self.root/engine), '--cache-dir', str(self.root/'cache'),
                *flags]
        if args:
            argv += ['--', *args]
        return subprocess.run(argv, capture_output=True, text=True, cwd=self.root,
                              env=env, timeout=60)

    def compare(self, source, *, files=None, command='run', flags=(), expected=None,
                fail=None, directory=False, args=()):
        self.source.write_text(source)
        for name, content in (files or {}).items():
            path = self.app/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        before = {p:p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        python = self.invoke('python', command, flags, directory, args)
        rust = self.invoke('rust', command, flags, directory, args)
        details = f'Python:\n{python.stdout}{python.stderr}\nRust:\n{rust.stdout}{rust.stderr}'
        self.assertNotEqual(rust.returncode, 3, details)
        self.assertEqual(rust.returncode, python.returncode, details)
        if fail:
            self.assertNotEqual(rust.returncode, 0, details)
            self.assertIn(fail, python.stdout+python.stderr)
            self.assertIn(fail, rust.stdout+rust.stderr)
        else:
            self.assertEqual(rust.returncode, 0, details)
            self.assertEqual(rust.stdout, python.stdout, details)
            if expected is not None:
                self.assertEqual(rust.stdout.rstrip('\n'), expected)
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        return rust

    def program(self, body, prefix=''):
        return 'package main\n'+prefix+'\nmain :: proc() {\n'+body+'\n}\n'

    def test_ordinary_odin_and_unicode_both_build_configurations(self):
        code = self.program('values := [?]int{3,4}; for value in values {fmt.println(value)}',
                            'import "core:fmt"\n// 😀 nested /* comment */\n')
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                self.compare(code, flags=flags, expected='3\n4')

    def test_selected_import_and_lexical_shadowing(self):
        self.compare(self.program('println(7); {println := 42; fmt.println(println)}',
                                  'import "core:fmt" / println\n'), expected='7\n42')

    def test_blank_selected_import(self):
        self.compare(self.program('println("selected")', 'import _ "core:fmt" / println\n'),
                     expected='selected')

    def test_unused_selected_import_still_resolves(self):
        self.compare(self.program('', 'import "core:fmt" / does_not_exist\n'), command='check',
                     fail='does_not_exist')

    def test_selected_import_comments_and_multi_file_scope(self):
        self.compare(self.program('println(other())', 'import "core:fmt" / println /* kept */\n'),
                     files={'other.thor':'package main\nother::proc()->int{return 9}\n'},
                     directory=True, expected='9')

    def test_arrays_membership_and_once_only_evaluation(self):
        self.compare(self.program('''
counter := 0
values := [next(&counter), next(&counter), next(&counter)]
if counter != 3 || 2 not_in values || 7 in values {panic("array semantics")}
fmt.println(values)
''', '''import "core:fmt"
next::proc(n:^int)->int{n^+=1;return n^}
'''), expected='[1, 2, 3]')

    def test_shadowed_type_query_is_a_normal_once_only_call(self):
        self.compare(self.program('''
count:=0
if type_of(&count) not_in [1,2,3] || count!=1 {panic("shadowed builtin")}
fmt.println(count)
''','import "core:fmt"\ntype_of::proc(count:^int)->int{count^+=1;return 2}\n'),expected='1')

    def test_contextual_array_and_membership_numeric_types(self):
        self.compare(self.program('''
x:i8=4
if x not_in [4,8] {panic("context")}
values:[3]i16=[1,2,3]
if 2 not_in values {panic("needle")}
fmt.println(values)
''', 'import "core:fmt"\n'), expected='[1, 2, 3]')

    def test_contextual_parameter_return_and_nested_arrays(self):
        self.compare(self.program('fmt.println(sum([3,4]), build())', '''import "core:fmt"
sum::proc(values:[2]i8)->int{return int(values[0]+values[1])}
build::proc()->[2][2]int{return [[1,2],[3,4]]}
'''), expected='7 [[1, 2], [3, 4]]')

    def test_constant_membership_and_ternary_stay_constants(self):
        self.compare(self.program('fmt.println(values)', '''import "core:fmt"
SIZE :: (2 in [1,2,3]) ? 2 : 1
values:[SIZE]int
'''), expected='[0, 0]')

    def test_constant_concatenation_preserves_element_type(self):
        self.compare(self.program('fmt.println(DATA)', '''import "core:fmt"
LEFT :: [2]i8{1,2}
DATA :: LEFT ++ [3,4] ++ []
'''), expected='[1, 2, 3, 4]')

    def test_concatenation_runtime_rejected(self):
        self.compare(self.program('values := [1,2]; other := values ++ [3]; _=other'),
                     command='check', fail='requires compile-time constant operands')

    def test_string_repetition_and_untyped_cstring(self):
        self.compare(self.program('consume(PAD + "x"); fmt.println(LABEL)', '''import "core:fmt"
PAD :: " " * 3
LABEL :: 4 * "a" + "b"
consume::proc(value:cstring){fmt.println(value)}
'''), expected='   x\naaaab')

    def test_repetition_zero_and_local_constant_counts(self):
        self.compare(self.program('N::5; text::"x"*N; empty::"a"*0; fmt.println(text, len(empty))',
                                  'import "core:fmt"\n'), expected='xxxxx 0')

    def test_runtime_string_repetition_rejected(self):
        self.compare(self.program('text:="x"; result:=text*3; _=result'), command='check',
                     fail='String repetition requires compile-time constant operands')

    def test_numeric_power_runtime_and_evaluation_order(self):
        self.compare(self.program('''
counter:=0
a:=next(&counter) ** next(&counter)
x:f64=2; y:f64=-3
if counter!=2 || a!=1 || x**y!=0.125 {panic("power")}
fmt.println(a,x**y)
''', 'import "core:fmt"\nnext::proc(n:^int)->int{n^+=1;return n^}\n'), expected='1 0.125')

    def test_numeric_power_defaults_literal_operand_types(self):
        self.compare(self.program('''
integer:=2**3
fraction:=2.0 ** -3
if integer!=8 || fraction!=0.125 {panic("default operand types")}
fmt.println(integer, fraction)
''','import "core:fmt"\n'),expected='8 0.125')

    def test_typed_numeric_loops_bounds_order_and_do_bodies(self):
        self.compare(self.program('''
order,total:=0,0
for i:int in mark(&order,1)..=mark(&order,3) {total+=i}
if order!=1333 || total!=6 {panic("bound evaluation")}
for i:f64 in 1..=3 {if type_of(i)!=f64 || .5**i<=0 {panic("numeric type")}}
for i:i8 in 126..=127 do total+=int(i)
for limit:=3; i:f64 in 0..<f64(limit) do total+=int(i)
fmt.println(total)
''','import "core:fmt"\nmark::proc(order:^int,n:int)->int{order^=order^*10+n;return n}\n'),expected='262')

    def test_typed_collection_loops_lifetimes_and_control_flow(self):
        code=self.program('''
values:=[2,4,6];sum,deferred:=0,0
outer: for value,(index:i8) in values {
 defer deferred+=1
 if index==0 do continue outer
 for j:int in 0..<2 {if j==1 do break;sum+=value}
 if index==2 do break outer
}
if sum!=10 || deferred!=3 {panic("control flow")}
for &value,(index:u8) in values {value+=int(index)}
for value,(index:u16) in &values {if value!=values[index]{panic("pointer")}}
view:=values[:];calls:=0
for value,(index:u8) in get(&calls,view) do sum+=value+int(index)
if calls!=1{panic("getter")}
growing:=make([dynamic]int);append(&growing,3,4);defer delete(growing)
for value,(index:u32) in growing{if value!=3+int(index){panic("dynamic")}}
fixed:[dynamic;4]int;append(&fixed,8,9)
for value,(index:u8) in fixed{if value!=8+int(index){panic("fixed")}}
fmt.println(values,sum)
''','import "core:fmt"\nget::proc(calls:^int,data:[]int)->[]int{calls^+=1;return data}\n')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='[2, 5, 8] 28')

    def test_typed_loop_generic_contextless_and_shadowing(self):
        self.compare(self.program('''
values:=[2,3,4]
if typed(f32)!=6 || indices(values[:],u8)!=12 || callback()!=3 {panic("generic")}
for prototype:=f64(0); number:type_of(prototype) in 1..=2 {
 if type_of(number)!=f64{panic("annotation context")}
}
for value,(index:u8) in values{index:=42;if index!=42{panic("shadow")};_=value}
fmt.println("typed")
''','''import "core:fmt"
typed::proc($T:typeid)->T{result:T;for i:T in 1..=3{result+=i};return result}
indices::proc(values:[]int,$I:typeid)->int{sum:=0;for value,(index:I) in values{sum+=value+int(index)};return sum}
callback::proc "c"()->int{values:=[3];total:=0;for value,(index:i8) in values do total+=value+int(index);return total}
'''),expected='typed')

    def test_typed_loop_contract_errors_and_inactive_branches(self):
        for body,message in [
            ('for i:string in 1..=3{_=i}','integer or floating-point'),
            ('values:=[1,2];for value:f64 in values{_=value}','only for numeric ranges'),
            ('for value,(index:i8) in "hello"{_=value;_=index}','only for arrays and slices'),
            ('values:=[1,2];for value,(index:f64) in values{_=value;_=index}','integer type'),
            ('for value,(index:i8) in 0..<3{_=value;_=index}','not numeric ranges'),
            ('values:=[1,2];for index,(index:i8) in values{}','Duplicate loop binding'),
            ('next::proc()->(int,int,bool){return 0,0,false};for value,(index:i8) in next(){_=value;_=index}','only for arrays and slices'),
            ('values:[129]int;for _,(index:i8) in values{_=index}','indices do not fit'),
        ]:
            with self.subTest(body=body):self.compare(self.program(body),command='check',fail=message)
        self.compare(self.program('when false {for i:string in 1..=3{_=i};values:[129]int;for _,(index:i8) in values{_=index}}'))

    def test_typed_loop_narrowing_checks_survive_unchecked_release(self):
        code=self.program('values:[129]int;view:=values[:];for _,(index:i8) in view{_=index}')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,fail='Typed loop index is out of range')

    def test_negative_fixed_array_indices_and_maps(self):
        self.compare(self.program('''
values:=[10,20,30]
values[-1]=40
table:=make(map[int]int); defer delete(table); table[-1]=7
fmt.println(values[-2], values[-1], table[-1])
''', 'import "core:fmt"\n'), expected='20 40 7')

    def test_runtime_sequence_reads_slices_and_utf8_bytes(self):
        code=self.program('''
a:=[1,2,3];s:=a[:];d:=make([dynamic]int);append(&d,1,2,3);defer delete(d)
i:i8=-1;u:u128=2
if a[i]!=3||s[-2]!=2||d[-3]!=1||a[u]!=3{panic("sequences")}
if "é😀"[-1]!=u8(0x80)||"abc"[-2:]!="bc"{panic("string bytes")}
if len(a[:-1])!=2||len(a[-2:-1])!=1||len(a[-3:])!=3{panic("slices")}
fmt.println(s[-1],d[-1])
''','import "core:fmt"\n')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='3 3')

    def test_runtime_sequence_lvalues_nested_storage_and_order(self):
        code=self.program('''
a:=[[1,2],[3,4]];i:=-1
a[i][i]=8;a[-1][-2]+=5;p:=&a[i][i];p^+=1
if a[1][1]!=9||a[1][0]!=8{panic("nested write")}
get()[index()]+=rhs()
if order!=123||data[1]!=7{panic("write order")}
order=0;part:=get()[index():high()]
if order!=124||len(part)!=1{panic("slice order")}
fmt.println(a,data)
''','''import "core:fmt"
data:=[2]int{1,2}
order:int
get::proc()->[]int{order=order*10+1;return data[:]}
index::proc()->int{order=order*10+2;return -1}
rhs::proc()->int{order=order*10+3;return 5}
high::proc()->int{order=order*10+4;return 2}
''')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='[[1, 2], [8, 9]] [1, 7]')

    def test_runtime_sequence_generic_reads_records_and_maps(self):
        self.compare(self.program('''
b:=Board{cells={{1,2},{3,4}}}
if read(b,-1,-2)!=3||generic_read(b,-1,-1)!=4||generic_read(&b,-1,-1)!=4{panic("record")}
a:=[1,2];m:=make(map[int]int);defer delete(m);m[-1]=9
if last(a)!=2||last(a[:])!=2||last("ab")!='b'||last(m)!=9{panic("generic")}
fmt.println("access")
''','''import "core:fmt"
Board::struct{cells:[2][2]int}
read::proc(board:Board,row,col:int)->int{return board.cells[row][col]}
generic_read::proc(board:$T,row,col:int)->int{return board.cells[row][col]}
last::proc(value:$T)->int{return int(value[-1])}
'''),expected='access')

    def test_runtime_sequence_generic_writes_keep_headers_and_lhs_order(self):
        code=self.program('''
m:=make(map[int]int);m[-1]=3;updated:=write(m);defer delete(updated)
if updated[-1]!=8{panic("map header")}
a:=[1,2];view:=write(a[:]);if a[1]!=8||view[1]!=8{panic("slice header")}
values:=[4,5];i:=0;assign(&values,&i)
if i!=1||values[0]!=9||values[1]!=5{panic("array lhs")}
i=0;assign(&m,&i);if i!=1||m[0]!=9{panic("map lhs")}
fmt.println(values,updated[-1])
''','''import "core:fmt"
write::proc(value:$T)->T{x:=value;x[-1]=7;x[-1]+=1;return x}
assign::proc(a:^$T,i:^int){i^,a^[i^]=1,9}
''')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='[9, 5] 8')

    def test_runtime_sequence_wide_bounds_do_not_truncate(self):
        for declaration in ['index:i128=-18446744073709551617','index:u128=18446744073709551616']:
            with self.subTest(declaration=declaration):
                self.compare(self.program('a:=[7,8];'+declaration+';_=a[index]'),fail='Bounds check')

    def test_switch_multiple_results_evaluation_and_assignment_order(self):
        code=self.program('''
order=0
a,b,c:=switch mark(2){case 1:mark(9),"bad",false;case 2:mark(3),"ok",true;case:mark(8),"bad",false}
if a!=3||b!="ok"||!c||order!=23{panic("selected arm")}
order=0;a,b,c=switch{case mark(1)==0:mark(9),"bad",false;case mark(2)==2:mark(4),"yes",false;case:mark(8),"bad",true}
if a!=4||b!="yes"||c||order!=124{panic("conditional arm")}
x,y:=1,2;x,y=(switch{case true:y,x;case:x,y})
values:=[4,5];i:=0;i,values[i]=switch{case true:1,9;case:0,0}
if x!=2||y!=1||i!=1||values[0]!=9||values[1]!=5{panic("assignment")}
fmt.println(a,b,c)
''','import "core:fmt"\norder:int\nmark::proc(x:int)->int{order=order*10+x;return x}\n')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='4 yes false')

    def test_switch_enum_coverage_and_all_nonreturning_signatures(self):
        self.compare(self.program('''
kind:=Kind.B
text:=switch kind{case .A:"a";case .B:"b";case .C:unreachable()}
other:string=switch{case true:"ok";case:stop()}
if text!="b"||other!="ok"{panic("switch result")}
fmt.println(text,other)
''','import "core:fmt"\nKind::enum{A,B,C}\nstop::proc()->!{panic("stopped")}\n'),expected='b ok')
        self.compare(self.program('kind:=Kind.A;value:=switch kind{case .A:1;case .B:2};_=value',
                                  'Kind::enum{A,B,C}\n'),command='check',fail='Unhandled')

    def test_switch_nested_multiple_calls_and_return_context(self):
        self.compare(self.program('a,b:=choose(1);c,d:=choose(2);fmt.println(a,b,c,d)', '''import "core:fmt"
pair::proc()->(int,string){return 7,"ok"}
choose::proc(n:int)->(int,string){return switch n{case 1:pair();case:switch{case true:8,"nested";case:9,"bad"}}}
'''),expected='7 ok 8 nested')

    def test_switch_assignment_break_preserves_targets_and_loop_control(self):
        code=self.program('''
a,b:=1,2;a,b=(switch{case false:3,4;case:break})
if a!=1||b!=2{panic("skip")}
a,b=switch{case true:b,a;case:break}
if a!=2||b!=1{panic("swap")}
data:=[4,5];order=0
data[index()],data[-1]=switch mark(2){case 2:mark(3),mark(4);case:break}
if order!=1234||data!=[3,4]{panic("target order")}
m:=make(map[int]int);defer delete(m)
m[-1]=switch{case true:3;case:break}
if m[-1]!=3{panic("map skip")}
visited:=0
for i:=0;i<3;i=switch{case true:i+1;case:break}{visited+=1}
fmt.println(a,b,data,visited)
''','''import "core:fmt"
order:int
index::proc()->int{order=order*10+1;return 0}
mark::proc(x:int)->int{order=order*10+x;return x}
''')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='2 1 [3, 4] 3')

    def test_switch_value_errors_and_constant_choice(self):
        for body,message in [
            ('a,b:=switch{case true:1,2;case:3};_=a;_=b','Every switch value arm must return 2 results'),
            ('a:=switch{case true:1};_=a','requires a default arm'),
            ('a:=switch{case true:1;case:break};_=a','break in a switch expression'),
        ]:
            with self.subTest(body=body):self.compare(self.program(body),command='check',fail=message)
        self.compare(self.program('fmt.println(PAD+"ok")','''import "core:fmt"
PAD::#switch{case false:undefined;case:" "*3}
'''),expected='   ok')

    def test_compile_switch_multi_constants_and_lazy_statement_selection(self):
        self.compare(self.program('''
if A!=2||B!="ok"{panic("global constants")}
x,y::(#switch{case true:3,"local";case:undefined,undefined})
#assert(x==3&&y=="local")
a,b:=#switch 2{case 1:undefined,undefined;case 2:7,"yes";case:undefined,undefined}
if a!=7||b!="yes"{panic("local selection")}
a,b=#switch{case false:undefined,undefined;case:9,"last"}
a,b=#switch{case true:break;case:undefined,undefined}
if a!=9||b!="last"{panic("compile skip")}
#switch 2 {case 1:SELECTED::1;case 2:SELECTED::2}
if SELECTED!=2{panic("statement visibility")}
count:=0
for a,b:=#switch{case true:1,2;case:undefined};a<3;a+=1{if b!=2{panic("header")};count+=1}
fmt.println(x,y,count)
''','''import "core:fmt"
A,B::#switch 2{case 2:2,"ok";case:undefined,undefined}
'''),expected='3 local 2')

    def test_compile_switch_native_case_validation_and_unselected_conditions(self):
        self.compare(self.program('''
VALUE::#switch{case true,missing_condition:7;case:0}
#switch{case true:CHOSEN::VALUE;case missing_condition:CHOSEN::missing}
if CHOSEN!=7{panic("lazy conditions")}
typed:int=#switch{case true:4;case:panic("inactive")}
fmt.println(typed)
''','import "core:fmt"\n'),expected='4')
        for expression in ['#switch 1{case 1:1;case C:2;case:3}',
                           '#switch #config(MODE,1){case 1:1;case int(1):2;case:3}']:
            with self.subTest(expression=expression):self.compare(self.program('_='+expression,'C::1\n'),command='check',fail='Duplicate')

    def test_global_multi_switch_initialization_order_and_native_values(self):
        self.compare(self.program('''
if count!=1||a!=7||text!="ok"||later!=8||values[-1]!=2||nested_n!=9||nested_s!="nested"{panic("global initialization")}
fmt.println(a,text,later)
''','''import "core:fmt"
count:int
mark::proc "contextless"()->int{count+=1;return 7}
a,text:=switch{case true:mark(),"ok";case:0,"bad"}
later:=a+1
values,other:=switch{case true:[1,2],3;case:[4,5],6}
nested_n,nested_s:=switch{case true:(switch{case true:9;case:0}),"nested";case:0,"other"}
'''),expected='7 ok 8')

    def test_switch_discarded_strings_and_named_sets_are_leak_free(self):
        code=self.program('''
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
{
 context.allocator=mem.tracking_allocator(&tracker)
 for i in 0..<10 {
  n,_:=switch{case true:i,f"discard {i}";case:0,f"unused {i}"}
  _,n=switch{case true:f"assignment {i}",i+1;case:break}
  named:=f"named {i}"
  _,n=switch{case true:named,n;case:"",0}
  if len(named)==0{panic("named ownership")};delete(named)
  a:=set[int]{1,2,2};b:=set[int]{2,3}
  u:=a|b;shared:=a&b;diff:=a-b;xor:=a~b
  if len(u)!=3||len(shared)!=1||len(diff)!=1||len(xor)!=2{panic("set operations")}
  delete(a);delete(b);delete(u);delete(shared);delete(diff);delete(xor)
 }
}
if tracker.current_memory_allocated!=0||len(tracker.bad_free_array)!=0{panic("temporary ownership")}
fmt.println("clean")
''','import "core:fmt"\nimport "core:mem"\n')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(code,flags=flags,expected='clean')

    def test_runtime_index_failure_reports_original_source_even_when_launched_directly(self):
        self.source.write_text(self.program('values:=[7,8];index:=-3;_=values[index]'))
        result=self.invoke('rust',command='build',flags=['-out:'+str(self.root/'bounds')])
        self.assertEqual(result.returncode,0,result.stderr)
        result=subprocess.run([str(self.root/'bounds')],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn(str(self.source),result.stderr)
        self.assertNotIn(str(self.root/'rust'),result.stderr)

    def test_set_temporary_query_matches_reference(self):
        self.compare(self.program('if len(set[int]{1,2})!=2{panic("temporary query")}'))

    def test_comparison_shift_and_nil_needles_use_the_container_element_type(self):
        source=self.program('''n:=2;truth:=b8(true);count:=uint(2);value:=u8(4);pointer:^int
if !((n==2) in [truth]){panic("comparison context")}
if !((1<<count) in [value]){panic("shift context")}
if nil not_in [pointer]{panic("nil context")}
''')
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=flags)

    def test_immutable_inline_storage_rejected(self):
        self.compare(self.program('values :> [1,2]; values[0]=3'), command='check',
                     fail="Cannot write immutable storage 'values'")
        self.compare(self.program('values :> [1,2]; pointer:=raw_data(values);_=pointer'),command='check',
                     fail="Cannot obtain a mutable data pointer to immutable storage 'values'")
        self.compare(self.program('values :> [1,2]; for &value in values {value=3}'),command='check',
                     fail="Cannot iterate by mutable reference over immutable storage 'values'")

    def test_immutable_copy_and_shadowing(self):
        self.compare(self.program('''
values :> [1,2]
copy:=values; copy[0]=7
{values := [3,4]; values[1]=5;fmt.println(values)}
fmt.println(values,copy)
''', 'import "core:fmt"\n'), expected='[3, 5]\n[1, 2] [7, 2]')

    def test_immutable_shadow_initializer_reads_outer_binding(self):
        self.compare(self.program('''
value:=7
{value :> value; fmt.println(value)}
fmt.println(value)
''','import "core:fmt"\n'),expected='7\n7')

    def test_automatic_using_feature_preserves_source(self):
        self.compare(self.program('record:=Record{4}; fmt.println(read(record))', '''import "core:fmt"
Record::struct{value:int}
read::proc(using r:Record)->int{return value}
'''), expected='4')

    def test_directory_build_excludes_test_sources(self):
        self.compare(self.program('fmt.println(7)', 'import "core:fmt"\n'), directory=True,
                     files={'broken_test.thor':'package main\nthis is invalid\n'}, expected='7')

    def test_mixed_relative_import_packages(self):
        self.compare(self.program('fmt.println(helper.value())',
                                  'import "core:fmt"\nimport "helper"\n'),
                     directory=True, files={'helper/value.odin':'package helper\nvalue::proc()->int{return 8}\n'},
                     expected='8')

    def test_program_arguments_stay_separate(self):
        self.compare(self.program('fmt.println(os.args[1],os.args[2])',
                                  'import "core:fmt"\nimport "core:os"\n'),
                     args=['one two', 'three'], expected='one two three')

    def test_resources_keep_original_file_relative_paths(self):
        code = self.program('fmt.println(DATA, PRESENT, ABSENT)', '''import "core:fmt"
DATA :: #load("assets/value.txt", string)
PRESENT :: #exists("assets/value.txt")
ABSENT :: #exists("assets/missing.txt")
''')
        for extension in ['thor', 'odin']:
            with self.subTest(extension=extension):
                self.source = self.app/('main.'+extension)
                self.compare(code, files={'assets/value.txt':'payload'}, expected='payload true false')

    def test_default_executable_name_matches_current_directory(self):
        self.source.write_text(self.program(''))
        for engine in ['python', 'rust']:
            with self.subTest(engine=engine):
                result = self.invoke(engine, command='build')
                self.assertEqual(result.returncode, 0, result.stderr)
                executable = self.root/(self.root.name+('.exe' if os.name=='nt' else ''))
                self.assertTrue(executable.is_file())
                executed = subprocess.run([str(executable)], capture_output=True, text=True)
                self.assertEqual(executed.returncode, 0, executed.stderr)

    def test_interpolation_formatting_named_and_manual_values(self):
        self.compare(self.program('''
width:=10; n:=1234
text:=f"value {n:0{width},d} {(1.0/3):.2%}"
defer delete(text)
fmt.println(text)
consume(f!"manual {n}")
''', 'import "core:fmt"\nconsume::proc(text:string){fmt.println(text);delete(text)}\n'),
                     expected='value 000001,234 33.33%\nmanual 1234')

    def test_interpolation_anonymous_arguments_do_not_leak_in_release(self):
        code = self.program('''
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker, context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
{
 context.allocator=mem.tracking_allocator(&tracker)
 for i in 0..<30 {
  if len(identity(f"hello {i}"))<7 {panic("temporary lifetime")}
  if false && len(identity(f"not used {i}"))>0 {panic("lazy argument")}
  consume(f!"manual {i}")
  text:=f"named {i}";delete(text)
 }
}
if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0 {panic("string lifetime")}
fmt.println("ok")
''', '''import "core:fmt"
import "core:mem"
identity::proc(text:string)->string{return text}
consume::proc(text:string){delete(text)}
''')
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                self.compare(code, flags=flags, expected='ok')

    def test_interpolation_format_parser_matches_reference(self):
        code = self.program('''
fmt.println(f"{12:+08d}|{0.125:.2%}|{'a':U}|{\"hi\":'界'^6}")
''', 'import "core:fmt"\n')
        self.compare(code)
        for spec, message in [('>>6', 'no longer uses < or >'), ('width', 'variable width needs nested braces'),
                              ('--4', 'Duplicate interpolation flag'), ('^.2', 'requires an explicit verb')]:
            with self.subTest(spec=spec):
                self.compare(self.program('text:=f"{2:'+spec+'}";_=text'), command='check', fail=message)

    def test_interpolation_width_and_field_evaluation_order(self):
        self.compare(self.program('''
order:=0
text:=f"{step(&order,1):{step(&order,2)}} {step(&order,3)}"
defer delete(text)
if order!=123 {panic("evaluation order")}
fmt.println(text)
''', 'import "core:fmt"\nstep::proc(order:^int,n:int)->int{order^=order^*10+n;return n}\n'),
                     expected=' 1 3')

    def test_scope_cleanup_call_is_not_moved_into_format_helper(self):
        self.compare(self.program('text:=f"{acquire()}";delete(text)', '''
@(deferred_out=release)
acquire::proc()->int{return 7}
release::proc(value:int){}
'''),command='check',fail='automatic scope cleanup must be called directly')
        self.compare(self.program('text:=f"{library.acquire()}";delete(text)', 'import "library"\n'),
                     files={'library/main.odin':'''package library
@(deferred_out=release)
acquire::proc()->int{return 7}
release::proc(value:int){}
'''},command='check',fail='automatic scope cleanup must be called directly')
        self.compare(self.program('borrow(f"hello {2}")','''
@(deferred_in=release)
borrow::proc(text:string){}
release::proc(text:string){}
'''),command='check',fail='automatic scope cleanup must be called directly')

    def test_original_diagnostic_offsets_utf16_and_schema(self):
        self.source.write_text(self.program('text:="😀"; value:int="bad"; _=text; _=value'))
        rust = self.invoke('rust', command='check', flags=['-json-errors'])
        python = self.invoke('python', command='check', flags=['-json-errors'])
        self.assertEqual(rust.returncode, 1, rust.stderr)
        rust_errors = json.loads(rust.stderr)['errors']
        python_errors = json.loads(python.stderr)['errors']
        self.assertEqual(rust_errors[0]['pos']['file'], str(self.source))
        self.assertEqual(rust_errors[0]['pos']['line'], python_errors[0]['pos']['line'])
        self.assertEqual(rust_errors[0]['pos']['column'], python_errors[0]['pos']['column'])
        self.assertEqual(rust_errors[0]['msgs'], python_errors[0]['msgs'])

    def test_source_map_partition_and_lookup_matches_python_consumer(self):
        self.source.write_text(self.program('values:=[1,2]; _=values'))
        result = self.invoke('rust', command='transpile')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = Path(result.stdout.strip())
        mapping = json.loads(Path(str(output)+'.map.json').read_text())
        from thorlang.sourcemap import SourceMap
        consumer = SourceMap(mapping['segments'], mapping['expansions'], mapping['regions'])
        cursor = 0
        for first, last, source, start, end, kind, exact in mapping['segments']:
            self.assertEqual(first, cursor)
            self.assertGreater(last, first)
            self.assertEqual(source, str(self.source))
            cursor = last
        self.assertEqual(cursor, len(output.read_bytes()))
        offset = output.read_bytes().index(b'values')
        origin = consumer.lookup(offset, len('values'))
        self.assertEqual(self.source.read_bytes()[origin.start:origin.end], b'values')
        self.assertTrue(mapping['expansions'])

    def test_parser_cache_corruption_recomputed(self):
        self.source.write_text(self.program(''))
        first = self.invoke('rust', command='check')
        self.assertEqual(first.returncode, 0, first.stderr)
        cache_files = list((self.root/'cache/rust-parse-v1').glob('*.json'))
        self.assertTrue(cache_files)
        for file in cache_files:
            file.write_text('{broken')
        second = self.invoke('rust', command='check')
        self.assertEqual(second.returncode, 0, second.stderr)
        for file in cache_files:
            self.assertIsInstance(json.loads(file.read_text())['payload']['nodes'], list)
            packet=json.loads(file.read_text())
            self.assertEqual(packet['schema'],'odin-frontend-raw-v1')
            self.assertTrue(all('immutable' not in node for node in packet['payload']['nodes']))

    def test_unported_features_fail_closed_without_python(self):
        for code in ['T::int;when T==int {value:=1} else {value :> 1};value=2;_=value']:
            with self.subTest(code=code):
                self.source.write_text(self.program(code, 'calculate::proc()->int{return 2}\n'))
                result = self.invoke('rust', command='check', flags=['-json-errors'])
                self.assertEqual(result.returncode, 3, result.stderr)
                packet = json.loads(result.stderr)
                self.assertEqual(packet['errors'][0]['stage'], 'rust-migration')

    def test_supported_temporary_consumers_have_clean_json_diagnostics(self):
        for code in ['value:=f"hello {2}"=="hello 2";_=value',
                     'value:=len(set[int]{1,2});_=value']:
            with self.subTest(code=code):
                result=self.compare(self.program(code),command='check',flags=['-json-errors'])
                packet=json.loads(result.stderr)
                self.assertEqual(packet['error_count'],0)

    def test_capabilities_admit_incomplete_port(self):
        result = subprocess.run([str(RUST), '--capabilities'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertFalse(packet['complete'])

        self.assertFalse(packet['python_fallback'])
        self.assertFalse(packet['parallel_lowering'])

    def test_frontend_ast_protocol_matches_reference_on_cross_feature_sources(self):
        from thorlang.compiler import Source
        from thorlang.driver import frontend_binary
        frontend, _ = frontend_binary(os.environ.get('THOR_ODIN', 'odin'))
        paths = [ROOT/name for name in ['demo/main.thor', 'examples/comprehensions/demo.thor',
                                       'examples/slice_access/demo.thor'] if (ROOT/name).is_file()]
        self.source.write_text(self.program('values:=[1,2];_=values'))
        paths.append(self.source)
        for path in paths:
            with self.subTest(path=path):
                result = subprocess.run([str(RUST), 'ast', str(path), '--no-cache'],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = next(item['frontend'] for item in json.loads(result.stdout)['sources']
                              if Path(item['path']) == path.resolve())
                expected = Source(path, frontend, package=False, project_name=path.stem, cache=False).info
                self.assertEqual(actual, expected)

    def test_large_ast_pipe_is_drained_without_deadlock(self):
        self.source.write_text('package main\n'+''.join(f'VALUE_{i}::{i}\n' for i in range(2000))+'main::proc(){}\n')
        result = self.invoke('rust', command='ast')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(result.stdout), 1_000_000)
        self.assertGreater(len(json.loads(result.stdout)['sources'][0]['frontend']['nodes']), 6000)

    def test_single_value_or_panic_evaluates_once(self):
        self.compare(self.program('''
count:=0
value:=get(&count) or_panic
if count!=1 || value!=7 {panic("optional evaluation")}
fmt.println(value)
''','import "core:fmt"\nget::proc(n:^int)->(int,bool){n^+=1;return 7,true}\n'), expected='7')

    def test_or_panic_failure_keeps_unicode_source_location_and_exit_status(self):
        code=self.program('/* 😀 */ value:=get() or_panic;_=value',
                          'get::proc()->(int,bool){return 0,false}\n')
        self.source.write_text(code)
        line=next(i for i,text in enumerate(code.splitlines(),1) if 'or_panic' in text)
        source_line=code.splitlines()[line-1]
        column=source_line.index('get()')+1
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags):
                python=self.invoke('python',flags=flags)
                rust=self.invoke('rust',flags=flags)
                self.assertNotEqual(python.returncode,0,python.stderr)
                self.assertEqual(rust.returncode,python.returncode,rust.stderr)
                for result in [python,rust]:
                    self.assertIn(f'{self.source}({line}:{column})',result.stderr)
                    self.assertIn('unexpected failure',result.stderr)

    def test_no_python_subprocess_fallback(self):
        self.source.write_text(self.program(''))
        trap = self.root/'trap'
        trap.mkdir()
        for name in ['python', 'python3']:
            program = trap/name
            program.write_text('#!/bin/sh\nexit 97\n')
            program.chmod(0o755)
        environment = {**os.environ, 'PATH':str(trap)+os.pathsep+os.environ.get('PATH', '')}
        result = self.invoke('rust', command='check', env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_readonly_generic_record_value_arguments_preserve_values(self):
        prefix='''Box::struct($N:int){items:[]int}
write::proc(values:[]$T){when T==Box(1){_=values[0]}else{values[0].items[0]=9}}
'''
        self.compare(self.program('values :> []Box(1){{[]int{1}}};write(values)',prefix),command='check')
        self.compare(self.program('values :> []Box(2){{[]int{1}}};write(values)',prefix),command='check',fail='read-only slice')
        prefix='''Box::struct($Enabled:bool){items:[]int}
write::proc(values:[]$T){when T==Box(true){_=values[0]}else{values[0].items[0]=9}}
'''
        self.compare(self.program('values :> []Box(true){{[]int{1}}};write(values)',prefix),command='check')
        self.compare(self.program('values :> []Box(false){{[]int{1}}};write(values)',prefix),command='check',fail='read-only slice')

    def test_readonly_enum_constant_record_arguments_use_values_including_aliases(self):
        prefix='''Mode::enum u8{Read=1,Alias=Read,Write}
Box::struct($Mode:Mode){items:[]int}
read::proc(values:[]$T){when T==Box(.Read){_=values[0]}else{values[0].items[0]=9}}
write::proc(values:[]$T){when T==Box(.Read){values[0].items[0]=9}else{_=values[0]}}
'''
        self.compare(self.program('values :> []Box(.Alias){{[]int{1}}};read(values)',prefix),command='check')
        self.compare(self.program('values :> []Box(.Write){{[]int{1}}};read(values)',prefix),command='check',fail='read-only slice')
        self.compare(self.program('values :> []Box(.Alias){{[]int{1}}};write(values)',prefix),command='check',fail='read-only slice')

    def test_readonly_explicit_and_inferred_constant_parameters(self):
        prefix='work::proc(values:[]int,$N:int){when N==1{_=values[0]}else{values[0]=9}}\n'
        self.compare(self.program('values :> []int{1};work(values,1)',prefix),command='check')
        self.compare(self.program('values :> []int{1};work(values,2)',prefix),command='check',fail='read-only slice')
        prefix='work::proc(values:[][$N]int){when N==1{_=values[0]}else{values[0][0]=9}}\n'
        self.compare(self.program('values :> [][1]int{{1}};work(values)',prefix),command='check')
        self.compare(self.program('values :> [][2]int{{1,2}};work(values)',prefix),command='check',fail='read-only slice')

    def test_method_receivers_and_overridden_library_readers_remain_checked(self):
        prefix='''Record::struct{items:[]int,apply:proc(self:^Record)}
change::proc(self:^Record){self.items[0]=9}
'''
        self.compare(self.program('values :> []int{1};record:=Record{values,change};record->apply()',prefix),command='check',fail='read-only slice')
        self.compare(self.program('values:=[]int{1};record:=Record{values,change};record->apply();if values[0]!=9{panic("method")}',prefix))
        library=self.root/'custom/basic';library.mkdir(parents=True)
        (library/'basic.odin').write_text('package basic\nprint::proc(values:[]int){values[0]=9}\n')
        for module in (ROOT/'runtime').iterdir():
            if module.is_dir() and module.name!='basic':(library.parent/module.name).symlink_to(module,target_is_directory=True)
        self.compare(self.program('values :> []int{1};basic.print(values)','import "thor:basic"\n'),command='check',flags=['-collection:thor='+str(library.parent)],fail='read-only slice')


if __name__ == '__main__':
    unittest.main()
