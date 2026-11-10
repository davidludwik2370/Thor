"""Runtime logical values follow direct-copy and borrowed-call ownership rules."""
import json
import unittest
from tests import test_rust_checked_calls as checked


class RustBigLoweringTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    tracked=checked.RustCheckedCallTests.tracked
    program=checked.RustCheckedCallTests.program

    def numeric(self,body,prefix=''):
        return '#+feature dynamic-literals\n'+self.program(body,'''import "thor:big"
expect::proc(value:$T,text:string){actual:=big.to_string(value);defer delete(actual);if actual!=text{panic(actual)}}
'''+prefix)

    def test_literals_and_zero_default_properties(self):
        huge='1234567890'*600
        self.tracked(self.numeric('''a:Integer='''+huge+'''
expect(a,'''+json.dumps(huge)+''')
b:=Integer(-0xffffffffffffffffffffffffffffffff)
expect(b,"-340282366920938463463374607431768211455")
r:Rational=0.10000000000000000000000001
expect(r,"10000000000000000000000001/100000000000000000000000000")
i:Integer=123.000;expect(i,"123")
z:Integer;q:Rational;n:=q.numerator;d:=q.denominator
expect(z,"0");expect(q,"0/1");expect(n,"0");expect(d,"1")
delete(a);delete(b);delete(r);delete(i);delete(z);delete(q);delete(n);delete(d)
'''))

    def test_constructor_strings_named_arguments_and_native_boundaries(self):
        self.tracked(self.numeric('''a,ok:=Integer("123456789012345678901234567890")
r,r_ok:=Rational("0.1(6)");s,s_ok:=Rational("-22","-13")
if !ok||!r_ok||!s_ok{panic("parse")}
expect(a,"123456789012345678901234567890");expect(r,"1/6");expect(s,"22/13")
t:=R(numerator=22,denominator=13);expect(t,"22/13")
u:=I(max(u128));v:=Rational(min(i128))
expect(u,"340282366920938463463374607431768211455");expect(v,"-170141183460469231731687303715884105728/1")
bad,bad_ok:=Integer("bad");if bad_ok{panic("bad parse")};expect(bad,"0")
delete(a);delete(r);delete(s);delete(t);delete(u);delete(v);delete(bad)
''','I::Integer\nR::Rational\n'))

    def test_copies_simultaneous_assignments_and_aliased_targets(self):
        self.tracked(self.numeric('''a:=Integer(10);b:=a
a=Integer(20);expect(a,"20");expect(b,"10")
a,b=b,Integer(11);expect(a,"10");expect(b,"11")
a=a;a,a=b,a;expect(a,"10");expect(b,"11")
for i in 0..<20{a=Integer(i)};expect(a,"19")
delete(a);delete(b)
'''))

    def test_operators_updates_and_fibonacci(self):
        self.tracked(self.numeric('''a,b:Integer=0,1
for i in 0..<20{a,b=b,a+b};expect(a,"6765");expect(b,"10946")
a+=2;a*=3;a-=1;a/=2;expect(a,"10150")
r:=Rational(1,3);r+=2;r*=3;expect(r,"7/1")
if a>=b||!(r==7)||r!=7{panic("compare")}
x:=Integer(1)<<128;expect(x,"340282366920938463463374607431768211456")
delete(a);delete(b);delete(r);delete(x)
'''))

    def test_generic_and_concrete_returns_clone_borrowed_values(self):
        self.tracked(self.numeric('''a:=Integer(42);b:=identity(a);c:=concrete(a);delete(a)
expect(b,"42");expect(c,"42");d:=factory();expect(d,"123")
e:=zero(Integer);f:=zero(Rational);expect(e,"0");expect(f,"0/1")
delete(b);delete(c);delete(d);delete(e);delete(f)
''','''identity::proc(value:$T)->T{return value}
concrete::proc(value:Integer)->Integer{return value}
factory::proc()->Integer{value:=Integer(123);defer delete(value);return value}
zero::proc($T:typeid)->T{return 0}
'''))

    def test_record_elements_are_owned_but_aggregate_copies_stay_shallow(self):
        self.tracked(self.numeric('''a:=Integer(123);first:=Holder{value=a};second:=first
value:=first.value;delete(a);expect(first.value,"123");expect(value,"123")
first.value=Integer(42);expect(first.value,"42");delete(first.value);delete(value);_=second
''','Holder::struct{value:Integer}\n'))

    def test_array_elements_copies_and_negative_index_replacement(self):
        self.tracked(self.numeric('''a:=Integer(1);values:=[2]Integer{a,Integer(2)};delete(a)
copy:=values[-1];values[-1]=values[0]
expect(copy,"2");expect(values[-1],"1");delete(copy)
for &value in values{delete(value)}
values2:=[Integer(3),Integer(4)];for &value in values2{delete(value)}
'''))

    def test_map_replacement_captures_keys_once(self):
        self.tracked(self.numeric('''values:=make(map[string]Integer);defer delete(values)
values[key()]=Integer(123);if calls!=1{panic("key count")}
values[key()]=values["a"];if calls!=2{panic("key count")}
copy:=values["a"];expect(copy,"123");delete(copy);delete(values["a"])
''','calls:int\nkey::proc()->string{calls+=1;return "a"}\n'))

    def test_globals_initialize_in_default_context_and_clone(self):
        self.tracked(self.numeric('''expect(first,"123");expect(second,"123");expect(global_r,"1/8")
first=Integer(42);expect(second,"123");delete(first);delete(second);delete(global_r)
''','first:Integer=123\nsecond:=first\nglobal_r:Rational=0.125\n'))

    def test_generic_numeric_operators_preserve_native_instantiations(self):
        self.tracked(self.numeric('''a:=Integer(5);b:=advance(a);delete(a);expect(b,"8");delete(b)
if advance(5)!=8{panic("native arithmetic")}
values:=make([]int,3);consume(values)
''','advance::proc(value:$T)->T{copy:=value;defer big.compiler_destroy(&copy);copy+=3;return copy}\nconsume::proc(value:$T){delete(value)}\n'))

    def test_record_and_sequence_literals_keep_context_for_numeric_elements(self):
        self.tracked(self.numeric('''row:=Holder{1};expect(row.value,"1");delete(row.value)
values:=[2]Integer{1,2};expect(values[0],"1");expect(values[1],"2")
for &value in values{delete(value)}
''','Holder::struct{value:Integer}\n'))

    def test_numeric_contract_errors_refer_to_source(self):
        cases=[('a:Integer=1.25','whole number'),('_=Integer(text=1)','must be a string'),
               ('_=Rational(1,0)','denominator must not be zero'),
               ('a:=Integer(1);_=a/0','divisor must not be zero'),
               ('a:=Integer(1);_=a<<-1','shift counts must be nonnegative'),
               ('a:=Rational(1,2);_=a&1','Rational does not support bitwise'),
               ('a:=Rational(1,2);a.numerator=3','read-only'),
               ('a:=Integer(1);bad(a)','borrowed')]
        for body,message in cases:
            with self.subTest(body=body):
                self.compare(self.numeric(body,'bad::proc(value:Integer){delete(value)}\n'),command='check',fail=message)

    def test_anonymous_arguments_properties_and_multiple_discarded_results(self):
        self.tracked(self.numeric('''expect(Integer(123),"123");expect(Rational(22,13).numerator,"22")
expect(Rational(22,13).denominator,"13")
_=Integer(12);Integer(13);Integer("777");Rational("1/3");_=Rational(1,3).numerator
_,ok:=Integer("123");if !ok{panic("parse")};_,ok=Rational("1/3");if !ok{panic("parse")}
'''))

    def test_dynamic_append_clones_and_comprehension_elements_own_values(self):
        self.tracked(self.numeric('''a:=Integer(123);values:=make([dynamic]Integer)
append(&values,a,Integer(456),789);delete(a)
expect(values[0],"123");expect(values[1],"456");expect(values[2],"789")
copy:=[value for value in values];for &value in values{delete(value)};delete(values)
expect(copy[0],"123");for &value in copy{delete(value)};delete(copy)
'''))

    def test_optional_selection_clones_borrowed_map_values(self):
        self.tracked(self.numeric('''values:=make(map[string]Integer);defer delete(values)
values["a"]=Integer(12)
a:=values["a"] or_else Integer(20);b:=values["b"] or_else 20
c:=Integer("31") or_else 20;d:=Integer("bad") or_else 20
expect(a,"12");expect(b,"20");expect(c,"31");expect(d,"20")
delete(values["a"]);expect(a,"12");delete(a);delete(b);delete(c);delete(d)
'''))

    def test_optional_branches_keep_caller_control_flow_and_failed_owner_cleanup(self):
        self.tracked(self.numeric('''for text in ["bad","3"]{value:=Integer(text) or_continue;expect(value,"3");delete(value)}
a,ok:=read("7");if !ok{panic("read")};expect(a,"7");delete(a)
b,b_ok:=early();if b_ok{panic("early")};expect(b,"0");delete(b)
if consume("bad")||!consume("3"){panic("discard optional")}
c:=Integer("8") or_panic;expect(c,"8");delete(c)
''','''read::proc(text:string)->(result:Integer,ok:bool){value:=Integer(text) or_return;defer delete(value);return value,true}
early::proc()->(result:Integer,ok:bool){value:=failed() or_return;defer delete(value);return value,true}
failed::proc()->(Integer,bool){return Integer(999),false}
consume::proc(text:string)->bool{_=Integer(text) or_return;return true}
'''))

    def test_predefined_types_do_not_require_an_import(self):
        self.tracked(self.program('''a:Integer=123;b:Rational=0.25
n:=b.numerator;d:=b.denominator
delete(a);delete(b);delete(n);delete(d)
'''))

    def test_global_multiple_results_use_one_independent_initializer(self):
        self.tracked(self.numeric('''if !ok{panic("global status")};expect(value,"12");delete(value)
''','value,ok:=Integer("12")\n'))

    def test_automatic_numeric_support_follows_imported_thor_results(self):
        self.tracked(self.program('''value:=provider.create();copy:=value;delete(value)
if copy!=12{panic("independent imported value")};delete(copy)
''','import "provider"\n'),files={'provider/main.thor':'package provider\ncreate::proc()->Integer{return 12}\n'})

    def test_anonymous_numeric_arguments_keep_local_and_variadic_type_context(self):
        self.tracked(self.numeric('''a:=Integer(1);expect(a+1,"2");delete(a)
sink(Integer(1),"two");numeric_sink(Integer(1),2)
''','''sink::proc(values:..any){if len(values)!=2{panic("count")}}
numeric_sink::proc(values:..Integer){if len(values)!=2{panic("count")}}
'''))

    def test_generic_named_record_and_map_literals_preserve_logical_field_types(self):
        self.tracked(self.numeric('''record:=make_record(Holder);expect(record.value,"1");delete(record.value)
values:=map[string]Integer{"a"=2};expect(values["a"],"2");delete(values["a"]);delete(values)
plain:=make_record(Plain);if plain.value!=1{panic("native field")}
''','''Holder::struct{value:Integer}
Plain::struct{value:int}
make_record::proc($T:typeid)->T{return T{value=1}}
'''))


    def test_fstring_constructor_inputs_release_only_their_temporary_strings(self):
        self.tracked(self.numeric('''a,ok:=Integer(f"12{3}");if !ok{panic("parse")};expect(a,"123");delete(a)
b,b_ok:=Rational(f"1.{25}");if !b_ok{panic("rational")};expect(b,"5/4");delete(b)
for flag in [false,true]{
 c:=Integer(flag?f"{7}":"8") or_else 0;expect(c,flag?"7":"8");delete(c)
}
d,d_ok:=Rational(f"{22}",f"{13}");if !d_ok{panic("pair")};expect(d,"22/13");delete(d)
Integer(f"{7}")
text:=f!"{42}";defer delete(text);e:=Integer(text) or_else 0;expect(e,"42");delete(e)
'''))


if __name__=='__main__':unittest.main()
