"""Exact constants, portable evaluation results, and ownership across fn/#run."""
import json
from fractions import Fraction
from thorlang import bigvalues,values
from tests import test_big_language as language
from tests import test_thor as base


class BigCompileTimeTests(base.unittest.TestCase):
    invoke=language.BigLanguageTests.invoke
    program=language.BigLanguageTests.program
    run_big=language.BigLanguageTests.run_big

    def test_imported_numeric_constants_are_readonly_before_unwrapping_the_package(self):
        files={'library/main.thor':'package library\nVALUE::Integer(123)\n'}
        for body in ['library.VALUE=Integer(2)','library.VALUE+=1','_=&library.VALUE',
                     'delete(library.VALUE)','delete((library.VALUE))']:
            with self.subTest(body=body):
                self.invoke(self.program(body,'import "library"\n'),files=files,command='check',fail='read-only')
        self.run_big('''copy:=library.VALUE;copy+=1;expect(copy,"124")
expect(library.VALUE,"123");delete(copy)
''','import "library"\n',files=files)

    def test_readonly_constants_exact_arithmetic_and_conditions(self):
        self.run_big('''
a:=VALUE;b:=NEXT;r:=RATIO;e:=EXACT
expect(a,"123456789012345678901234567890")
expect(b,"246913578024691357802469135781")
expect(r,"22/13");expect(e,"3/10")
assert(CHECK && SELECTED==7)
delete(a);delete(b);delete(r);delete(e)
''','''VALUE::Integer(123456789012345678901234567890)
NEXT::VALUE*2+1
RATIO::Rational(numerator=22,denominator=13)
EXACT::Rational(0.1)+0.2
CHECK::NEXT>VALUE
when NEXT>VALUE {SELECTED::7} else {SELECTED::8}
''')

    def test_readonly_constants_do_not_allocate_until_copied(self):
        self.run_big('''
context.allocator=mem.nil_allocator()
assert(big.equal(VALUE,VALUE))
assert(big.equal(RATIO,RATIO))
''','''import "core:mem"
VALUE::Integer(123456789012345678901234567890)
RATIO::Rational(22,13)
''',flags=())
        self.invoke(self.program('context.allocator=mem.nil_allocator();copy:=CHOICE;_=copy',
            'import "core:mem"\nCHOICE::#switch {case true:Integer(123);case:Integer(0)}\n'),fail='allocation failed')
        self.invoke(self.program('CHOICE+=1',
            'CHOICE::#switch {case true:Integer(123);case:Integer(0)}\n'),command='check',fail='read-only')

    def test_constants_are_readonly_and_domain_errors_map_to_source(self):
        for body in ['VALUE=Integer(2)','VALUE+=1','_=&VALUE','delete(VALUE)']:
            with self.subTest(body=body):
                self.invoke(self.program(body,'VALUE::Integer(123)\n'),command='check',fail='read-only')
        for prefix,message in [('VALUE::Integer(2)/0','divisor'),('VALUE::Integer(2)**-1','nonnegative'),
                               ('VALUE::Rational(1,0)','denominator'),('VALUE::Rational(2)**0.5','whole exponents'),
                               ('VALUE::Integer(1)+Rational(2)','matching types'),('VALUE::Integer(1)<<-1','nonnegative')]:
            with self.subTest(prefix=prefix):self.invoke(self.program('_=VALUE',prefix+'\n'),command='check',fail=message)
        for expression in ['Integer(Rational(2))','Rational(Rational(2),3)']:
            self.invoke(self.program('_=VALUE','VALUE::'+expression+'\n'),command='check',fail='integer inputs')

    def test_fn_and_run_results_preserve_values_before_deferred_deletion(self):
        self.run_big('''
a:=calculate(22);b:=#run calculate(22);c:=VALUE
expect(a,"705429498686404044207947776")
expect(b,"705429498686404044207947776")
expect(c,"705429498686404044207947776")
a+=1;assert(b==c && a!=b)
delete(a);delete(b);delete(c)
''','''calculate::fn(n:int)->Integer{value:=Integer(n)**20;defer delete(value);return value}
VALUE::#run calculate(22)
''')

    def test_run_constructor_long_numbers_and_rational_components(self):
        huge='1234567890'*600
        self.run_big('''
a:=#run Integer('''+huge+''')
expect(a,'''+json.dumps(huge)+''');delete(a)
b:=#run Rational(22,13);expect(b,"22/13");delete(b)
c:=NUMERATOR;expect(c,"22");delete(c)
''','''RATIO::Rational(22,13)
NUMERATOR::RATIO.numerator
''')

    def test_nested_fn_run_records_slices_and_input_borrowing(self):
        self.run_big('''
a:=#run make_report()
expect(a.value,"123456789012345678901234567890")
expect(a.ratio,"1/3")
expect(a.rows[0],"7");expect(a.rows[1],"8")
b:=make_report()
a.value+=1;assert(a.value!=b.value)
pure.destroy(a);pure.destroy(b)
''','''import "thor:pure"
Report::struct{value:Integer,ratio:Rational,rows:[]Integer}
identity::fn(value:Integer)->Integer{return value}
make_report::fn()->Report{
 number:=Integer(123456789012345678901234567890)
 defer delete(number)
 rows:=make([]Integer,2);rows[0]=Integer(7);rows[1]=Integer(8)
 return Report{identity(number),Rational(1,3),rows}
}
''')

    def test_big_conditions_select_runs_and_switches_lazily(self):
        self.run_big('''
a:=SELECTED;b:=CHOICE
expect(a,"22");expect(b,"33")
delete(a);delete(b)
''','''VALUE::Integer(123456789012345678901234567890)
identity::fn(n:int)->Integer{return Integer(n)}
when VALUE>0 {SELECTED::#run identity(22)} else {SELECTED::#run missing()}
CHOICE::#switch VALUE {case 0..<100: Integer(0);case: #run identity(33)}
''')

    def test_run_uses_big_constant_inputs_and_nested_runtime_bindings(self):
        self.run_big('''
for _ in 0..<3 {
 a:=#run identity(VALUE);b:=#run identity(VALUE)
 a+=1;assert(a!=b);expect(b,"123456789012345678901234567890")
 delete(a);delete(b)
}
''','''VALUE::Integer(123456789012345678901234567890)
identity::fn(value:Integer)->Integer{return value}
''')

    def test_imported_big_constants_are_values_and_local_constants_stay_readonly(self):
        self.run_big('''
LOCAL::library.VALUE+1
a:=LOCAL;expect(a,"124");delete(a)
''','import "library"\n',files={'library/main.thor':'package library\nVALUE::Integer(123)\n'})

    def test_constant_operators_match_independent_native_runtime_operations(self):
        import random
        rng=random.Random(90331)
        declarations=[];checks=[]
        for index in range(8):
            a=rng.randrange(-2**220,2**220);b=rng.randrange(1,2**160)*rng.choice([-1,1])
            prefix=f'I{index}_'
            declarations.extend([f'{prefix}A::Integer({a})',f'{prefix}B::Integer({b})'])
            checks.append('{a:=Integer('+str(a)+');b:=Integer('+str(b)+')')
            for step,op in enumerate(['+','-','*','/','%','%%','&','|','~','&~']):
                name=prefix+'RESULT_'+str(step)
                declarations.append(f'{name}::{prefix}A {op} {prefix}B')
                checks.append(f'assert({name}==a {op} b)')
            for step,expression in enumerate(['a<<4','a>>4','a**3','~a','-a']):
                name=prefix+'UNARY_'+str(step)
                declarations.append(name+'::'+expression.replace('a',prefix+'A'))
                checks.append(f'assert({name}==({expression}))')
            checks.append('delete(a);delete(b)}')
            prefix=f'R{index}_'
            declarations.extend([f'{prefix}A::Rational({a},13)',f'{prefix}B::Rational({b},17)'])
            checks.append('{a:=Rational('+str(a)+',13);b:=Rational('+str(b)+',17)')
            for step,op in enumerate(['+','-','*','/','%','%%']):
                name=prefix+'RESULT_'+str(step)
                declarations.append(f'{name}::{prefix}A {op} {prefix}B')
                checks.append(f'assert({name}==a {op} b)')
            checks.append('delete(a);delete(b)}')
        self.run_big('\n'.join(checks),'\n'.join(declarations)+'\n')

    def test_big_switch_ranges_reject_overlaps_and_preserve_open_boundaries(self):
        self.run_big('assert(SELECTED==2)','VALUE::Integer(3)\nSELECTED::#switch VALUE {case 0..<3:1;case 3..<5:2;case:0}\n')
        self.invoke(self.program('_=SELECTED','VALUE::Integer(3)\nSELECTED::#switch VALUE {case 0..<4:1;case 3..<5:2;case:0}\n'),command='check',fail='overlapping')

    def test_local_numeric_constants_remain_usable_inside_expression_helpers(self):
        self.run_big('''
LOCAL::Integer(123)
CHOICE::#switch LOCAL {case 0..<100:Integer(0);case:LOCAL+1}
RATIO::#switch {case LOCAL>100:Rational(1,3);case:Rational(0)}
LOCAL_RUN::#run Integer(5)
assert(LOCAL==123 && CHOICE==124 && RATIO==Rational(1,3))
assert(LOCAL_RUN==5)
copy:=CHOICE
copy+=1
assert(CHOICE==124 && copy==125)
text:=f"local {LOCAL} {RATIO}"
assert(text=="local 123 1/3")
delete(copy);delete(text)
''')


