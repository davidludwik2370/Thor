"""Logical type identities and constructor status types agree with the reference."""
import json, subprocess, tempfile, unittest
from pathlib import Path
from thorlang.driver import Project, frontend_binary
from tests import test_rust_big_values as numeric


class RustBigResolutionTests(unittest.TestCase):
    setUpClass=classmethod(numeric.RustBigValueTests.setUpClass.__func__)

    def compare(self,body,prefix='',files=None,untyped_only=False):
        with tempfile.TemporaryDirectory(prefix='thor-big-resolution-') as directory:
            source=Path(directory).resolve()/'main.thor'
            source.write_text('package main\n'+prefix+'\ninspect::proc(){\n'+body+'\n}\n')
            for name,content in (files or {}).items():
                path=source.parent/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            project=Project(source,frontend_binary('odin')[0],['-debug'],cache_frontend=False)
            resolver=project.resolver
            def shape(typ):
                out=dict(kind=typ.kind)
                if typ.kind in {'scalar','string'}:out.update(name=typ.name,untyped=typ.untyped)
                if typ.kind in {'slice','dynamic','pointer','array'}:out['elem']=shape(typ.elem)
                if typ.kind=='array':out['length']=typ.length
                return out
            expected={}
            for node in project.sources[source].nodes:
                if node.kind!='Call' or node.role('callee').text!='query':continue
                label,arg=node.group('args')
                is_type=arg.source.denotes_type(arg)
                typ=resolver.type_node(arg) if is_type else resolver.type_of(arg)
                expected[json.loads(label.text)]=dict(shape=shape(typ),type_operand=is_type,untyped_operand=resolver.membership_untyped(arg))
            result=subprocess.run([numeric.BINARY],input=json.dumps(dict(operation='resolve',file=str(source)))+'\n',capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            packet=json.loads(result.stdout)
            if untyped_only:
                expected={name:value['untyped_operand'] for name,value in expected.items()}
                self.assertIn('ok',packet,packet)
                self.assertEqual({name:value['untyped_operand'] for name,value in packet['ok'].items()},expected)
            else:
                self.assertEqual(packet,dict(ok=expected))
            return expected

    def test_predefined_aliases_arrays_values_and_rational_properties(self):
        result=self.compare('''a:Integer;b:Rational;p:^Rational
query("integer",Integer);query("rational",Rational)
query("alias",I);query("array",[2]R)
query("value",a);query("constructor",I(12))
query("numerator",b.numerator);query("pointer_property",p.denominator)
''','I::Integer\nR::Rational\n')
        self.assertEqual(result['value']['shape'],dict(kind='big_integer'))
        self.assertFalse(result['value']['type_operand'])

    def test_qualified_selected_and_wildcard_aliases(self):
        for prefix in ['import "thor:big" / Integer, Rational\n',
                       'import * "thor:big"\n',
                       'import _ "thor:big" / Integer, Rational\n']:
            with self.subTest(prefix=prefix):
                self.compare('''a:Integer;b:Rational
query("selected",Integer);query("value",a);query("property",b.denominator)
''',prefix)
        self.compare('query("qualified",numbers.Integer);value:numbers.Rational;query("member",value.numerator)',
                     'import numbers "thor:big"\n')

    def test_text_constructors_have_boolean_statuses(self):
        self.compare('''a,ok:=Integer("12")
b,b_ok:=Rational("1.2(3)")
c,c_ok:=Rational(numerator="12",denominator="13")
query("a",a);query("ok",ok);query("b",b);query("b_ok",b_ok);query("c",c);query("c_ok",c_ok)
''')

    def test_contextual_and_generic_helper_results_keep_logical_types(self):
        self.compare('''a:=identity(Integer(3));b:=identity(Rational(3,7))
query("integer",a);query("rational",b);query("returned",result());query("property",b.numerator)
''','identity::proc(value:$T)->T{return value}\nresult::proc()->Integer{return 0}\n')

    def test_user_type_and_local_value_shadowing(self):
        self.compare('''query("type",Integer)
a:=Integer{3};query("record",a);query("field",a.numerator)
{Rational:=7;query("local",Rational)}
''','Integer::struct{numerator:int}\n')
        self.compare('query("alias",Integer);value:Integer;query("value",value)',
                     'Integer::u64\n')

    def test_mutable_values_default_their_types_before_specialization(self):
        result=self.compare('''integer:=7;real:=1.25;text:="text";alias:=integer
frozen :> 7
CONSTANT::7
query("integer",integer);query("real",real);query("text",text);query("alias",alias)
query("frozen",frozen);query("constant",CONSTANT);query("literal",7)
query("generic",identity(integer))
''','identity::proc(value:$T)->T{return value}\n')
        for name in ['integer','real','text','alias','frozen','generic']:
            self.assertFalse(result[name]['shape']['untyped'])
        self.assertTrue(result['constant']['shape']['untyped'])
        self.assertTrue(result['literal']['shape']['untyped'])

    def test_ordinary_huge_literals_and_native_overflow_expressions_do_not_infer_big(self):
        result=self.compare('query("literal",'+('1234567890'*600)+');query("native",(u128(1)<<127)*2)')
        self.assertEqual(result['literal']['shape'],dict(kind='scalar',name='int',untyped=True))
        self.assertEqual(result['native']['shape'],dict(kind='scalar',name='u128',untyped=False))

    def test_cross_package_and_native_aliases_keep_the_bridge_identity(self):
        self.compare('''a:=provider.integer();b:=provider.rational()
query("integer",a);query("rational",b);query("property",b.numerator)
''','import "provider"\n',files={'provider/main.odin':'''package provider
import "thor:big"
integer::proc()->big.Integer{return {}}
rational::proc()->big.Rational{return {}}
'''})

    def test_operand_coercion_checks_syntax_and_both_arithmetic_operands(self):
        result=self.compare('''typed:=1;count:=uint(2);text:="text";pair:Pair
N::1
query("comparison",typed==1);query("left_typed",typed+1);query("right_typed",1+typed)
query("constant",N+1);query("shift",1<<count);query("typed_shift",u8(1)<<count)
query("min",min(N,2));query("clamp",clamp(N,0,2))
query("array_len",len([1,2]));query("literal_len",len("text"));query("runtime_len",len(text))
query("conditional",(typed==1)?1:2);query("nil",nil)
query("nominal_len",len(pair))
''',prefix='Pair::distinct [2]int\n',untyped_only=True)
        for name in ['comparison','constant','shift','min','clamp','array_len','literal_len','conditional','nil','nominal_len']:
            self.assertTrue(result[name])
        for name in ['left_typed','right_typed','typed_shift','runtime_len']:
            self.assertFalse(result[name])


if __name__=='__main__':unittest.main()
