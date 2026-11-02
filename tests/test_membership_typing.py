"""Contextual membership literals, without implicit casts of typed operands."""
import unittest

from tests import test_thor as base


class MembershipTypingTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_puzzle_narrow_integer_and_parenthesized_not_in(self):
        self.invoke(self.program('''
startTile:i8=4
if startTile in [4,8] do startTile-=1
assert(startTile==3&&startTile not_in (([4,8])))
assert(startTile in [3]&&startTile not_in [])
'''))

    def test_integer_widths_signedness_distinct_and_float_types(self):
        for typ, value, literal in [('i8','-4','[-4,8]'),('u8','4','[4,255]'),
                                    ('i16','400','[400,800]'),('u16','400','[400,65535]'),
                                    ('i128','1<<100','[1<<100,1<<110]'),
                                    ('u128','1<<127','[1<<127,1<<126]'),
                                    ('f32','4.5','[4,4.5]'),('f64','4.5','[4.5,8]'),
                                    ('Count','4','[4,8]')]:
            with self.subTest(typ=typ):
                self.invoke(self.program(f'value:{typ}={value};assert(value in {literal})',
                                         'Count::distinct i8\n'))

    def test_constant_expressions_aliases_and_imported_untyped_constants(self):
        self.invoke(self.program('''
value:i8=4
assert(value in [FOUR,TWICE*2])
assert(value in [lib.FOUR,lib.TWO*4])
assert(value in [-(-FOUR),(1<<3)])
assert(TYPED in [FOUR,8])
''', 'import "lib"\nFOUR::4\nTWICE::2\nTYPED::i8(4)\n'),
                    files={'lib/main.thor':'package lib\nFOUR::4\nTWO::2\n'})

    def test_native_library_constants_keep_untyped_literal_behavior(self):
        self.invoke(self.program('assert(math.PI not_in [3.0,4.0]&&PI not_in [3.0,4.0])',
                                 'import "core:math"\nPI::math.PI\n'))
        self.invoke(self.program('_=math.PI in [1,2]','import "core:math"\n'),command='check',fail='Error:')

    def test_literal_needle_uses_existing_container_type(self):
        self.invoke(self.program('''
nums:=[1,2,3]
small:=[2]i8{4,8}
view:=small[:]
growing:[dynamic]i8;append(&growing,4,8);defer delete(growing)
fixed:[dynamic;4]i8;append(&fixed,4,8)
assert(6 not_in nums&&2 in nums)
assert(4 in small&&6 not_in small&&4 in view&&6 not_in view)
assert(4 in growing&&6 not_in growing&&8 in fixed&&6 not_in fixed)
assert(FOUR in small&&2*2 in view)
''', 'FOUR::4\n'))

    def test_literal_and_typed_operands_keep_normal_default_rules(self):
        self.invoke(self.program('''
assert(1 in [1,2]&&1.5 in [1.5,2]&&2.0 in [1.5,2])
assert(1 in [f32(1),2])
values:=[4,8];#assert(type_of(values)==[2]int)
explicit:=[i8(4),8];#assert(type_of(explicit)==[2]i8)
assert(4 in values&&4 in explicit)
assert(len("ab") not_in [1.5,2.5])
assert(size_of(i8) in [1.0,2.5])
assert(abs(-2) in [1.5,2.0])
array:[3]i8;assert(len(array) in [2.5,3.0])
'''))
        for body in ['_=1.5 in [1,2]', '_=1 in [1,2.5]']:
            with self.subTest(body=body):self.invoke(self.program(body),command='check',fail='Error:')

    def test_existing_typed_values_are_not_converted(self):
        for body in [
            'value:i8=4;values:=[4,8];_=value in values',
            'value:i8=4;_=value in [int(4),8]',
            'value:i8=4;_=value in [4,i16(8)]',
            'value:i8=4;other:i16=4;_=value in [other,8]',
            'value:Count=4;other:i8=4;_=value in [other,8]',
            'value:f32=4;_=value in [f64(4),8]',
            'value:=4;values:=[2]i8{4,8};_=value in values',
        ]:
            with self.subTest(body=body):
                self.invoke(self.program(body,'Count::distinct i8\n'),command='check',fail='Error:')

    def test_comparison_and_logical_needles_keep_native_untyped_boolean_context(self):
        self.invoke(self.program('''
value:=4
assert((value==4) in [b8(true),false])
assert((value<8) in [b8(true),false])
assert((true&&false) in [b8(false),true])
values:[2]b8={true,false};assert((value==4) in values)
'''))
        self.invoke(self.program('value:=true;_=value in [b8(true),false]'),command='check',fail='Error:')

    def test_representability_signedness_and_fractional_values(self):
        for typ, literal in [('i8','[4,128]'),('i8','[-129,4]'),('u8','[-1,4]'),
                             ('u8','[4,256]'),('i8','[4,4.5]'),('f32','[4,1e100]')]:
            with self.subTest(typ=typ,literal=literal):
                self.invoke(self.program(f'value:{typ}=4;_=value in {literal}'),command='check',fail='Error:')

    def test_arithmetic_conditionals_fields_indices_dereferences_and_calls(self):
        self.invoke(self.program('''
value:i8=4
record:=Record{4};values:=[2]i8{4,8};pointer:=&value
assert(1+value in [5,9]&&value+1 in [5,9])
assert((true?value:i8(8)) in [4,8])
assert((1<<u8(2)) in [4,8])
assert(record.value in [4,8]&&values[0] in [4,8]&&pointer^ in [4,8])
assert(get() in [4,8]&&generic(value) in [4,8])
''','Record::struct{value:i8}\nget::proc()->i8{return 4}\ngeneric::proc(value:$T)->T{return value}\n'))

    def test_enum_rune_string_pointer_and_nested_array_contexts(self):
        self.invoke(self.program('''
color:Color=.Blue;assert(color in [.Red,.Blue]&&.Blue in [Color.Red,Color.Blue])
letter:rune='界';assert(letter in ['a','界'])
text:Text="hello";assert(text in ["hello","world"])
value:=4;pointer:=&value;assert(pointer in [nil,pointer]&&nil in [pointer,nil])
pair:=[2]i8{1,2};assert(pair in [[1,2],[3,4]])
record:=Record{4};assert(record in [{4},{8}])
''','Color::enum{Red,Blue}\nText::distinct string\nRecord::struct{value:i8}\n'))

    def test_maps_bitsets_and_sets_keep_key_or_element_context(self):
        self.invoke(self.program('''
mapping:map[i8]int;defer delete(mapping);mapping[4]=1
bits:bit_set[0..<10]={4,8}
names:=set[i8]{4,8};defer delete(names)
assert(4 in mapping&&6 not_in mapping&&4 in bits&&6 not_in bits)
assert(4 in names&&6 not_in names)
'''))

    def test_compile_time_constants_conditions_and_generic_constraints(self):
        self.invoke(self.program('''
assert(FOUND&&!MISSING&&SELECTED==7&&allowed(4)==4&&shape(i8(8)))
#assert(TYPED in [4,8])
when TYPED in [4,8] {assert(true)} else {missing()}
when TYPED not_in [1,2] {assert(true)}
''','''
TYPED::i8(4)
FOUND::TYPED in [4,8]
MISSING::TYPED in [2,8]
SELECTED::#switch{case TYPED in [4,8]:7;case:panic("unreachable")}
allowed::proc($N:i8)->i8 where N in [4,8]{return N}
shape::proc(value:$T)->bool where T in [i8,i16]{return value in [4,8]}
'''))

    def test_runtime_pure_automatic_and_run_evaluation(self):
        self.invoke(self.program('''
value:i8=4
assert(inside(value)&&AUTO&&EXPLICIT&&PROC)
''','''
inside::fn(value:i8)->bool{return value in [4,8]}
ordinary::proc(value:i8)->bool{return value not_in [2,6]}
AUTO::inside(i8(4))
EXPLICIT::#run inside(i8(4))
PROC::#run ordinary(i8(4))
'''))

    def test_constant_context_failures_and_inactive_branches_remain_lazy(self):
        for prefix in ['BAD::i8(4) in [4,128]\n',
                       'BAD::#run inside(i8(4))\ninside::fn(value:i8)->bool{return value in [4,128]}\n']:
            with self.subTest(prefix=prefix):self.invoke(self.program('_=BAD',prefix),command='check',fail='128')
        self.invoke(self.program('''
when false {value:i8=4;_=value in [4,128]}
when false {value:i8=4;_=value in [4,missing]}
assert(OK)
''','OK::#switch{case true:true;case:i8(4) in [4,128]}\n'))

    def test_eager_elements_once_in_source_order_and_lazy_enclosing_conditions(self):
        self.invoke(self.program('''
order:=0
assert(mark(&order,4) in [mark(&order,4),mark(&order,8)])
assert(order==448)
order=0
assert(!(false&&(mark(&order,4) in [mark(&order,4),mark(&order,8)])))
assert(true||(mark(&order,4) not_in [mark(&order,4),mark(&order,8)]))
assert(order==0)
assert(mark(&order,4) not_in []&&order==4)
value:i8=4
assert(value in [change(&value),8]&&value==8)
''','''
mark::proc(order:^int,value:i8)->i8{order^=order^*10+int(value);return value}
change::proc(value:^i8)->i8{value^=8;return 4}
'''))

    def test_nested_switch_interpolation_loops_and_native_contextless_code(self):
        self.invoke(self.program('''
value:i8=4
selected:=switch{case value in [4,8]:7;case:panic("bad value")}
text:=f"{value in [4,8]}:{selected}";assert(text=="true:7");delete(text)
for item in [i8(4),8]{assert(item in [4,8])}
assert(contextless(value))
''','contextless::proc "contextless"(value:i8)->bool{return value in [4,8]}\n'))

    def test_owned_operands_cleanup_and_inline_record_context(self):
        self.invoke('''package main
import "core:testing"
@(test) owned::proc(t:^testing.T){
 for i in 0..<3 {
  testing.expect(t,f"value {i}" in ["value 0","value 1","value 2"])
  testing.expect(t,4 in (set[i8]{4,8}))
  testing.expect(t,(switch{case true:i8(4);case:panic("bad")}) in [4,8])
 }
}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_empty_and_type_lists_preserve_resolution_and_constraints(self):
        self.invoke(self.program('''
value:i8=4;assert(value not_in []&&4 not_in [])
assert(i8 in [i8,i16]&&int not_in [i8,i16])
'''))
        self.invoke(self.program('_=missing in []'),command='check',fail='Undeclared')
        self.invoke(self.program('_=allowed(i8(6))',
                                 'allowed::proc($N:i8)->i8 where N in [4,8]{return N}\n'),
                    command='check',fail='where')


if __name__ == '__main__':
    unittest.main()
