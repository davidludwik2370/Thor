"""Power precedence, native numeric contracts, folding and interactions."""
import json
import subprocess
import unittest
from tests import test_thor as base


class PowerTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_precedence_and_right_association(self):
        self.invoke(self.program('''
assert(2**3**2==512)
assert(-2**2 == -4 && (-2)**2==4 && +2**3==8)
assert(2*3**2+1==19 && 2**3*2==16)
assert(2**-3.0==0.125 && 2.0**-3==0.125)
assert(2.0**-2**2==0.0625)
assert((2**3)**2==64)
assert(3 in 0..<2**3 && 8 not_in 0..<2**3)
'''))

    def test_matching_runtime_types_and_literal_coercion(self):
        self.invoke(self.program('''
a:int=2;b:int=3;assert(a**b==8)
x:f64=2;y:f64 = -3;assert(x**y==.125)
z:f32=2;assert(type_of(z**3)==f32 && z**-3==.125)
assert(2**y==.125 && type_of(2**y)==f64)
small:i8=2;assert(type_of(small**3)==i8 && small**3==8)
wide:u128=2;assert(wide**100==(u128(1)<<100))
be:u32be=3;assert(be**2==9)
value:Count=2;exponent:Count=3;assert(type_of(value**exponent)==Count && value**exponent==8)
float:Fraction=2;assert(type_of(float**-3)==Fraction && float**-3==.125)
''','Count::distinct int\nFraction::distinct f32\n'))

    def test_typed_mismatches_are_rejected(self):
        for declarations in ['a:int=2;b:f64=3', 'a:f64=2;b:int=3',
                             'a:i32=2;b:i64=3','a:f32=2;b:f64=3',
                             'a:Count=2;b:int=3']:
            with self.subTest(declarations=declarations):
                self.invoke(self.program(declarations+';_=a**b','Count::distinct int\n'),command='check',fail='Error:')

    def test_unsupported_operands_and_negative_integer_constants(self):
        for expression in ['true**false','"a"**"b"','[1,2]**[2,3]', '2**-3', '2**1000']:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression),command='check',fail='Error:')

    def test_negative_integer_runtime_exponent_panics_even_without_asserts(self):
        self.invoke(self.program('a:=2;b:=-3;_=a**b'),fail='Integer power requires',flags=['-disable-assert'])

    def test_zero_fractional_and_runtime_width_semantics(self):
        self.invoke(self.program('''
assert(0**0==1 && 7**0==1 && 0**5==0)
assert(1**(2**100)==1 && (-1)**(2**100)==1 && 0**(2**100)==0)
a:=0;b:=0;assert(a**b==1)
x:f64=9;y:f64=.5;assert(x**y==3)
assert(9.0**.5==3 && 0.0**0==1)
small:u8=16;exponent:u8=2;assert(small**exponent==0)
'''))

    def test_operands_execute_once_left_to_right_and_stay_lazy(self):
        self.invoke(self.program('''
order:=0
assert(mark(&order,2)**mark(&order,3)**mark(&order,2)==512)
assert(order==232)
a:=2
assert(a**change(&a)==8 && a==9)
order=0
assert(true || mark(&order,2)**mark(&order,3)==8);assert(order==0)
assert((false ? mark(&order,2)**3 : 7)==7 && order==0)
count:=0
for i:=0;i<2;i=mark(&count,i+1)**1 {}
assert(count==12)
''','''
mark::proc(order:^int,value:int)->int{order^=order^*10+value;return value}
change::proc(value:^int)->int{value^=9;return 3}
'''))

    def test_constants_config_array_sizes_and_literal_result_coercion(self):
        self.invoke(self.program('''
assert(SIZE==8 && len(data)==8 && VALUE==.125 && ONE==1 && SMALL==0)
small:i8=SIZE;assert(small==8)
wide:u128=2**100;assert(wide==(u128(1)<<100))
when 2**3==8 {assert(true)} else {undefined_unselected()}
CHOICE::#switch {case 2**3 in 0..<10:7;case:9}
assert(CHOICE==7)
''','''
SIZE::2**#config(EXPONENT,3)
VALUE::2.0**-3
ONE::u128(1)**max(u128)
SMALL::u8(16)**u8(255)
data:[2**3]int
'''))

    def test_fn_run_generics_and_constraints(self):
        self.invoke(self.program('''
assert(AUTO==8 && EXPLICIT==.125)
assert(power(3,4)==81 && power(f32(2),f32(-3))==.125)
assert(sized(3)==9 && bounded(7)==7)
assert(constant_power(2,3)==8)
record:Grid(3);assert(len(record.data)==9)
''','''
Grid::struct($N:int) {data:[N**2]int}
power::fn(a,b:$T)->T{return a**b}
AUTO::power(2,3)
EXPLICIT::#run power(2.0,-3.0)
sized::proc($N:int)->int {X::N**2;return X}
bounded::proc($N:int)->int where N in 0..<2**3 {return N}
constant_power::proc($N:int,$E:int)->int where N**E < 100 {return N**E}
'''))

    def test_interpolation_switch_negative_indices_and_contextless(self):
        self.invoke(self.program('''
text:=f"power {2**3} {(2.0**-3)}";assert(text=="power 8 0.125");delete(text)
values:=[2,3];assert(values[-2]**values[-1]==8)
a:=switch 1{case 1:2;case:3};assert(a**3==8)
assert(callback(2,3)==8)
''','callback::proc "c"(a,b:int)->int{return a**b}\n'))

    def test_inactive_branches_do_not_instantiate_invalid_power(self):
        self.invoke(self.program('''
when false {bad::2**-3} else {assert(true)}
assert(true || 2**-3==0)
'''))

    def test_type_queries_do_not_execute_power_or_its_operands(self):
        source=self.program('''
order:=0
if type_of(2**-3)!=int || type_of(mark(&order,2)**3)!=int || order!=0 {
    panic("power type query evaluated its operands")
}
record:Grid(2)
if len(record.data)!=256 {panic("wrong generic array length")}
''','mark::proc(order:^int,value:int)->int{order^+=1;return value}\nGrid::struct($N:int){data:[N**(2**3)]int}\n')
        for flags in (['-debug'], ['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']):
            with self.subTest(flags=flags):
                self.invoke(source,flags=flags)

    def test_float_special_values_survive_constant_reconstruction(self):
        self.invoke(self.program('assert(math.is_nan(NAN) && math.is_inf(INFINITY,1))',
            'import "core:math"\nNAN::(-1.0)**0.5\nINFINITY::0.0**-1\n'))

    def test_float_power_cache_hits_and_exponent_invalidation(self):
        source=self.program('assert(VALUE==.125)','VALUE::2.0**-3\n')
        root,first=self.invoke(source,flags=['--cache-report'])
        self.assertEqual(json.loads(first.stdout)['thor_cache']['writes'],1)
        entry=root/'input/main.thor'
        def again():
            result=subprocess.run([str(base.THOR),'run',str(entry),'--output-dir',str(root/'output'),'--cache-report'],
                                  capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)['thor_cache']
        self.assertEqual(again()['hits'],1)
        entry.write_text(source.replace('**-3','**-4').replace('VALUE==.125','VALUE==.0625'))
        self.assertEqual(again()['writes'],1)

    def test_unresolved_type_level_exponent_has_an_explicit_diagnostic(self):
        self.invoke(self.program('value:Grid(2,3);_=value',
            'Grid::struct($N:int,$E:int){data:[N**E]int}\n'),command='check',fail='fixed nonnegative integer exponent')

    def test_native_expansion_and_multiplication_unchanged(self):
        self.invoke(self.program('''
pair:=Pair{2,3};assert(sum(**pair)==5)
ptr:=&pair.a;assert(ptr^*pair.b==6)
assert(sum(2**3,4)==12)
''','Pair::struct{a,b:int}\nsum::proc(a,b:int)->int{return a+b}\n'))