class BigValueProtocolTests(base.unittest.TestCase):
    def test_arbitrarily_long_digits_and_canonical_protocol(self):
        digits='9876543210'*600
        number=bigvalues.decimal_integer(digits)
        packet=bigvalues.packet('big_integer',number)
        self.assertEqual(packet['value'],digits)
        self.assertEqual(bigvalues.number(packet),number)
        code=values.expression(packet,'Integer','meta',big=lambda v,t:'STATIC')
        self.assertEqual(code,'STATIC')
        ratio=bigvalues.packet('big_rational',Fraction(-22,13))
        self.assertEqual(bigvalues.number(ratio),Fraction(-22,13))
        self.assertTrue(values.has_owned(ratio))
        self.assertFalse(values.has_owned(ratio,slices_only=True))
        self.assertIn('clone(STATIC)',values.materialize(ratio,'Rational','meta','pure','p',big=lambda v,t:'STATIC'))

    def test_invalid_canonical_contents_are_rejected(self):
        for packet in [dict(kind='big_integer',value='01'),dict(kind='big_integer',value='-0'),
                       dict(kind='big_integer',value='1);panic("bad")'),dict(kind='big_rational',numerator='2',denominator='4'),
                       dict(kind='big_rational',numerator='1',denominator='0'),dict(kind='big_rational',numerator='1',denominator='-2')]:
            with self.subTest(packet=packet):
                with self.assertRaises(ValueError):values.expression(packet,'T','meta')

