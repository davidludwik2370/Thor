"""Static logical constants keep read-only backing and independent runtime copies."""
import os
import unittest
from tests import test_rust_big_lowering as runtime
from tests.test_rust_compiler import UNCHECKED


class RustBigSourceConstantTests(unittest.TestCase):
    setUpClass=classmethod(runtime.RustBigLoweringTests.setUpClass.__func__)
    setUp=runtime.RustBigLoweringTests.setUp
    invoke=runtime.RustBigLoweringTests.invoke
    compare=runtime.RustBigLoweringTests.compare
    tracked=runtime.RustBigLoweringTests.tracked
    numeric=runtime.RustBigLoweringTests.numeric
    program=runtime.RustBigLoweringTests.program

    def test_static_arithmetic_views_and_independent_copies(self):
        self.tracked(self.numeric('''a:=VALUE;b:=NEXT;r:=RATIO;e:=EXACT
expect(a,"123456789012345678901234567890");expect(b,"246913578024691357802469135781")
expect(r,"22/13");expect(e,"3/10");if !CHECK||SELECTED!=7{panic("selection")}
a+=1;if a==VALUE{panic("static mutation")}
delete(a);delete(b);delete(r);delete(e)
''','''VALUE::Integer(123456789012345678901234567890)
NEXT::VALUE*2+1
RATIO::Rational(numerator=22,denominator=13)
EXACT::Rational(0.1)+0.2
CHECK::NEXT>VALUE
when NEXT>VALUE{SELECTED::7}else{SELECTED::8}
'''))

    def test_static_reading_does_not_allocate(self):
        self.compare(self.numeric('''context.allocator=mem.nil_allocator()
if !big.equal(VALUE,VALUE)||!big.equal(RATIO,RATIO){panic("static views")}
''','''import "core:mem"
VALUE::Integer(123456789012345678901234567890)
RATIO::Rational(22,13)
'''))

    def test_local_constants_remain_available_inside_closed_helpers(self):
        self.tracked(self.numeric('''LOCAL::Integer(123)
RATIO::Rational(1,3)
copy:=LOCAL;copy+=1;expect(copy,"124")
text:=f"local {LOCAL} {RATIO}";if text!="local 123 1/3"{panic("format")}
delete(copy);delete(text)
'''))

    def test_readonly_constants_and_domain_errors(self):
        for body in ['VALUE=Integer(2)','VALUE+=1','_=&VALUE','delete(VALUE)']:
            with self.subTest(body=body):
                self.compare(self.numeric(body,'VALUE::Integer(123)\n'),command='check',fail='read-only')
        for expression,message in [('Integer(2)/0','divisor'),('Integer(2)**-1','nonnegative'),
                                   ('Rational(1,0)','denominator'),('Rational(2)**0.5','whole exponents'),
                                   ('Integer(1)+Rational(2)','matching types'),('Integer(1)<<-1','nonnegative')]:
            with self.subTest(expression=expression):
                self.compare(self.numeric('_=VALUE','VALUE::'+expression+'\n'),command='check',fail=message)

    def test_constant_operators_match_runtime_operations(self):
        prefix='A::Integer(-123456789012345678901234567890)\nB::Integer(17000)\n'
        body='a:=A;b:=B\n'
        for index,op in enumerate(['+','-','*','/','%','%%','&','|','~','&~']):
            prefix+=f'RESULT_{index}::A {op} B\n'
            body+=f'if RESULT_{index}!=a {op} b{{panic("constant operation")}}\n'
        for index,expression in enumerate(['a<<4','a>>4','a**3','~a','-a']):
            prefix+='UNARY_'+str(index)+'::'+expression.replace('a','A')+'\n'
            body+=f'if UNARY_{index}!=({expression}){{panic("constant unary")}}\n'
        body+='delete(a);delete(b)\n'
        self.tracked(self.numeric(body,prefix))

    def test_literal_budget_applies_before_expansion_and_across_cache_hits(self):
        # Reuse the same cache while changing only the configured limit.
        source = self.numeric('expect(VALUE, "1/1000")',
                              'VALUE :: Rational(10.000e-4)\n')
        self.source.write_text(source)
        for limit, accepted in [('4', True), ('3', False), ('6', True)]:
            with self.subTest(limit=limit):
                result = self.invoke('rust', env={**os.environ, 'THOR_MAX_LITERAL_DIGITS': limit})
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                if not accepted:
                    self.assertIn('requires 4 digits', result.stderr)

        self.source.write_text(self.numeric(
            'x := #run make_value(); defer delete(x); expect(x, "1234")',
            'make_value :: fn() -> Integer { Integer(1234) }\n'))
        for limit, accepted in [('4', True), ('3', False), ('4', True)]:
            with self.subTest(comptime_limit=limit):
                result = self.invoke('rust', env={**os.environ, 'THOR_MAX_LITERAL_DIGITS': limit})
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                if not accepted:
                    self.assertIn('requires 4 digits', result.stderr)

        self.source.write_text(self.program('_=TINY', 'TINY :: Rational(1e-100000000)\n'))
        result = self.invoke('rust', 'check', env={k:v for k,v in os.environ.items() if k != 'THOR_MAX_LITERAL_DIGITS'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires 100000001 digits', result.stderr)
        self.assertIn('limit is 16000000', result.stderr)

    def test_literal_budget_runtime_generic_and_radix_boundaries(self):
        env = {**os.environ, 'THOR_MAX_LITERAL_DIGITS': '3'}
        for expr, prefix in [
                ('Integer(0x01_234)', ''), ('Rational(1e-3)', ''),
                ('Rational(1, 1000)', ''), ('Integer(1e3, allocator=context.allocator)', ''),
                ('number(Integer)', 'number :: proc($T: typeid) -> T { return 1234 }\n')]:
            with self.subTest(expr=expr):
                self.source.write_text(self.program('x := '+expr+'; delete(x)', prefix))
                result = self.invoke('rust', 'check', env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('requires 4 digits', result.stderr)

        self.source.write_text(self.program('if number(int) != 1234 || ZERO != 0 || VALUE != 255 {panic("value")}',
            'number :: proc($T: typeid) -> T { return 1234 }\n'
            'ZERO :: Rational(0e-100000000)\nVALUE :: Integer(0x000_ff)\n'))
        for flags in ([], ['-disable-assert'], UNCHECKED):
            with self.subTest(flags=flags):
                result = self.invoke('rust', flags=flags, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.source.write_text(self.program('_=I', 'I :: Integer(1)\n'))
        result = self.invoke('rust', 'check', env={**env, 'THOR_MAX_LITERAL_DIGITS':'0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('THOR_MAX_LITERAL_DIGITS must be a positive whole number', result.stderr)

    def test_power_budget_checks_both_rational_components(self):
        for expression in ('Rational(1,256)**16777216', 'Rational(-256,3)**-16777216',
                           'Integer(256)**16777216', 'Integer(256)<<134217720'):
            with self.subTest(expression=expression):
                source = self.numeric('_=VALUE', 'VALUE::'+expression+'\n')
                self.compare(source, command='check', fail='Compile-time result exceeds 16 MiB')
        prefix = '''SMALL::Rational(-1,2)**-3
UNIT::Rational(-1)**-100000000000000000001
ZERO::Integer(0)**100000000000000000001
'''
        self.tracked(self.numeric('if SMALL!=-8 || UNIT!=-1 || ZERO!=0{panic("power")}', prefix))


    def test_compile_switch_constants_keep_selected_types_and_lazy_arms(self):
        self.tracked(self.numeric('''LOCAL::Integer(123)
CHOICE::#switch LOCAL{case 0..<100:Integer(0);case:LOCAL+1}
RATIO::#switch{case LOCAL>100:Rational(1,3);case:missing()}
LAZY::#switch{case true,missing():Integer(7);case:Integer(0)}
copy:=CHOICE;copy+=1;expect(copy,"125")
if CHOICE!=124||RATIO!=Rational(1,3)||LAZY!=7{panic("selected values")}
delete(copy)
'''))

    def test_numeric_switch_ranges_select_scalar_constants_and_reject_overlap(self):
        self.tracked(self.numeric('if SELECTED!=2{panic("selected scalar")}','''VALUE::Integer(3)
SELECTED::#switch VALUE{case 0..<3:1;case 3..<5:2;case:0}
'''))
        self.compare(self.numeric('_=SELECTED','''VALUE::Integer(3)
SELECTED::#switch VALUE{case 0..<4:1;case 3..<5:2;case:0}
'''),command='check',fail='overlapping')

    def test_imported_constants_keep_readonly_rules_and_independent_copies(self):
        files={'library/main.thor':'package library\nVALUE::Integer(123)\n'}
        for body in ['library.VALUE=Integer(2)','library.VALUE+=1','_=&library.VALUE',
                     'delete(library.VALUE)','delete((library.VALUE))']:
            with self.subTest(body=body):
                self.compare(self.numeric(body,'import "library"\n'),files=files,command='check',fail='read-only')
        self.tracked(self.numeric('''copy:=library.VALUE;copy+=1;expect(copy,"124")
expect(library.VALUE,"123");delete(copy)
''','import "library"\n'),files=files)

    def test_constant_builtins_preserve_numeric_context_in_nested_calls(self):
        self.tracked(self.numeric('''a:=LIMIT;b:=MAGNITUDE;c:=REVERSED
expect(a,"3");expect(b,"8");expect(c,"3")
delete(a);delete(b);delete(c)
''','''VALUE::Integer(-8)
LIMIT::clamp(max(VALUE,3),0,9)
REVERSED::clamp(max(3,VALUE),0,9)
MAGNITUDE::abs(VALUE)
'''))

    def test_constant_numeric_membership_is_exact_and_lazy(self):
        self.tracked(self.numeric('''if !INCLUDED||!ABSENT||!LAZY{panic("membership")}
''','''INCLUDED::Integer(4) in [2,4,8]
ABSENT::Rational(1,3) not_in [0.1,0.2,0.3]
LAZY::Integer(4) in [4,missing()]
'''))

    def test_text_constructor_constants_require_explicit_execution(self):
        self.compare(self.numeric('_=VALUE','VALUE::Integer("12")\n'),command='check',fail='use #run')


    def test_selected_imported_constant_types_settle_before_copy_lowering(self):
        files={'library/main.thor':'''package library
CHOICE::#switch{case false:0;case:Integer(123)}
RATIO::#switch{case false:0;case:Rational(1,3)}
'''}
        self.tracked(self.numeric('''copy:=library.CHOICE;copy+=1;ratio:=library.RATIO;ratio+=1
expect(copy,"124");expect(library.CHOICE,"123");expect(ratio,"4/3")
delete(copy);delete(ratio)
''','import "library"\n'),files=files)
        self.compare(self.numeric('delete(library.CHOICE)','import "library"\n'),files=files,command='check',fail='read-only')

    def test_forward_declared_selected_constant_types_are_ready_for_mutation(self):
        source=self.numeric('copy:=CHOICE;copy+=1;expect(copy,"124");delete(copy)')+'\nCHOICE::#switch{case false:0;case:Integer(123)}\n'
        self.tracked(source)

    def test_selected_constant_types_are_ready_before_native_execution_scheduling(self):
        files={'library/main.thor':'package library\nCHOICE::#switch{case false:0;case:Integer(123)}\n'}
        self.tracked(self.numeric('''copy:=#run identity(library.CHOICE);copy+=1;expect(copy,"124");delete(copy)
''','import "library"\nidentity::fn(value:Integer)->Integer{return Integer(value)}\n'),files=files)

if __name__=='__main__':unittest.main()
