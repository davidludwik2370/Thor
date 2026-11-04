"""Import usage depends on source bindings, independently of call result inference."""
import unittest

from tests import test_tls as base


class SelectedImportUsageTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    unused_imports=base.TLSTests.unused_imports

    def test_math_overloads_in_immutable_initializers_and_incomplete_type_calls(self):
        for param,arg in [('f32','degrees'),('f64','degrees'),('$T','degrees'),('string','degrees')]:
            with self.subTest(param=param):
                text='''// 😀
package main
import "core:math" / sin, cos, to_radians, round
rotate::proc(degrees:'''+param+''')->f64{
    angle:>to_radians('''+arg+''')
    return f64(sin(angle)+cos(angle))
}
main::proc(){}
'''
                client,main=self.open(text,compiler=False,unused_imports=True)
                errors=self.unused_imports(client,main)
                self.assertEqual(len(errors),1,errors)
                self.assertEqual(errors[0]['range']['start'],self.position(text,'round\n',0))

    def test_calls_aliases_and_exported_aliases_survive_unsaved_edits(self):
        text='''package main
import "library" / chosen, unused
use::proc(n:f32){value:>chosen(n);_=value}
main::proc(){}
'''
        library='''package library
import "core:math"
chosen::math.to_radians
unused::proc(){}
'''
        client,main=self.open(text,files={'library/main.odin':library},compiler=False,unused_imports=True)
        errors=self.unused_imports(client,main)
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(text,'unused\n',0))
        for version,body,expected in [(2,'alias::chosen;_=alias(n)',1), (3,'_=library.chosen(n)',2),
                                      (4,'chosen:=library.chosen;_=chosen(n)',2), (5,'_=chosen("invalid")',1)]:
            changed=text.replace('value:>chosen(n);_=value',body)
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            errors=self.unused_imports(client,main)
            self.assertEqual(len(errors),expected,errors)
            if version in {3,4}:
                self.assertEqual(errors[0]['range']['start'],self.position(changed,'chosen,',0))

    def test_parameters_local_aliases_field_names_and_named_arguments_are_not_uses(self):
        for body in ['main::proc(){chosen:=library.chosen;_=chosen(f32(1))}',
                     'use::proc(chosen:f32){_=chosen}\nmain::proc(){library.other(chosen=1)}',
                     'main::proc(){record:=struct{chosen:int}{chosen=1};_=record.chosen;library.other(chosen=1)}']:
            with self.subTest(body=body):
                text='package main\nimport "library" / chosen\n'+body+'\n'
                library='package library\nchosen::proc(value:f32)->f32{return value}\nother::proc(chosen:int){}\n'
                client,main=self.open(text,files={'library/main.thor':library},compiler=False,unused_imports=True)
                errors=self.unused_imports(client,main)
                self.assertEqual(len(errors),1,errors)
                self.assertEqual(errors[0]['range']['start'],self.position(text,'chosen\n',0))

    def test_shadowing_namespace_and_same_name_initializer_keep_the_import_binding(self):
        for body in ['use::proc(math:int){angle:>to_radians(f32(math));_=angle}',
                     'main::proc(){to_radians:=to_radians(f32(90));_=to_radians}',
                     'main::proc(){record:=map[f64]int{PI=1};_=record;_=to_radians(f32(90))}',
                     'build::proc($T:typeid)->T{return T{PI=1}}\nmain::proc(){_=build(map[f64]int);_=to_radians(f32(90))}']:
            with self.subTest(body=body):
                text='package main\nimport "core:math" / to_radians, PI\n'+body+'\n'
                client,main=self.open(text,compiler=False,unused_imports=True)
                errors=self.unused_imports(client,main)
                expected=0 if 'PI=1' in body else 1
                self.assertEqual(len(errors),expected,errors)
                if errors:self.assertEqual(errors[0]['range']['start'],self.position(text,'PI\n',0))


if __name__=='__main__':unittest.main()
