"""Formatting, builtin reductions, generic components, and shared basic."""
from tests import test_big_language as language
from tests import test_thor as base

class BigIntegrationTests(base.unittest.TestCase):
    invoke=language.BigLanguageTests.invoke
    program=language.BigLanguageTests.program
    run_big=language.BigLanguageTests.run_big

    def test_literal_coercion_in_typed_declarations_and_compound_updates(self):
        self.run_big('''
a:Integer=1
a+=2
expect(a,"3")
a*=4;a-=1;a/=11
expect(a,"1")
b:Rational=1
b+=2;b/=4
expect(b,"3/4")
delete(a);delete(b)
''')
        self.invoke(self.program('a:Integer=1;n:=2;a+=n'),command='check',fail='Cannot')

    def test_native_generic_constant_defaults_remain_constant(self):
        self.run_big('assert(defaults(int)==2)','defaults::proc($T:typeid,low:T=-1,high:T=1+2)->T{return low+high}\n')

    def test_fstrings_exact_numbers_grouping_nested_temporaries_and_pure_calls(self):
        self.run_big('''
text:=f"{Integer(12345678901234567890):,} {Rational(22,13):8} {Integer(2)+3}"
assert(text=="12,345,678,901,234,567,890    22/13 5");delete(text)
nested:=f"outer {f\"inner {Integer(2)**10}\"}"
assert(nested=="outer inner 1024");delete(nested)
a:=describe(22);b:=#run describe(22)
assert(a=="value 22/13" && b==a);delete(a);delete(b)
''','describe::fn(n:int)->string{return f"value {Rational(n,13)}"}\n')

    def test_basic_print_and_opt_in_native_fmt_registry(self):
        self.run_big('''
basic.print(Integer(3),Rational(22,13).numerator)
a:=Integer(12345678901234567890);r:=Rational(22,13)
basic.print(a,r,sep=" | ")
registry:map[typeid]fmt.User_Formatter
format.add_big_formatters(&registry)
fmt.set_user_formatters(&registry)
text:=fmt.aprintf("%d %v",a,r)
assert(text=="12345678901234567890 22/13");delete(text)
temporary_text:=fmt.aprintf("%d %v",Integer(3),Rational(1,3))
assert(temporary_text=="3 1/3");delete(temporary_text)
parts:=[2]Integer{a,r.numerator}
nested:=fmt.aprint(parts)
assert(nested=="[12345678901234567890, 22]");delete(nested)
for &part in parts {delete(part)}
format.destroy_formatter_registry(&registry)
fmt.println("formatter registry released")
delete(a);delete(r)
''','import "thor:basic"\nimport "thor:format"\nimport "core:fmt"\n')

    def test_variadic_numeric_arguments_coerce_literals_and_release_temporaries(self):
        self.run_big('''
a:=total(Integer(1),2,3);expect(a,"6");delete(a)
b:=generic_total(Integer(4),5);expect(b,"9");delete(b)
''','''total::proc(values:..Integer)->Integer{
result:Integer
defer delete(result)
for value in values {result+=value}
return result
}
generic_total::proc(first:$T,values:..T)->T{
result:=first
defer when T==Integer || T==Rational {delete(result)}
for value in values {result+=value}
return result
}
''')

    def test_method_argument_literals_adopt_numeric_parameter_type(self):
        self.run_big('record:=Record{apply};value:=record->apply(2);expect(value,"3");delete(value)',
            'Record::struct{apply:proc(self:^Record,value:Integer)->Integer}\napply::proc(self:^Record,value:Integer)->Integer{return value+1}\n')

    def test_inline_and_named_membership_owns_only_anonymous_elements(self):
        self.run_big('''
a:=Integer(4)
assert(a in [1,4,8]);assert(6 not_in [Integer(1),a+1])
assert(Integer(4) in [a,Integer(5)])
values:=[a,Integer(8)]
assert(a in values && 8 in values && 6 not_in values)
for &value in values {delete(value)}
delete(a)
''')

    def test_runtime_numeric_switch_selectors_ranges_and_anonymous_results(self):
        self.run_big('''
a:=Integer(10)
selected:=switch next(){case 0..<5:Integer(0);case 10,11:a+2;case:panic("unreachable")}
assert(calls==1 && selected==12)
r:=Rational(1,3)
kind:=switch r {case 0..<0.25:1;case 0.25..=0.5:2;case:3}
assert(kind==2)
text:=f"picked {switch Integer(3) {case 3:Integer(7);case:Integer(9)}}"
assert(text=="picked 7");delete(text)
x,y:=switch a {case 10:Integer(1),Integer(2);case:Integer(3),Integer(4)}
x,y=switch Integer(99){case 10:Integer(7),Integer(8);case:break}
assert(x==1 && y==2)
x=switch Integer(99){case 99:x+1;case:break}
assert(x==2)
delete(a);delete(selected);delete(r);delete(x);delete(y)
''','calls:int\nnext::proc()->Integer{calls+=1;return Integer(10)}\n')

    def test_numeric_switch_snapshots_selector_before_case_effects(self):
        self.run_big('''
a:=Integer(7)
selected:=switch a {case replace(&a):1;case:2}
assert(a==99 && selected==1)
delete(a)
''','replace::proc(value:^Integer)->Integer{value^=Integer(99);return Integer(7)}\n')

    def test_numeric_switch_rejections_use_original_case_locations(self):
        for selector,cases,message in [
            ('Integer(3)','case 0..<4:1;case 3..<5:2;case:0','overlapping'),
            ('Rational(1,3)','case 1..<0:1;case:0','empty or reversed'),
            ('Integer(3)','case i64(3):1;case:0','Cannot'),
            ('Integer(3)','case Rational(3):1;case:0','Cannot')]:
            with self.subTest(cases=cases):
                self.invoke(self.program('result:=switch '+selector+' {'+cases+'};_=result'),command='check',fail=message)

    def test_numeric_builtins_preserve_ownership_and_context(self):
        self.run_big('''
a:=Integer(-8);b:=Integer(3)
x:=abs(a);expect(x,"8")
y:=min(a,b,2);expect(y,"-8")
z:=max(a,b,99);expect(z,"99")
c:=clamp(a,-2,2);expect(c,"-2")
assert(a==Integer(-8)&&b==Integer(3))
delete(x);delete(y);delete(z);delete(c);delete(a);delete(b)
r:=abs(Rational(-22,13));expect(r,"22/13");delete(r)
''')

    def test_spread_min_max_empty_slices_and_independent_results(self):
        self.run_big('''
values:=[Integer(8),Integer(-2),Integer(3)]
a:=min(**values);b,ok:=max(..values[:]);c:=min(9,**values)
assert(ok);expect(a,"-2");expect(b,"8");expect(c,"-2")
for &v in values {delete(v)}
expect(a,"-2");expect(b,"8")
empty:[]Integer
missing,found:=min(..empty)
assert(!found);expect(missing,"0")
delete(a);delete(b);delete(c);delete(missing)
''')

    def test_generic_properties_preserve_ordinary_fields_and_pointer_receivers(self):
        self.run_big('''
r:=Rational(22,13)
a:=component(r);b:=component(&r)
expect(a,"22");expect(b,"22")
c2:=through(r);expect(c2,"22");delete(c2)
record:=Record{Integer(7)}
c:=component(record);expect(c,"7");delete(c);delete(record.numerator)
delete(a);delete(b);delete(r)
''','''Record::struct{numerator:Integer}
component::proc(value:$T)->Integer{return value.numerator}
identity::proc(value:$T)->T{return value}
through::proc(value:$T)->Integer{return identity(value).numerator}
''')

    def test_shared_basic_numeric_generics_and_sorting(self):
        self.run_big('''
values:=[Integer(1),Integer(2),Integer(3)]
a:=basic.sum(values[:]);expect(a,"6")
sorted:=basic.sorted(values[:]);assert(sorted[0]==values[0]);delete(sorted)
for &v in values {delete(v)}
delete(a)
native_values:=[1,2,3]
assert(basic.sum(native_values[:])==6)
''','import "thor:basic"\n')

    def test_generic_property_guards_preserve_writable_ordinary_fields(self):
        self.run_big('''
record:=Record{2};change(&record);assert(record.numerator==9)
pointer:=&record.numerator;pointer^=10;assert(record.numerator==10)
''','Record::struct{numerator:int}\nchange::proc(value:^$T){value.numerator=9}\n')
        self.invoke(self.program('r:=Rational(22,13);change(&r)',
            'change::proc(value:^$T){value.numerator=9}\n'),command='check',fail='read-only')

    def test_constant_builtins_membership_and_matching_type_errors(self):
        self.run_big('''
a:=LIMIT;b:=MAGNITUDE;expect(a,"3");expect(b,"8");assert(INCLUDED)
delete(a);delete(b)
''','''VALUE::Integer(-8)
LIMIT::clamp(max(VALUE,3),0,9)
MAGNITUDE::abs(VALUE)
INCLUDED::Integer(4) in [2,4,8]
''')
        for body,message in [('text:=f"{Rational(1,3):,}"','Grouping requires a decimal'),('a:=min(Integer(1),i64(2))','Cannot'),
                             ('a:=max(Rational(1),Integer(2))','Cannot')]:
            with self.subTest(body=body):self.invoke(self.program(body),command='check',fail=message)
