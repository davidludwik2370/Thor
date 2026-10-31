"""Stage two: real compiler, ownership, contextual literals, and diagnostics."""
import json
import subprocess
from pathlib import Path
from tests import test_thor as base


class BigLanguageTests(base.unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def run_big(self,body,prefix='',**kwargs):
        source=self.program(body,'''import "thor:big"
expect :: proc(value: $T, text: string) {
    actual := big.to_string(value)
    defer delete(actual)
    assert(actual == text)
}
'''+prefix.replace('#+feature dynamic-literals\n',''))
        result=self.invoke('#+feature dynamic-literals\n'+source,flags=kwargs.pop('flags',('--track-allocations=basic',)),**kwargs)
        self.assertEqual(result[1].stderr,'')
        return result

    def test_predefined_types_long_unquoted_and_exact_decimal_literals(self):
        huge='1234567890'*600
        self.run_big('''
a: Integer = '''+huge+'''
expect(a, '''+json.dumps(huge)+''')
b := Integer(-0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF)
expect(b,"-1461501637330902918203684832716283019655932542975")
r: Rational = 0.1000000000000000000000000000000000000001
expect(r,"1000000000000000000000000000000000000001/10000000000000000000000000000000000000000")
integral: Integer = 123.000
expect(integral,"123")
delete(a);delete(b);delete(r);delete(integral)
''')

    def test_zero_values_and_no_import_required(self):
        self.invoke(self.program('''
a: Integer
b: Rational
n := b.numerator
d := b.denominator
delete(a);delete(b);delete(n);delete(d)
'''),flags=('--track-allocations=basic',))

    def test_constructors_strings_named_arguments_aliases_and_machine_ranges(self):
        self.run_big('''
a,ok := Integer("123456789012345678901234567890")
assert(ok);expect(a,"123456789012345678901234567890")
r,r_ok := Rational("0.1(6)")
s,s_ok := Rational("-22","-13")
assert(r_ok && s_ok);expect(r,"1/6");expect(s,"22/13")
t := R(numerator=22,denominator=13)
expect(t,"22/13")
u := I(max(u128));expect(u,"340282366920938463463374607431768211455")
v := Rational(min(i128));expect(v,"-170141183460469231731687303715884105728/1")
bad,bad_ok := Integer("not a number");assert(!bad_ok);expect(bad,"0")
delete(a);delete(r);delete(s);delete(t);delete(u);delete(v);delete(bad)
''','I::Integer\nR::Rational\n')

    def test_direct_copies_reassignment_self_assignment_and_aliasing_targets(self):
        self.run_big('''
a := Integer(10)
b := a
a = Integer(20)
expect(a,"20");expect(b,"10")
a,b = b,Integer(11)
expect(a,"10");expect(b,"11")
a = a
expect(a,"10")
a,a = b,a
expect(a,"10");expect(b,"11")
for i in 0..<20 { a = Integer(i) }
expect(a,"19")
delete(a);delete(b)
''')

    def test_returned_values_borrowed_parameters_and_generic_copies(self):
        self.run_big('''
a:=Integer(42)
b:=identity(a)
c:=concrete(a)
delete(a)
expect(b,"42");expect(c,"42")
d:=factory();expect(d,"123")
e:=zero(Integer);expect(e,"0")
f:=zero(Rational);expect(f,"0/1")
delete(b);delete(c);delete(d);delete(e);delete(f)
''','''identity::proc(value:$T)->T { return value }
concrete::proc(value:Integer)->Integer { return value }
factory::proc()->Integer { value:=Integer(123);defer delete(value);return value }
zero::proc($T:typeid)->T {return 0}
''')

    def test_anonymous_arguments_getters_and_discarded_results_are_released(self):
        self.run_big('''
expect(Integer(123),"123")
expect(Rational(22,13).numerator,"22")
expect(Rational(22,13).denominator,"13")
_ = Integer(12)
Integer(13)
Integer("777")
Rational("1/3")
_ = Rational(1,3).numerator
_, ok := Integer("123")
assert(ok)
_, ok = Rational("1/3")
assert(ok)
''')

    def test_readonly_properties_and_pointer_access(self):
        self.run_big('''
r:=Rational(22,13)
p:=&r
n:=r.numerator
d:=p.denominator
big.set_fraction(&r,1,2)
expect(n,"22");expect(d,"13")
delete(r);delete(n);delete(d)
''')
        for body in ['r.numerator=12','r.denominator*=2','_=&r.numerator',
                     'p:=&r;p.denominator=0']:
            with self.subTest(body=body):
                self.invoke(self.program('r:=Rational(22,13);'+body),command='check',fail='read-only')

    def test_struct_construction_direct_members_and_shallow_aggregate_copy(self):
        self.run_big('''
a:=Integer(123)
first:=Holder{value=a}
second:=first // An intentional shallow aggregate copy, not a second owner.
direct:=first.value
delete(a)
expect(first.value,"123");expect(direct,"123")
first.value = Integer(42)
expect(first.value,"42")
delete(first.value);delete(direct)
_ = second // Its old allocation was invalidated; do not read or destroy it.
''','Holder::struct{value:Integer}\n')

    def test_arrays_negative_indices_and_element_copies(self):
        self.run_big('''
a:=Integer(1)
values:=[2]Integer{a,Integer(2)}
delete(a)
copy:=values[-1]
values[-1]=values[0]
expect(copy,"2");expect(values[-1],"1")
delete(copy)
for &value in values {delete(value)}
''')

    def test_map_assignment_captures_key_once_and_replaces_owned_values(self):
        self.run_big('''
values:=make(map[string]Integer)
defer delete(values)
values[key()]=Integer(123)
assert(calls==1)
values[key()]=values["a"]
assert(calls==2)
copy:=values["a"]
expect(copy,"123")
delete(copy);delete(values["a"])
''','calls:int\nkey::proc()->string{calls+=1;return "a"}\n')

    def test_globals_dependency_order_and_independent_owners(self):
        self.run_big('''
expect(first,"123");expect(second,"123");expect(global_r,"1/8")
first=Integer(42)
expect(second,"123")
delete(first);delete(second);delete(global_r)
''','first:Integer=123\nsecond:=first\nglobal_r:Rational=0.125\n')

    def test_generics_assignment_and_native_instantiations_preserve_ordinary_types(self):
        self.run_big('''
a:=Integer(1);b:=Integer(2)
swap(&a,&b)
expect(a,"2");expect(b,"1")
delete(a);delete(b)
x,y:=1,2;swap(&x,&y);assert(x==2&&y==1)
text:=identity("native");assert(text=="native")
''','''swap::proc(a,b:^$T){a^,b^=b^,a^}
identity::proc(x:$T)->T{return x}
''')

    def test_lazy_branches_and_switch_value_copies(self):
        self.run_big('''
a:=Integer(123)
b:=true ? a : Integer(42)
c:=switch {case true:a;case:Integer(42)}
delete(a)
expect(b,"123");expect(c,"123")
delete(b);delete(c)
''')

    def test_static_errors_and_native_overflow_do_not_promote(self):
        cases=[('a:=Rational(1,0)','denominator must not be zero'),
               ('a:=Rational(1,-0.0)','denominator must not be zero'),
               ('a:=Rational(1,Integer(0))','denominator must not be zero'),
               ('a:=Rational("1","0")','denominator must not be zero'),
               ('a:Integer=1.25','whole number'),
               ('a:=Integer(f64(1))','exact integer inputs'),
               ('a:=max(u128)*2','Error:'),
               ('a:=Integer(1);_=a._value','backing storage is private'),
               ('r:=Rational(1,2);_=r._numerator','backing storage is private'),
               ('a:=Integer(1);p:=&a;_=p._value','backing storage is private')]
        for source,message in cases:
            with self.subTest(source=source):
                self.invoke(self.program(source),command='check',fail=message)

    def test_multiple_results_after_native_first_result_and_map_destinations(self):
        self.run_big('''
flag,value:=pair()
assert(flag);expect(value,"123")
flag,value=pair()
values:=make(map[string]Integer)
flag,values["a"]=pair()
assert(flag);expect(values["a"],"123")
delete(values["a"]);expect(values["a"],"0")
delete(values);delete(value)
flag,_=pair()
flag2,_:=pair();assert(flag2)
''','''pair::proc()->(bool,Integer){return true,Integer(123)}
''')

    def test_global_parsed_results_are_initialized_once(self):
        self.run_big('''expect(global_i,"123");assert(global_ok);delete(global_i)
assert(global_only_ok)
''','''global_i,global_ok:=Integer("123")
_,global_only_ok:=Rational("1/3")
''')

    def test_multiple_switch_results_and_skip_assignment_preserve_ownership(self):
        self.run_big('''
a:=Integer(1);b:=Integer(2)
a,b=switch{case true:b,a;case:Integer(3),Integer(4)}
expect(a,"2");expect(b,"1")
a=switch{case false:Integer(9);case:break}
expect(a,"2")
a,b=switch{case false:Integer(9),Integer(8);case:break}
expect(a,"2");expect(b,"1")
x,y:=switch{case true:a,Integer(7);case:b,Integer(9)}
expect(x,"2");expect(y,"7")
delete(x);delete(y);delete(a);delete(b)
''')

    def test_map_literal_clones_individual_values(self):
        self.run_big('''
a:=Integer(123)
values:=map[string]Integer{"a"=a,"b"=456}
delete(a)
expect(values["a"],"123");expect(values["b"],"456")
delete(values["a"]);delete(values["b"]);delete(values)
''','#+feature dynamic-literals\n')

    def test_borrowed_values_cannot_be_deleted_and_generic_owned_deletion(self):
        for prefix,body in [
            ('bad::proc(value:Integer){delete(value)}','bad(Integer(1))'),
            ('','values:=[1]Integer{Integer(123)};for value in values{delete(value)}'),
        ]:
            self.invoke(self.program(body,prefix),command='check',fail='borrowed')
        self.run_big('''a:=Integer(123);clear(&a);expect(a,"0");delete(a)
text:=make([]int,3);clear(&text)
''','''clear::proc(value:^$T){delete(value^)}
''')
        for prefix in ['Tagged::distinct Integer','M::map[Integer]int','S::set[Rational]{}']:
            self.invoke(self.program('',prefix),command='check',fail='not supported yet')

    def test_dynamic_append_establishes_individual_owners(self):
        self.run_big('''
a:=Integer(123)
values:=make([dynamic]Integer)
append(&values,a,Integer(456),789)
delete(a)
expect(values[0],"123");expect(values[1],"456");expect(values[2],"789")
for &value in values{delete(value)}
delete(values)
''')

    def test_named_results_and_conditional_owned_returns(self):
        self.run_big('''
a:=factory();expect(a,"123");delete(a)
b:=Integer(4)
c:=choose(b,false);d:=choose(b,true)
delete(b);expect(c,"4");expect(d,"5");delete(c);delete(d)
''','''factory::proc()->(result:Integer){result=Integer(123);return}
choose::proc(value:Integer,create:bool)->Integer{return create?Integer(5):value}
''')

    def test_constructor_argument_names_allocators_and_borrowed_aggregate_elements(self):
        self.run_big('''
a:=Integer(value=123,allocator=context.allocator)
b:=Rational(numerator=13,denominator=22,allocator=context.allocator)
c:=Rational(b)
expect(a,"123");expect(b,"13/22");delete(b);expect(c,"13/22");delete(a);delete(c)
''')
        for body,prefix,message in [
            ('_=Integer(wrong=123)','','constructor argument name'),
            ('_=Rational(numerator=1)','','constructor argument name'),
            ('_=Integer(text=1)','','must be a string'),
            ('values:=[1]Integer{Integer(1)};bad(values)', 'bad::proc(values:[1]Integer){delete(values[0])}', 'borrowed'),
            ('value:=Integer(1);bad(value)','bad::proc(value:$T){delete(value)}','borrowed'),
        ]:
            self.invoke(self.program(body,prefix),command='check',fail=message)
        self.run_big('values:=make([]int,3);consume(values)', 'consume::proc(value:$T){delete(value)}')

    def test_commented_language_demo_in_debug_mode_has_no_leaks(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([str(root/'thor'),'run',str(root/'examples/big_numbers'),'-debug'],
                              text=True,capture_output=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stderr,'')
        self.assertIn('All Integer/Rational stage-two examples passed.',result.stdout)

    def test_optional_fallbacks_returns_and_loop_branches_own_selected_values(self):
        self.run_big('''
source:=Integer(123)
a:=Integer("4") or_else source
b:=Integer("bad") or_else source
values:=make(map[string]Integer)
values["a"]=source
c:=values["a"] or_else Integer(0)
d:=values["missing"] or_else source
delete(source);delete(values["a"]);delete(values)
expect(a,"4");expect(b,"123");expect(c,"123");expect(d,"123")
delete(a);delete(b);delete(c);delete(d)
success,ok:=read("88");assert(ok);expect(success,"88");delete(success)
failure,failure_ok:=read("bad");assert(!failure_ok);delete(failure)
for text in ["bad","3"] { value:=Integer(text) or_continue;expect(value,"3");delete(value) }
_,false_ok:=early();assert(!false_ok)
assert(consume("123"));assert(!consume("bad"))
_ = Integer("7") or_else Integer(0)
''','''read::proc(text:string)->(result:Integer,ok:bool){value:=Integer(text) or_return;defer delete(value);return value,true}
early::proc()->(result:Integer,ok:bool){value:=failed() or_return;defer delete(value);return value,true}
failed::proc()->(Integer,bool){return Integer(456),false}
consume::proc(text:string)->bool{_ = Integer(text) or_return;return true}
''')

    def test_debug_trace_build_preserves_optional_and_component_ownership(self):
        self.run_big('''
value,ok:=Integer("123");assert(ok)
copy:=Integer("bad") or_else value
r:Rational=0.125
n:=r.numerator
expect(copy,"123");expect(n,"1")
delete(value);delete(copy);delete(r);delete(n)
''',flags=('-debug','--track-allocations=trace'))

    def test_immutable_numeric_owners_still_clone_and_allow_final_cleanup(self):
        self.run_big('''
source:=Integer(123)
frozen:>source
typed:Integer > 456
copy:=frozen
delete(source)
expect(frozen,"123");expect(copy,"123");expect(typed,"456")
delete(frozen);delete(copy);delete(typed)
''')

    def test_predefined_type_names_are_shadowable(self):
        self.invoke(self.program('a:=Integer{5};assert(a.value==5);assert(value(12)==12)',
            'Integer::struct{value:int}\nvalue::proc(Rational:int)->int{return Rational}\n'))
