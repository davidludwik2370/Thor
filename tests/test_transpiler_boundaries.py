"""Regression checks at native/compile-time and borrowed/owned boundaries."""
import json
import unittest

from tests import test_rust_compiler as native


class TranspilerBoundaryTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare

    def test_native_numeric_layout_survives_cached_evaluation(self):
        source = '''package main
Key::enum{A,B,C}
Bits::bit_set[Key;u16be]
Values::struct{q:quaternion256,little:bit_set[Key;u32le],big:Bits}
build::fn(x:f64)->Values{return {quaternion(real=x,imag=f64(2),jmag=f64(3),kmag=f64(4)),{.C},{.B}}}
main::proc(){
 n:=f64(9)
 runtime:=build(n)
 folded:=#run build(9)
 if folded!=runtime || real(folded.q)!=9 || imag(folded.q)!=2 || jmag(folded.q)!=3 ||
    kmag(folded.q)!=4 || folded.big!={.B} || folded.little!={.C} {panic("numeric result changed")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=flags)
                run = self.invoke('rust', flags=[*flags, '--cache-report'])
                self.assertEqual(run.returncode, 0, run.stderr)
                packet = next(json.loads(line) for line in run.stdout.splitlines()
                              if line.startswith('{"thor_cache"'))
                self.assertGreater(packet['thor_cache']['hits'], 0, packet)

    def test_static_results_and_union_views_need_explicit_ownership(self):
        cases = [
            'f::fn()->string{"a"+"b"}',
            'f::fn()->string{3*"ab"}',
            'TEXT::"static"\nf::fn()->string{TEXT}',
            'f::fn()->string{#config(NAME,"abcd")}',
            'f::fn(xs:[]int)->[]int{xs[1:1]}',
            'fallback::proc()->([]int,[]int,bool){return nil,nil,false}\n'
            'f::fn(xs:[]int)->([]int,[]int){return fallback() or_else (xs,xs)}',
            'S::union{int,string}\nf::fn(x:S)->string{x.(string) or_else ""}',
            'a::[]int{1}; b::[]int{2}\nf::fn()->[]int{a++b}',
            'a::[1]string{"a"}; b::[1]string{"b"}\nf::fn()->[2]string{a++b}',
        ]
        for decl in cases:
            with self.subTest(decl=decl):
                self.compare('package main\n'+decl+'\nmain::proc(){}\n', command='check',
                             fail='cannot return borrowed storage')

    def test_static_operations_can_return_inline_values_and_explicit_copies(self):
        source = '''package main
