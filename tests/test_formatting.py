"""Expanded interpolation: exact numbers, Odin conventions, and ownership."""
import json
import unittest
from tests import test_thor as base


class FormattingTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def fields(self, cases, prefix=''):
        body = []
        for index, (field, expected) in enumerate(cases):
            body.append(f't{index}:=f"{{{field}}}"; assert(t{index}=={json.dumps(expected,ensure_ascii=False)}); delete(t{index})')
        result=self.invoke(self.program('\n'.join(body),prefix),flags=['--track-allocations=basic'])
        self.assertNotIn('still allocated',result[1].stdout+result[1].stderr)
        return result

    def test_native_defaults_explicit_precision_and_percent(self):
        self.fields([
            ('1.25:f','1.250'),('1.25:F','1.250'),('1.25:e','1.250000e+00'),
            ('1.25:E','1.250000E+00'),('1.25:g','1.25'),('1.25:G','1.25'),
            ('1.25:.2f','1.25'),('1.25:.f','1'),('0.123456:.2%','12.35%'),
            ('0.125:%','12.500%'),('2.5:.0f','2'),('3.5:.0f','4'),
            ('-0.001:.2f','-0.00'),('12345.0:.3g','1.23e+04'),
            ('f32(1.2):g','1.2'),
        ])

    def test_flags_padding_grouping_and_custom_fill(self):
        self.fields([
            ('-42:08d','-0000042'),('42:08.4d','    0042'),('Integer(42):08.4d','    0042'),('42:+08d','+0000042'),('42: 8d','      42'),
            ('42:#08x','0x00002a'),('42:-08d','42      '),
            ('-42:\'0\'8d','00000-42'),('"é😀":5s','   é😀'),
            ('"é😀":-5s','é😀   '),('"é😀":\'_\'^5s','_é😀__'),
            ('"é😀":\'_\'5s','___é😀'),('"é😀":\'_\'-5s','é😀___'),
            ('1234567.125:,.2f','1,234,567.12'),('-1234:012,.1f','-00001,234.0'),
            ('1234567.0:,.2e','1.23e+06'),('0.123456:10.2%','    12.35%'),
            ('1234:,','1,234'),('1234.5:,','1,234.5'),
        ])

    def test_native_verbs_strings_bytes_runes_enums_and_aggregates(self):
        self.fields([
            ('true:t','true'),('255:#x','0xff'),('255:X','FF'),('10:b','1010'),
            ('12:z','10'),('8:o','10'),('65:c','A'),("'é':q","'é'"),
            ('65:U','U+41'),('"é😀z":.2s','é😀'),('"é😀z":8.2s','      é😀'),
            ('"ab":x','6162'),('"ab": X','61 62'),
            ('"ab":T','string'),('1.0:h','0h3ff0000000000000'),('1.0:+h','0h3ff0000000000000'),
            ('[]u8{65,66}:x','4142'),('[]u8{65,66}: X','41 42'),
            ('1024:M','1.00KiB'),('1024:#m','1.00 kib'),
            ('Color.Red:v','Red'),('Color.Red:d','0'),('[1,2]:v','[1, 2]'),
            ('R{2}:v','R{x = 2}'),
        ],'Color::enum{Red,Blue}\nR::struct{x:int}\n')
        self.invoke(self.program('''value:="é😀z"
actual:=f"{value:.2q}";defer delete(actual)
expected:=fmt.aprintf("%q","é😀");defer delete(expected)
if actual!=expected {panic("native Unicode quoting")}
''','import "core:fmt"\n'))

    def test_exact_integer_bases_decimal_percent_and_type(self):
        self.fields([
            ('x:d','123456789012345678901234567890'),
            ('x:.2f','123456789012345678901234567890.00'),
            ('x:,.2f','123,456,789,012,345,678,901,234,567,890.00'),
            ('x:.4e','1.2346e+29'),('x:.4g','1.235e+29'),
            ('x:x','18ee90ff6c373e0ee4e3f0ad2'),('x:X','18EE90FF6C373E0EE4E3F0AD2'),
            ('Integer(42):#08x','0x00002a'),('Integer(-42):#08x','-0x0002a'),('Integer(12):#z','0z10'),('Integer(8):#o','0o10'),('Integer(10):#b','0b1010'),('Integer(42):+.1%','+4200.0%'),
            ('x:T','Integer'),('x:w','Integer(123456789012345678901234567890)'),
            ('Integer(1000000):g','1e+06'),('Integer(120):g','120'),
            ('u128(340282366920938463463374607431768211455):.2f','340282366920938463463374607431768211455.00'),
            ('Integer(1024):#M','1.00 KiB'),
        ],'x::Integer(123456789012345678901234567890)\n')

    def test_rational_rounding_ties_carries_and_signs(self):
        self.fields([
            ('Rational(1,3):.20f','0.33333333333333333333'),
            ('Rational(2,3):.6g','0.666667'),('Rational(1,7):.4e','1.4286e-01'),
            ('Rational(5,2):.0f','2'),('Rational(7,2):.0f','4'),
            ('Rational(-5,2):.0f','-2'),('Rational(-7,2):.0f','-4'),
            ('Rational(-1,1000):.2f','-0.00'),('Rational(9999,1000):.2f','10.00'),
            ('Rational(9999,1000):.2e','1.00e+01'),('Rational(9999,1000):.3g','10'),
            ('Rational(1,100000):.3g','1e-05'),('Rational(1234567,10):,.2f','123,456.70'),
            ('Rational(1,8):.2%','12.50%'),('Rational(1,3):v','1/3'),
            ('Rational(1,3):T','Rational'),('Rational(1,3):w','Rational(1, 3)'),
            ('Rational(0):.3e','0.000e+00'),('Rational(0):.3g','0'),
            ('Rational(1000):.4g','1000'),('Rational(1,10000):.2g','0.0001'),
        ])

    def test_large_precision_uses_sized_storage_at_runtime_and_compile_time(self):
        self.invoke(self.program('''
text:=render(1.25)
assert(len(text)==1002 && text[:5]=="1.250" && text[-1]=='0')
assert(text==AT_COMPTIME && text==EXPLICIT)
delete(text)
big_text:=f"{Rational(1,3):.1000f}"
assert(len(big_text)==1002 && big_text[2]=='3' && big_text[-1]=='3');delete(big_text)
scientific:=f"{1.25:.1000e}"
assert(len(scientific)==1006 && scientific[-4:]=="e+00");delete(scientific)
''','''render::fn(x:f64)->string{return f"{x:.1000f}"}
AT_COMPTIME::render(1.25)
EXPLICIT::#run render(1.25)
'''))

    def test_exact_big_formats_match_fn_run_and_cached_results(self):
        prefix='''render::fn()->string{return f"{Rational(1,3):.18g}|{Integer(123456789012345678901234567890):.4e}|{Rational(-1,1000):.2f}"}
AUTO::render()
EXPLICIT::#run render()
'''
        source=self.program('text:=render();assert(text==AUTO && text==EXPLICIT && text=="0.333333333333333333|1.2346e+29|-0.00");delete(text)',prefix)
        self.invoke(source,flags=['--track-allocations=basic'])

    def test_cached_precision_result_survives_runtime_edit_and_invalidates_on_format_change(self):
        from tests.test_t21_t22 import CacheTests
        source=self.program('assert(TEXT=="0.3333")','render::fn()->string{return f"{Rational(1,3):.4f}"}\nTEXT::#run render()\n')
        root,first=CacheTests.invoke(self,source,flags=['--cache-report'])
        self.assertGreaterEqual(CacheTests.report(self,first)['writes'],1)
        warm=CacheTests.again(self,root)
        self.assertGreaterEqual(CacheTests.report(self,warm)['hits'],1)
        (root/'input/main.thor').write_text(source.replace('assert(TEXT','extra:=22;_=extra;assert(TEXT'), newline='')
        self.assertGreaterEqual(CacheTests.report(self,CacheTests.again(self,root))['hits'],1)
        (root/'input/main.thor').write_text(source.replace('.4f','.6g').replace('"0.3333"','"0.333333"'), newline='')
        changed=CacheTests.again(self,root)
        self.assertGreaterEqual(CacheTests.report(self,changed)['writes'],1)

    def test_precision_complex_and_quaternion_components(self):
        self.invoke(self.program('''
c:=complex(1.25,2.5)
s:=f"{c:.400f}";assert(len(s)==806 && s[:5]=="1.250" && s[-1]=='i');delete(s)
a:=f"{c:.2e}";assert(a=="1.25e+00+2.50e+00i");delete(a)
q:=quaternion(real=1.0,imag=-2.0,jmag=3.0,kmag=4.0)
b:=f"{q:.1f}";assert(b=="1.0-2.0i+3.0j+4.0k");delete(b)
'''))

    def test_side_effect_order_nested_conditional_and_automatic_cleanup(self):
        self.invoke('''package main
import "core:testing"
@(test) all::proc(t:^testing.T){
 for flag in [false,true] {
  n:=0
  s:=f`{next(&n):03d}|{(flag ? f"{next(&n):.2f}" : f"{next(&n):.1%}"):8s}|{Rational(1,3):.40g}`
  testing.expect(t,n==2);delete(s)
  consume(f"{Integer(42):#08x}");consume(f"{Rational(2,3):.4f}")
 }
}
next::proc(n:^int)->int{n^+=1;return n^}
consume::proc(text:string){assert(len(text)>0)}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_format_type_errors_and_bare_rational_general_are_rejected(self):
        for field,message in [
            ('Rational(1,3):g','requires explicit precision'),('Rational(1,2):G','requires explicit precision'),
            ('Rational(2):x','Rational formatting'),('Integer(2):h','Integer formatting'),
            ('"x":f','String formatting'),('true:d','Boolean formatting'),
            ('true:,','Grouping requires'),('3.5:d','Float formatting'),
            ('[1,2]:.400f','not aggregate'),('2:#,x','Grouping requires'),
        ]:
            with self.subTest(field=field):
                self.invoke(self.program('_=f"{'+field+'}"'),command='check',fail=message)

    def test_exact_rounding_matches_independent_decimal_reference(self):
        from decimal import Decimal, localcontext
        import random
        rng=random.Random(741)
        cases=[]
        with localcontext() as ctx:
            ctx.prec=200
            for _ in range(48):
                numerator=rng.randint(-10**35,10**35)
                denominator=rng.randint(1,10**12)
                precision=rng.randrange(0,20)
                expected=format(Decimal(numerator)/Decimal(denominator),f'.{precision}f')
                cases.append((f'Rational({numerator},{denominator}):.{precision}f',expected))
        self.fields(cases)

    def test_large_integer_and_hex_precision_and_byte_limits(self):
        self.invoke(self.program('''a:=f"{42:.1000d}";assert(len(a)==1000 && a[-2:]=="42");delete(a)
b:=f"{Integer(42):#.1000x}";assert(len(b)==1002 && b[:2]=="0x" && b[-2:]=="2a");delete(b)
c:=f"{\"ab\":.1000x}";assert(c=="6162");delete(c)
d:=f"{[]u8{65,66,67}:.2x}";assert(d=="4142");delete(d)
'''))

    def test_format_syntax_errors(self):
        for spec,message in [('>8','no longer uses'),('<8','no longer uses'),("'x'>8",'no longer uses'),
                             ('^08','cannot be combined'),('^-8','cannot be combined'),("'x'08",'quoted fill'),
                             ('8.2','explicit verb'),('*f','Unsupported'),('[2]d','Unsupported'),
                             ('8.2n','Unsupported'),('++,d','Duplicate')]:
            with self.subTest(spec=spec):
                self.invoke(self.program('_=f"{1:'+spec+'}"'),command='check',fail=message)

    def test_overlong_format_numbers_fail_cleanly_before_python_integer_conversion(self):
        from thorlang.formatting import parse_format
        def reject(message):raise RuntimeError(message)
        for suffix in ['9'*5000+'d','.'+'9'*5000+'f']:
            with self.subTest(suffix=suffix[:10]):
                with self.assertRaisesRegex(RuntimeError,'too large'):
                    parse_format(suffix,reject)
        with self.assertRaisesRegex(RuntimeError,'quoted fill'):
            parse_format("' '08d",reject)

    def test_custom_formatter_registry_and_expanded_source_forms(self):
        self.invoke(self.program('''
registry:map[typeid]fmt.User_Formatter
registry[typeid_of(Item)]=custom;fmt.set_user_formatters(&registry)
defer {fmt._user_formatters=nil;delete(registry)}
s:=f"{Item{7}:10v}";assert(s=="    item-7");delete(s)
custom_format:=f"{Item{7}:f}";assert(custom_format=="item-7");delete(custom_format)
record:=Record{3,"hi"}
source:=f"{record:w}";assert(len(source)>0);delete(source)
expanded:=f"{record:#v}";assert(strings.contains(expanded,"\\n"));delete(expanded)
''','''import "core:fmt"
import "core:strings"
Item::struct{x:int}
Record::struct{x:int,text:string}
custom::proc(fi:^fmt.Info,arg:any,verb:rune)->bool {
 if arg.id!=typeid_of(Item){return false}
 value:=arg.(Item);fmt.fmt_string(fi, "item-7", 's');return true
}
'''))


if __name__ == '__main__': unittest.main()