class BigCacheTests(base.unittest.TestCase):
    invoke=language.BigLanguageTests.invoke
    program=language.BigLanguageTests.program

    def test_cached_contents_survive_runtime_edits_and_invalidate_for_pure_changes(self):
        from tests.test_t21_t22 import CacheTests
        report=lambda result:CacheTests.report(self,result)
        again=lambda root:CacheTests.again(self,root,flags=['--track-allocations=basic'])
        source=self.program('a:=#run work();b:=#run work();a+=1;assert(a!=b);delete(a);delete(b)',
                            'work::fn()->Integer{return Integer(123456789012345678901234567890)}\n')
        root,first=self.invoke(source,flags=['--cache-report','--track-allocations=basic'])
        self.assertGreaterEqual(report(first)['writes'],1)
        warm=again(root)
        self.assertGreaterEqual(report(warm)['hits'],2)
        self.assertEqual(warm.stderr,'')
        (root/'input/main.thor').write_text(source.replace('a+=1','extra:=22;_=extra;a+=1'))
        self.assertGreaterEqual(report(again(root))['hits'],2)
        (root/'input/main.thor').write_text(source.replace('567890)','567891)'))
        changed=again(root)
        self.assertGreaterEqual(report(changed)['writes'],1)
        packets=[json.loads(p.read_text())['value'] for p in (root/'input/.thor-cache/eval-v1').glob('*.json')]
        self.assertTrue(all(v['kind']=='big_integer' and set(v)=={'kind','value'} for v in packets))
