"""Reference comparisons for logical builtins, components and selection."""
import unittest
from tests import test_rust_big_lowering as runtime

class RustBigIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(runtime.RustBigLoweringTests.setUpClass.__func__)
    setUp=runtime.RustBigLoweringTests.setUp
    invoke=runtime.RustBigLoweringTests.invoke
    compare=runtime.RustBigLoweringTests.compare
    tracked=runtime.RustBigLoweringTests.tracked
    numeric=runtime.RustBigLoweringTests.numeric
    program=runtime.RustBigLoweringTests.program

    def test_scalar_numeric_builtins_clone_selected_values(self):
        self.tracked(self.numeric('''a:=Integer(-8);b:=abs(a);c:=max(a,3);d:=min(3,a);e:=clamp(a,-4,9)
delete(a);expect(b,"8");expect(c,"3");expect(d,"-8");expect(e,"-4")
r:=Rational(-1,3);s:=abs(r);t:=max(r,0.5);delete(r);expect(s,"1/3");expect(t,"1/2")
delete(b);delete(c);delete(d);delete(e);delete(s);delete(t)
'''))

    def test_expanded_extrema_empty_optional_and_named_collections(self):
        self.tracked(self.numeric('''values:=[Integer(3),Integer(-1),Integer(8)]
a:=min(**values);b,ok:=max(..values[:]);if !ok{panic("nonempty")}
empty:[]Integer;c,c_ok:=min(..empty);if c_ok{panic("empty")};expect(c,"0")
d,d_ok:=max(Integer(12),**values,..empty);if !d_ok{panic("mixed")}
for &value in values{delete(value)}
expect(a,"-1");expect(b,"8");expect(d,"12");delete(a);delete(b);delete(c);delete(d)
'''))

    def test_generic_builtins_preserve_native_and_numeric_results(self):
        self.tracked(self.numeric('''a:=Integer(-8);b:=magnitude(a);c:=larger(a,Integer(3));d:=bounded(a);delete(a)
expect(b,"8");expect(c,"3");expect(d,"0");delete(b);delete(c);delete(d)
if magnitude(-8)!=8||larger(2,3)!=3||bounded(-8)!=0{panic("native")}
''','''magnitude::proc(value:$T)->T{return abs(value)}
larger::proc(value:$T,other:T)->T{return max(value,other)}
bounded::proc(value:$T)->T{return clamp(value,0,10)}
'''))

    def test_generic_components_own_only_rational_results(self):
        self.tracked(self.numeric('''r:=Rational(22,13);n:=component(r,Integer);d:=denominator(&r,Integer);delete(r)
expect(n,"22");expect(d,"13");delete(n);delete(d)
expect(component(Rational(22,13),Integer),"22")
if component(Record{7},int)!=7{panic("ordinary field")}
''','''Record::struct{numerator:int}
component::proc(value:$T,$R:typeid)->R{return value.numerator}
denominator::proc(value:$T,$R:typeid)->R{return value.denominator}
'''))

    def test_generic_component_mutation_rejects_rational_and_accepts_fields(self):
        self.tracked(self.numeric('''record:=Record{7};set(&record);if record.numerator!=9{panic("field")}
''','''Record::struct{numerator:int}
set::proc(value:$T){value.numerator=9}
'''))
        self.compare(self.numeric('''r:=Rational(22,13);set(&r)
''','set::proc(value:$T){value.numerator=9}\n'),command='check',fail='read-only')

    def test_membership_keeps_named_values_and_releases_anonymous_operands(self):
        self.tracked(self.numeric('''a:=Integer(3);values:=[Integer(1),Integer(3)]
if a not_in values||a not_in [1,3,5]||Integer(8) in values{panic("membership")}
if Integer(3) not_in [member(1),member(3),member(5)]||calls!=3{panic("evaluation order")}
if !generic_contains(values,a)||generic_contains([1,2],3){panic("generic")}
delete(a);for &value in values{delete(value)}
''','''calls:int
member::proc(value:int)->Integer{calls+=1;return Integer(value)}
generic_contains::proc(values:$T,value:$E)->bool{return value in values}
'''))

    def test_runtime_switch_owns_snapshot_and_range_operands(self):
        self.tracked(self.numeric('''value:=Integer(3)
answer:=switch value{case 0..<3:Integer(0);case 3..=5:value+1;case:Integer(9)}
expect(answer,"4");delete(answer)
a,b:=switch value{case 3: value,value+2;case:Integer(0),Integer(0)}
expect(a,"3");expect(b,"5");delete(a);delete(b)
a=Integer(7);a=switch value{case 0:Integer(0);case:break};expect(a,"7");delete(a)
delete(value)
'''))

    def test_anonymous_extrema_operands_are_evaluated_once_and_released(self):
        self.tracked(self.numeric('''a:=max(member(8),member(2),member(3));if calls!=3{panic("calls")};expect(a,"8");delete(a)
b:=min(**[Integer(7),Integer(1)]);expect(b,"1");delete(b)
''','calls:int\nmember::proc(value:int)->Integer{calls+=1;return Integer(value)}\n'))

    def test_generic_components_release_owned_producer_results(self):
        self.tracked(self.numeric('''r:=Rational(22,13);n:=component(r,Integer);delete(r);expect(n,"22");delete(n)
if component(Record{7},int)!=7{panic("ordinary field")}
''','''Record::struct{numerator:int}
identity::proc(value:$T)->T{return value}
component::proc(value:$T,$R:typeid)->R{return identity(value).numerator}
'''))

    def test_switch_snapshot_survives_case_mutation_and_blanks_are_destroyed(self):
        self.tracked(self.numeric('''value:=Integer(3)
answer:=switch value{case change(&value):0;case 3:2;case:9}
if answer!=2||value!=9{panic("snapshot")}
_,keep:=switch answer{case 2:Integer(1),value;case:Integer(0),Integer(0)}
expect(keep,"9");delete(keep);delete(value)
''','change::proc(value:^Integer)->Integer{value^=Integer(9);return Integer(5)}\n'))

    def test_switch_reassignment_snapshots_all_results_before_committing(self):
        self.tracked(self.numeric('''a:=Integer(3);b:=Integer(4)
a,b=switch 1{case 1:b,a;case:Integer(0),Integer(0)}
expect(a,"4");expect(b,"3");delete(a);delete(b)
'''))

    def test_generic_membership_preserves_native_collection_dispatch(self):
        self.tracked(self.program('''values:=[1,2,3];if !contains(values,2)||contains(values[:],4){panic("native sequence")}
map_values:=make(map[int]int);defer delete(map_values);map_values[2]=7
if !contains(map_values,2)||contains(map_values,3){panic("map key")}
bits:=bit_set[Flag]{.second};if !contains(bits,Flag.second)||contains(bits,Flag.first){panic("bit set")}
''','''Flag::enum{first,second}
contains::proc(values:$T,needle:$E)->bool{return needle in values}
'''))

if __name__=='__main__':unittest.main()