import "thor:pure"
U::union{int,string}
copy_text::fn(x:U)->string{pure.clone(x.(string) or_else "")}
a::[1]int{2}; b::[1]int{5}
join::fn()->[2]int{a++b}
EMPTY::""
ALIAS::EMPTY
empty::fn()->string{ALIAS+("abc"*0)}
NO_ITEMS::[]int{}
NO_MORE::(NO_ITEMS++NO_ITEMS)
empty_items::fn()->[]int{NO_MORE++NO_ITEMS}
main::proc(){
 u:U="test";s:=copy_text(u);defer delete(s)
 items:=empty_items();defer delete(items)
 if s!="test" || empty()!="" || raw_data(items)!=nil || join()!=([2]int{2,5}){panic("owned or inline result")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            self.compare(source, flags=flags)

    def test_config_string_ownership_uses_the_selected_value(self):
        source = '''package main
DEFAULT::#config(TEXT,"")
get::fn()->string{DEFAULT}
main::proc(){if get()!=""{panic("default")}}
'''
        self.compare(source)
        self.compare(source, command='check', flags=['-define:TEXT=hello'],
                     fail='cannot return borrowed storage')

    def test_rational_properties_and_record_fields_have_different_owners(self):
        declarations = '''package main
Fraction::Rational
Record::struct{numerator:Integer}
component::fn(x:$T)->Integer{x.numerator}
alias::component
'''
        positive = declarations + '''main::proc(){
 r:=Fraction(23,7);defer delete(r)
 get:fn(Fraction)->Integer=alias
 a:=get(r);defer delete(a)
 b:=#run alias(Fraction(23,7));defer delete(b)
 a+=2
 if a!=25 || b!=23 || r!=Fraction(23,7){panic("independent components")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            self.compare(positive, flags=flags)
        for use in ('x:=alias(r)', 'get:fn(Record)->Integer=alias;x:=get(r)',
                    'x:=#run alias(Record{Integer(7)})'):
            with self.subTest(use=use):
                self.compare(declarations+'main::proc(){r:=Record{Integer(7)};defer delete(r.numerator);'
                             +use+';delete(x)}\n', command='check', fail='cannot return borrowed storage')

    def test_numeric_update_must_replace_the_borrow_on_every_path(self):
        for op in ('v+=1', 'v=v+1'):
            with self.subTest(op=op):
                self.compare('package main\nf::fn(x:Integer,b:bool)->Integer{v:=x;if b{'
                             +op+'};v}\nmain::proc(){}\n', command='check',
                             fail='cannot return borrowed storage')

    def test_generic_numeric_property_ownership_inside_aggregate_results(self):
        declarations = '''package main
Parts::struct{n:Integer, ds:[1]Integer}
SameNames::struct{numerator,denominator:Integer}
parts::fn(x:$T)->Parts{Parts{x.numerator, {x.denominator}}}
'''
        good = declarations + '''Scalar::struct{numerator:int}
WithData::struct{n:int,data:[]int}
copy_scalar::fn(x:$T)->WithData{WithData{x.numerator,make([]int,1)}}
main::proc(){
 r:=Rational(5,7);defer delete(r)
 cb:fn(Rational)->Parts=parts
 a:=cb(r);defer delete(a.n);defer delete(a.ds[0])
 b:=#run parts(Rational(5,7));defer delete(b.n);defer delete(b.ds[0])
 c:=copy_scalar(Scalar{8});defer delete(c.data)
 a.n+=1
 if a.n!=6 || a.ds[0]!=7 || b.n!=5 || b.ds[0]!=7 || r!=Rational(5,7) || c.n!=8{panic("aggregate ownership")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(good, flags=flags)
        for call in ('parts(r)', '#run parts(SameNames{Integer(1),Integer(2)})'):
            with self.subTest(call=call):
                self.compare(declarations+'main::proc(){r:=SameNames{Integer(1),Integer(2)};_='+call+'}\n',
                             command='check', fail='cannot return borrowed storage')

    def test_record_and_soa_evaluation_respects_builtin_shadowing(self):
        source = '''package main
type_of::proc()->int{return 17}
len::proc()->int{return 9}
Row::struct{x:int}
row::fn(x:int)->Row{return {x}}
rows::fn(x:int)->#soa[]Row{v:=make(#soa[]Row,1);v[0]=row(x);v}
ONE::#run row(3)
main::proc(){
 v:=#run rows(7);defer delete(v)
 if ONE.x!=3 || v[0].x!=7 || type_of()+len()!=26{panic("shadowed builtin")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            self.compare(source, flags=flags)

    def test_sparse_enumerated_array_round_trip_checks_unnamed_slots(self):
        source = '''package main
Key::enum{First=0,Last=2}
Table::#sparse[Key]int
table::fn(i:int)->Table{a:Table;a[Key(i)]=42;return a}
main::proc(){v:=#run table(INDEX);if v[.Last]!=42{panic("sparse result")}}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source.replace('INDEX','2'), flags=flags)
                self.compare(source.replace('INDEX','1'), flags=flags,
                             fail='Nonzero unnamed enum-array slots')

    def test_oversized_evaluation_reports_the_limit_with_assertions_disabled(self):
        source = '''package main
import "thor:pure"
text::fn(n:int)->string{buf:=make([]u8,n);return pure.clone(string(buf))}
main::proc(){value:=#run text(8388608);defer delete(value)}
'''
        # Hex encoding doubles the payload before the packet header is added.
        self.compare(source, flags=native.UNCHECKED, fail='Compile-time result is too large')

    def test_wide_indices_are_checked_even_with_assertions_disabled(self):
        for typ, access in [('i128','xs[i]'), ('u128','xs[:i]'), ('u128','xs[i]=9')]:
            with self.subTest(typ=typ, access=access):
                statement = access if '=' in access else '_='+access
                source = f'package main\nmain::proc(){{xs:=[7,9];i:={typ}(1)<<65;{statement}}}\n'
                for flags in ([], ['-disable-assert']):
                    self.compare(source, flags=flags, fail='Bounds check failed')
        self.compare('package main\nmain::proc(){xs:=[7,9];i:=i128(-1);xs[i]=12;if xs[1]!=12{panic("wide index")}}\n',
                     flags=native.UNCHECKED)
