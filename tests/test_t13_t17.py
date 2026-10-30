"""Behavioral tests for expression-local lowering, strings, switches and Sets."""
import unittest
from tests import test_thor as base


class ExpressionTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_interpolation_quoted_raw_escapes_and_nested_quotes(self):
        self.invoke(self.program('''
a:=f"hello {echo("nested")} {{braces}}\\n"
b:=f`raw \\n {3}`
assert(a=="hello nested {braces}\\n" && b==`raw \\n 3`)
delete(a);delete(b)
''','echo::proc(x:string)->string{return x}\n'))

    def test_interpolation_formatting_and_nested_fields(self):
        self.invoke(self.program('''
a:=f"{i64(-9223372036854775808):,}|{1234:'0'>8,}|{"é😀":'界'^5}"
assert(a=="-9,223,372,036,854,775,808|0001,234|界é😀界界")
b:=f"outer {f"inner {2}"} {(false ? 8 : 9)}"
assert(b=="outer inner 2 9")
delete(a);delete(b)
'''))

    def test_interpolation_field_source_order_once(self):
        self.invoke(self.program('x:=0; s:=f"{next(&x)} {next(&x)}";assert(s=="1 2" && x==2);delete(s)',
                                 'next::proc(x:^int)->int{x^+=1;return x^}\n'))

    def test_interpolation_delimiters_as_format_fill(self):
        self.invoke(self.program('''
a:=f"{1:'}'>3}|{2:'{'<3}|{3:'"'>3}"
assert(a==`}}1|2{{|""3`);delete(a)
'''))

    def test_interpolation_fill_uses_native_rune_escapes_in_quoted_and_raw_strings(self):
        self.invoke(self.program(r'''
a:=f"{1:'\''>3}|{2:'\\'<3}|{3:'\u754c'^5}|{4:'\U0001f600'>3}|{5:'\x30'>3}"
assert(a==`''1|2\\|界界3界界|😀😀4|005`);delete(a)
b:=f`{"é😀":'界'^5}|{1:'`'>3}`
assert(b=="界é😀界界|``1");delete(b)
c:=f"{"x":'\n'>3}|{"x":'\000'>3}"
assert(c=="\n\nx|\x00\x00x");delete(c)
'''))

    def test_interpolation_fill_literals_and_alignment_are_validated(self):
        for spec,message in [
            ('0>5','single-quoted rune'),('界^5','single-quoted rune'),
            ("'界'5",'followed by'),("'界'",'followed by'),
            ("''>3",'illegal rune literal'),("'ab'>3",'illegal rune literal'),
            (r"'\uD800'>3",'invalid Unicode'),(r"'\U00110000'>3",'invalid Unicode'),
            (r"'\q'>3",'unknown escape'),("'x'>-3",'Unsupported interpolation format'),
        ]:
            with self.subTest(spec=spec):
                self.invoke(self.program('_=f"{1:'+spec+'}"'),command='check',fail=message)

    def test_quoted_format_fill_agrees_at_runtime_run_and_automatic_evaluation(self):
        self.invoke(self.program('''
value:=1234
text:=label(value)
assert(text=="0001,234|界é😀界界"&&text==AUTO&&text==EXPLICIT)
delete(text)
''','''
label::fn(value:int)->string{return f`{value:'0'>8,}|{"é😀":'界'^5}`}
AUTO::label(1234)
EXPLICIT::#run label(1234)
'''))

    def test_interpolation_empty_fields_and_formats_rejected(self):
        for text,message in [('f"{}"','requires an expression'),('f"{1"','terminated'),
                             ('f"oops }"','Unmatched'),('f"{1:.2f}"','Unsupported interpolation format')]:
            with self.subTest(text=text): self.invoke(self.program('_='+text),command='check',fail=message)

    def test_interpolation_grouping_type_error(self):
        self.invoke(self.program('_=f"{1.25:,}"'),command='check',fail='integer')

    def test_interpolation_owned_storage_with_memory_tracking(self):
        self.invoke('''package main
import "core:testing"
@(test) owned::proc(t:^testing.T){
 for i in 0..<5 {
  text:=f"hello {(f"nested {i}")} {i:>8,} {(true ? f"a {i}" : f"b {i}")} {switch i {case 0:f"zero";case:f"other {i}"}}"
  testing.expect(t,len(text)>0)
  delete(text)
 }
}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_set_syntax_and_generic_operations_with_memory_tracking(self):
        self.invoke('''package main
import "core:testing"
work::proc(a,b:$T)->T{x:=a|b;x-=a;return x}
@(test) owned::proc(t:^testing.T){
 a:=set[string]{"a","b","a"};b:=set[string]{"b","c"}
 c:=work(a,b);testing.expect(t,len(c)==1&&"c" in c)
 a|=b;a&=b;a~=b;clear(a)
 delete(a);delete(b);delete(c)
}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_runtime_switch_selector_once_and_lazy_arms(self):
        self.invoke(self.program('''
x:=0
answer:=switch next(&x) {case 1: next(&x); case: next(&x)*10}
assert(answer==2&&x==2)
''','next::proc(x:^int)->int{x^+=1;return x^}\n'))

    def test_runtime_switch_boolean_multi_cases_short_circuit(self):
        self.invoke(self.program('''
x:=0
answer:=switch {case test(&x,1),test(&x,2): 5; case test(&x,3):9; case:0}
assert(answer==5&&x==1)
''','test::proc(x:^int,n:int)->bool{x^=x^*10+n;return true}\n'))

    def test_runtime_switch_multi_values_ranges_default_anywhere(self):
        self.invoke(self.program('''
for value in 0..<7 {
 result:=switch value {case:0; case 1,2:10; case 3..<5:20; case 5..=6:30}
 assert(result==(value==0 ? 0 : value<3 ? 10 : value<5 ? 20 : 30))
}
'''))

    def test_runtime_switch_contextual_arrays_enums_and_records(self):
        self.invoke(self.program('''
a:[2]Color=switch 1 {case 1:[.Red,.Blue];case:[.Blue,.Red]}
s:S=switch {case true:{x=1};case:{x=2}}
assert(a[1]==.Blue&&s.x==1)
''','Color::enum{Red,Blue}\nS::struct{x:int}\n'))

    def test_switch_missing_default_duplicate_cases_and_statement_arms(self):
        for expr,message in [('switch 1 {case 1:2}','requires a default'),
                             ('switch 1 {case 1:2;case 1:3;case:4}','Duplicate'),
                             ('switch 1 {case 1:2;case:3;case:4}','one default'),
                             ('switch 1 {case 1: x:=3;case:4}','only result expressions or break')]:
            with self.subTest(expr=expr): self.invoke(self.program('_='+expr),command='check',fail=message)

    def test_compile_switch_selector_and_boolean_expressions_are_constants(self):
        self.invoke(self.program('assert(A==20&&B==30&&len(a)==20)', '''
A::#switch #config(MODE,2){case 1:10;case 2:20;case:0}
B::#switch {case false:10;case true:30;case:0}
a:[A]int
'''))

    def test_compile_switch_statement_visibility_return_and_loop_break(self):
        self.invoke(self.program('''
#switch 2 {case 1:X::1;case 2:X::2}
assert(X==2&&answer()==7)
x:=0
for {#switch true {case true:break};x+=1}
assert(x==0)
''','answer::proc()->int{#switch {case true:return 7;case:return 9}}\n'))

    def test_compile_switch_selected_arms_only_and_native_when(self):
        self.invoke(self.program('''
VALUE::#switch 2{case 1:missing;case 2:7;case:other_missing}
#switch {case true: SELECTED::VALUE;case missing_bool:SELECTED::missing_value}
when SELECTED==7 { assert(true) }
'''))

    def test_inactive_when_and_compile_switch_helpers_remain_unchecked(self):
        self.invoke(self.program('''
VALUE::#switch 2{case 1:f"{missing}";case 2:"ok";case:f"{1.25:,}"}
text:="ok" when true else f"{also_missing}"
assert(VALUE=="ok"&&text=="ok")
'''))

    def test_compile_switch_selected_set_type_drives_later_operations(self):
        self.invoke(self.program('''
names:=#switch 2{case 1:"unused";case 2:set[int]{1};case:false}
names+=2;assert(len(names)==2&&2 in names);delete(names)
'''))
        self.invoke(self.program('''
names:>#switch 2{case 1:"unused";case 2:set[int]{1};case:false}
clear(names)
'''),command='check',fail='immutable storage')

    def test_generic_clear_protects_set_header_but_allows_existing_pointer(self):
        prefix='work::proc(value:$T){local:>value;clear(local)}\n'
        self.invoke(self.program('a:[dynamic]int;append(&a,1,2);work(&a);assert(len(a)==0);delete(a)',prefix))
        self.invoke(self.program('a:=set[int]{1};work(a);delete(a)',prefix),command='check',fail='immutable storage')

    def test_compile_switch_runtime_selector_is_rejected(self):
        self.invoke(self.program('x:=1;_=#switch x{case 1:2;case:3}'),command='check',fail='constant')

    def test_compile_switch_ranges_and_multi_case(self):
        self.invoke(self.program('A::#switch 5{case 1,2:10;case 3..=6:20;case:0};assert(A==20)'))

    def test_compile_switch_comma_conditions_are_lazy(self):
        self.invoke(self.program('A::#switch {case true,missing_condition:7;case:0};assert(A==7)'))

    def test_compile_switch_duplicate_alias_ranges_and_enum_cases(self):
        for cases in ['case 1:2;case C:3;case:4','case 1..=3:2;case 2:3;case:4']:
            self.invoke(self.program('_=#switch 1{'+cases+'}','C::1\n'),command='check',fail='Duplicate')
        self.invoke(self.program('_=#switch Color.Red{case .Red:1;case Color.Red:2;case:0}',
                    'Color::enum{Red,Blue}\n'),command='check',fail='Duplicate')

    def test_compile_switch_native_constant_case_validation(self):
        for cases in ['case #config(KEY,1):2;case int(1):3;case:4',
                      'case "a":1;case "\\x61":2;case:3']:
            selector='"a"' if '"a"' in cases else '1'
            self.invoke(self.program('_=#switch '+selector+'{'+cases+'}'),command='check',fail='Duplicate')

    def test_compile_switch_nested_generated_work_and_visibility(self):
        self.invoke(self.program('''
#switch 2 {case 2:
 data:=[1,2];i:=-1;text:=f"{switch data[i]{case 2:7;case:0}}"
case: missing()}
assert(text=="7");delete(text)
'''))

    def test_generic_set_operators_iteration_length_and_cleanup(self):
        self.invoke(self.program('a:=set[int]{1,2};b:=set[int]{2,3};assert(work(a,b)==3);delete(a);delete(b)', '''
work::proc(left,right:$T)->int{
 x:=left|right
 n:=len(x)
 for value in x {assert(value in [1,2,3])}
 x-=1; x+=9
 assert(x!=left)
 clear(&x);delete(x)
 return n
}
'''))

    def test_generic_native_operations_keep_contextual_numeric_types(self):
        self.invoke(self.program('assert(f(u8(3))==4)', 'f::proc(value:$T)->T{x:=value;x+=1;assert(x==4);return x}\n'))

    def test_contextless_expression_helpers_and_generic_type_constants(self):
        self.invoke(self.program('assert(f([1,2])==2&&g(7)==7)', '''
f::proc "contextless"(value:$T/[2]int)->int{index:=-1;return value[index]}
g::proc(value:$T)->T{return switch 1 {case 1:value;case:T{}}}
'''))

    def test_set_literals_operations_queries_and_cleanup(self):
        self.invoke(self.program('''
a:=set[int]{1,2,2}; b:=set[int]{2,3}
assert(len(a)==2&&1 in a&&9 not_in a)
u:=a|b;i:=a&b;d:=a-b;x:=a~b
assert(len(u)==3&&len(i)==1&&len(d)==1&&len(x)==2)
copy:=set_copy(b);assert(i==copy);delete(copy)
assert(a!=b)
for item in x{assert(item in [1,3])}
delete(a);delete(b);delete(u);delete(i);delete(d);delete(x)
''','set_copy::proc(unused:set[int])->set[int]{return set[int]{2}}\n'))

    def test_set_contextual_enum_literal_and_zero_initialized(self):
        self.invoke(self.program('a:=set[Color]{.Red,.Blue,.Red};b:set[Color];b+=.Red;assert(len(a)==2&&.Red in b);delete(a);delete(b)',
                                 'Color::enum{Red,Blue}\n'))

    def test_set_in_place_and_alias_self_operations(self):
        self.invoke(self.program('''
a:=set[int]{1,2}; alias:=a
a|=alias; a&=alias; assert(len(a)==2)
a-=alias; assert(len(a)==0)
a+=4; a-=4; a-=99; assert(len(a)==0)
a+=1;a+=2;a~=a;assert(len(a)==0)
clear(&a);delete(a)
'''))

    def test_set_results_independent_and_assignment_aliases(self):
        self.invoke(self.program('''
a:=set[int]{1};b:=set[int]{2};c:=a|b
c+=3; assert(3 not_in a && 3 not_in b)
alias:=a;assert(alias==a)
delete(a);delete(b);delete(c)
'''))

    def test_set_mutation_integrates_with_immutability(self):
        for operation in ['a+=3','a|=b','a~=b','clear(a)','clear(&a)']:
            with self.subTest(operation=operation):
                self.invoke(self.program('a:>set[int]{1};b:=set[int]{2};'+operation),command='check',fail='immutable storage')

    def test_set_preserves_native_operators_and_set_identifier(self):
        self.invoke(self.program('set:=[4,5];a:=3;b:=5;assert(set[1]==5&&a|b==7&&a&b==1&&a-b==-2&&(a~b)==6)'))

    def test_negative_array_slice_dynamic_and_string_accesses(self):
        self.invoke(self.program('''
a:=[1,2,3]; s:=a[:]; d:=make([dynamic]int);append(&d,1,2,3)
assert(a[-1]==3&&s[-2]==2&&d[-3]==1)
assert("é😀"[-1]==u8(0x80))
tail:="abc"[-2:];assert(tail=="bc")
assert(len(a[:-1])==2&&len(a[-2:-1])==1&&len(a[-3:])==3)
delete(d)
'''))

    def test_negative_lvalues_compound_addresses_and_nested_arrays(self):
        self.invoke(self.program('''
a:=[[1,2],[3,4]]; i:=-1
a[i][i]=8;a[-1][-2]+=5
p:=&a[i][i];p^+=1
assert(a[1][1]==9&&a[1][0]==8)
'''))

    def test_record_parameter_array_reads_keep_native_readonly_addressability(self):
        prefix='''Board::struct{cells:[2][2]int}
read::proc(board:Board,row,col:int)->int{return board.cells[row][col]}
generic_read::proc(board:$T,row,col:int)->int{return board.cells[row][col]}
'''
        self.invoke(self.program('''
b:=Board{cells={{1,2},{3,4}}}
assert(read(b,1,1)==4 && read(b,-1,-2)==3)
assert(generic_read(b,1,0)==3 && generic_read(b,-1,-1)==4)
assert(generic_read(&b,-1,-1)==4)
''',prefix))

    def test_reference_range_over_indexed_array_row_preserves_original_storage(self):
        prefix='''Board::struct{cells:[2][2]int}
write::proc(board:^Board,row:int){for &value in board.cells[row]{value+=10}}
generic_write::proc(board:$T,row:int){for &value in board.cells[row]{value+=100}}
'''
        self.invoke(self.program('''
b:=Board{cells={{1,2},{3,4}}}
write(&b,-1)
generic_write(&b,0)
assert(b.cells[0][0]==101 && b.cells[0][1]==102)
assert(b.cells[1][0]==13 && b.cells[1][1]==14)
''',prefix))

    def test_nested_temporary_array_reads_and_reference_headers_remain_valid(self):
        prefix='''Board::struct{cells:[2][2]int}
make_board::proc()->Board{return {cells={{1,2},{3,4}}}}
last::proc(values:$T,row,col:int)->int{return values[row][col]}
'''
        self.invoke(self.program('''
i:=-1
assert(make_board().cells[i][i]==4)
grid:=[2][2]int{{1,2},{3,4}}
assert(last(grid,i,i)==4 && last(grid[:],i,i)==4)
rows:=[2][]int{grid[0][:],grid[1][:]}
for &value in rows[i]{value+=5}
assert(grid[1][1]==9)
''',prefix))

    def test_reference_range_does_not_make_readonly_record_parameters_mutable(self):
        prefix='''Board::struct{cells:[2][2]int}
write::proc(board:Board,row:int){for &value in board.cells[row]{value=9}}
'''
        self.invoke(self.program('b:=Board{};write(b,0)',prefix),command='check',fail='temporary array')

    def test_negative_access_evaluation_order_once(self):
        self.invoke(self.program('''
result:=get()[bound(2)]
assert(result==3&&order==12)
order=0
part:=get()[bound(2):bound(3)]
assert(len(part)==1&&order==123)
''','''data:=[3]int{1,2,3}
order:int
get::proc()->[]int{order=order*10+1;return data[:]}
bound::proc(n:int)->int{order=order*10+n;return n==2 ? -1 : 3}
'''))

    def test_negative_index_signed_width_and_unsigned_preservation(self):
        self.invoke(self.program('''
a:[300]int;a[299]=7
i:i8=-1;assert(a[i]==7)
u:u128=299;assert(a[u]==7)
'''))

    def test_negative_map_keys_preserve_read_write_and_address_rules(self):
        self.invoke(self.program('m:=make(map[int]int);m[-1]=7;m[-1]+=1;assert(m[-1]==8);delete(m)'))

    def test_negative_generic_arrays_slices_maps_and_strings(self):
        self.invoke(self.program('''
a:=[1,2];assert(last(a)==2&&last(a[:])==2&&last("ab")=='b')
m:=make(map[int]int);m[-1]=9;assert(last(m)==9);delete(m)
''','last::proc(value:$T)->int{return int(value[-1])}\n'))

    def test_generic_negative_assignment_preserves_map_headers(self):
        self.invoke(self.program('''
m:=make(map[int]int);m[-1]=3; updated:=write(m); assert(updated[-1]==8);delete(updated)
a:=[1,2];view:=write(a[:]);assert(a[1]==8&&view[1]==8)
''','write::proc(value:$T)->T{x:=value;x[-1]=7;x[-1]+=1;return x}\n'))

    def test_negative_write_operand_order_once(self):
        self.invoke(self.program('get()[index()]+=rhs();assert(order==123&&data[1]==7)', '''
data:=[2]int{1,2}
order:int
get::proc()->[]int{order=order*10+1;return data[:]}
index::proc()->int{order=order*10+2;return -1}
rhs::proc()->int{order=order*10+3;return 5}
'''))

    def test_generic_multiple_assignment_keeps_original_lhs_values(self):
        self.invoke(self.program('''
a:=[4,5];i:=0;write(&a,&i);assert(i==1&&a[0]==9&&a[1]==5)
m:map[int]int;write(&m,&i);assert(i==1&&m[1]==9);delete(m)
''','write::proc(a:^$T,i:^int){i^,a^[i^]=1,9}\n'))

    def test_very_wide_negative_bound_does_not_wrap_into_a_valid_index(self):
        self.invoke(self.program('a:=[7,8];i:i128=-18446744073709551617;_=a[i]'),fail='Bounds check')

    def test_negative_fixed_array_readonly_and_slice_shallow_writes(self):
        self.invoke(self.program('data:=[1,2];a:>data;assert(a[-1]==2);s:>data[:];s[-1]=8;assert(data[1]==8)'))
        self.invoke(self.program('a:>[1,2];a[-1]=8'),command='check',fail='immutable storage')

    def test_enum_array_and_pointer_indices_keep_native_meaning(self):
        self.invoke(self.program('''
data:=[3,4];p:=&data;assert(p[1]==4)
multi:=raw_data(data[:]);assert(multi[1]==4)
e:[Color]int;e[.Blue]=9;assert(e[.Blue]==9)
value:=7;callable:=identity;fn:=&callable;assert(fn^(value)==7)
''','Color::enum{Red,Blue}\nidentity::proc(x:int)->int{return x}\n'))

    def test_negative_const_string_index_and_slice(self):
        self.invoke(self.program('assert(C==\'c\'&&S=="bc")','C::"abc"[-1]\nS::"abc"[-2:]\n'))

    def test_negative_bounds_are_not_clamped(self):
        self.invoke(self.program('a:=[1,2];_=a[-3]'),command='check',fail='out of bounds')
        self.invoke(self.program('a:=[1,2];i:=-3;_=a[i]'),fail='Bounds check')

    def test_generated_work_stays_in_lazy_branches_and_arguments(self):
        self.invoke(self.program('''
x:=0
result:=false && (switch {case next(&x)==1:true;case:false})
assert(!result&&x==0)
s:=true ? "borrowed" : f"{next(&x)}"
assert(s=="borrowed"&&x==0)
answer:=combine(next(&x),switch 1{case 1:next(&x);case:0},next(&x))
assert(answer==123&&x==3)
''','next::proc(x:^int)->int{x^+=1;return x^}\ncombine::proc(a,b,c:int)->int{return 100*a+10*b+c}\n'))

    def test_generated_work_in_loop_conditions_posts_and_do_bodies(self):
        self.invoke(self.program('''
i:=0
for (switch i {case 0,1,2:true;case:false}) { i+=1 }
assert(i==3)
for j:=0;j<3;j=switch j{case 0:1;case 1:2;case:3} do i+=1
assert(i==6)
for j:=0;j<2;j+=1 do use(f"{j}")
''','use::proc(s:string){delete(s)}\n'))

    def test_set_and_generic_index_updates_in_loop_posts(self):
        self.invoke(self.program('''
s:set[int];i:=0;for ;i<3;s+=i{i+=1};assert(len(s)==3);delete(s)
data:=[0,0];write(&data);assert(data[1]==3)
''','write::proc(data:^$T){i:=0;for ;i<3;data^[-1]+=1{i+=1}}\n'))

    def test_generated_helpers_in_native_switch_cases_and_nested_scopes(self):
        self.invoke(self.program('''
x:=2
switch x {case 2: local:=3;s:=f"{local}";assert(s=="3");delete(s)}
{x:=7;s:=f"{x}";assert(s=="7");delete(s)}
'''))

    def test_generated_names_do_not_shadow_user_bindings(self):
        self.invoke(self.program('''
builder:=3;capture_0:=4;field_1:=5;Result::int
s:=f"{builder} {capture_0} {field_1}"; assert(s=="3 4 5");delete(s)
x:Result=switch 1{case 1:capture_0;case:field_1};assert(x==4)
'''))

    def test_generic_native_contextless_operators_remain_contextless(self):
        self.invoke(self.program('assert(combine(u8(1),u8(2))==3)', '''
combine::proc "contextless"(a,b:$T)->T{x:=a|b;x-=0;x|=0;return x}
'''))
        self.invoke(self.program('a:[dynamic]int;append(&a,1,2);reset(&a);assert(len(a)==0);delete(a)',
                                 'reset::proc "contextless"(p:^$T){clear(p)}\n'))
        self.invoke(self.program('assert(mask(u8(2))==3);p:^int;assert(empty(p))', '''
mask::proc "contextless"(a:$T)->T{return 1|a}
empty::proc "contextless"(a:$T)->bool{return nil==a}
'''))

    def test_set_library_type_and_syntax_interoperate(self):
        self.invoke(self.program('''
a:sets.Set(int);a+=1;b:=set[int]{2};c:=a|b
assert(len(c)==2&&1 in c);delete(a);delete(b);delete(c)
''','import sets "thor:sets"\n'))

    def test_symmetric_difference_parenthesized_and_switch_operands(self):
        self.invoke(self.program('''
a:=set[int]{1,2};b:=set[int]{2,3}
c:=xor(a,b);d:=a ~ switch 1{case 1:b;case:a}
assert(c==d&&len(c)==2);delete(c);delete(d);delete(a);delete(b)
''','xor::proc(a,b:$T)->T{return a ~ (b)}\n'))


if __name__=='__main__': unittest.main()
