"""Native compile-time execution reconstructs independently owned logical values."""
import json
import unittest
from tests import test_rust_big_lowering as runtime

class RustBigExecutionTests(unittest.TestCase):
    setUpClass=classmethod(runtime.RustBigLoweringTests.setUpClass.__func__)
    setUp=runtime.RustBigLoweringTests.setUp
    invoke=runtime.RustBigLoweringTests.invoke
    compare=runtime.RustBigLoweringTests.compare
    tracked=runtime.RustBigLoweringTests.tracked
    numeric=runtime.RustBigLoweringTests.numeric
    program=runtime.RustBigLoweringTests.program

    def test_fn_run_and_static_results_survive_deferred_deletion(self):
        self.tracked(self.numeric('''a:=calculate(22);b:=#run calculate(22);c:=VALUE
expect(a,"705429498686404044207947776");expect(b,"705429498686404044207947776");expect(c,"705429498686404044207947776")
a+=1;if a==b||b!=c{panic("independence")};delete(a);delete(b);delete(c)
''','''calculate::fn(n:int)->Integer{value:=Integer(n)**20;defer delete(value);return value}
VALUE::#run calculate(22)
'''))

    def test_direct_constructor_execution_retains_exact_contents(self):
        huge='1234567890'*100
        self.tracked(self.numeric('a:=#run Integer('+huge+');expect(a,'+json.dumps(huge)+''');delete(a)
b:=#run Rational(22,13);expect(b,"22/13");delete(b)
c:=NUMERATOR;expect(c,"22");delete(c)
''','RATIO::Rational(22,13)\nNUMERATOR::RATIO.numerator\n'))

    def test_nested_owned_result_materializes_logical_elements(self):
        self.tracked(self.numeric('''a:=#run make_report();b:=make_report()
expect(a.value,"123456789012345678901234567890");expect(a.ratio,"1/3");expect(a.rows[0],"7");expect(a.rows[1],"8")
a.value+=1;if a.value==b.value{panic("independence")};pure.destroy(a);pure.destroy(b)
''','''import "thor:pure"
Report::struct{value:Integer,ratio:Rational,rows:[]Integer}
identity::fn(value:Integer)->Integer{return value}
make_report::fn()->Report{
 number:=Integer(123456789012345678901234567890);defer delete(number)
 rows:=make([]Integer,2);rows[0]=Integer(7);rows[1]=Integer(8)
 return Report{identity(number),Rational(1,3),rows}
}
'''))

    def test_big_constant_inputs_and_local_static_bindings(self):
        self.tracked(self.numeric('''LOCAL::#run Integer(5)
for _ in 0..<3{a:=#run identity(VALUE);b:=#run identity(VALUE);a+=1;if a==b{panic("independence")};expect(b,"123456789012345678901234567890");delete(a);delete(b)}
c:=LOCAL;expect(c,"5");delete(c)
''','VALUE::Integer(123456789012345678901234567890)\nidentity::fn(value:Integer)->Integer{return value}\n'))

    def test_big_conditions_select_native_requests_lazily(self):
        self.tracked(self.numeric('''a:=SELECTED;b:=CHOICE;expect(a,"22");expect(b,"33");delete(a);delete(b)
''','''VALUE::Integer(123456789012345678901234567890)
identity::fn(n:int)->Integer{return Integer(n)}
when VALUE>0{SELECTED::#run identity(22)}else{SELECTED::#run missing()}
CHOICE::#switch VALUE{case 0..<100:Integer(0);case:#run identity(33)}
'''))

    def test_native_generic_value_slots_reject_logical_numbers(self):
        self.compare(self.numeric('_=work(Integer(2))','work::fn($n:Integer)->int{return 1}\n'),command='check',fail='cannot be native generic constant parameters')

    def test_imported_run_constants_keep_readonly_views_and_independent_copies(self):
        files={'library/main.thor':'''package library
value::fn()->Integer{return Integer(123)}
VALUE::#run value()
'''}
        self.tracked(self.numeric('''a:=library.VALUE;b:=#run identity(library.VALUE);a+=1
expect(a,"124");expect(b,"123");expect(library.VALUE,"123");delete(a);delete(b)
''','import "library"\nidentity::fn(value:Integer)->Integer{return value}\n'),files=files)
        self.compare(self.numeric('delete(library.VALUE)','import "library"\n'),files=files,command='check',fail='read-only')

    def test_numeric_aggregate_constants_require_runtime_bindings(self):
        self.compare(self.numeric('_=VALUE','''Row::struct{value:Integer}
work::fn()->Row{return Row{Integer(1)}}
VALUE::#run work()
'''),command='check',fail='aggregate containing Integer/Rational')
        self.compare(self.numeric('_=VALUE','''identity::fn(value:int)->Integer{return Integer(value)}
VALUE::[identity(value) for value in 0..<2]
'''),command='check',fail='Integer/Rational-containing comprehension')

    def test_text_constructors_execute_through_explicit_optional_consumers(self):
        self.compare(self.numeric('_=#run Integer("123")'),command='check',fail='Assignment count mismatch')
        self.tracked(self.numeric('''a:=#run read_integer("123");b:=#run read_rational("0.1(6)")
expect(a,"123");expect(b,"1/6");delete(a);delete(b)
''','''read_integer::fn(text:string)->Integer{return Integer(text) or_panic}
read_rational::fn(text:string)->Rational{return Rational(text) or_panic}
'''))

if __name__=='__main__':unittest.main()
