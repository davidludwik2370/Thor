"""Struct arithmetic editing uses original types, fields, and source spans."""
import json
import unittest
from tests import test_tls as base


class StructArithmeticTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_result_hover_completion_literal_fields_and_readonly(self):
        text='''package main
Foo::struct{alpha,beta:int}
main::proc(){
    x:=Foo{1,2}
    total:>x+{alpha=3,beta=4}
    left:>{alpha=3,beta=4}+x
    x+={alpha=2,beta=3}
    _=x+{}
    _={}+x
    x+={}
    _=total.alpha;_=left.beta
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        for fragment in ('total.alpha','left.beta'):
            hover=self.hover(client,path,text,fragment)
            self.assertIn('Foo',json.dumps(hover))
        for fragment,offset in (('x+{}',3),('{}+x',1),('x+={}',4)):
            completed=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},
                'position':self.position(text,fragment,offset),'context':{'triggerKind':1}})
            self.assertIn('alpha',json.dumps(completed))
            self.assertIn('beta',json.dumps(completed))
        spans=self.semantic_spans(client,path,text)
        self.assert_original_identifier_tokens(path,text,spans)

    def test_reference_field_errors_and_unsaved_recovery(self):
        text='// 😀 header\npackage main\nFoo::struct{value:[]int}\nmain::proc(){x:=Foo{};_=x+x}\n'
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='self-contained fixed-size')
        self.assertEqual(len(errors),1,errors)
        start=self.position(text,'x+x',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+3}})
        self.assertNotIn('__thor_',json.dumps(errors))
        changed=text.replace('value:[]int','value:int')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_constants_and_pure_results_keep_struct_hover(self):
        text='''package main
Foo::struct{alpha,beta:int}
VALUE::Foo{1,2}+{3,4}
calculate::fn(x:Foo)->Foo{return x+{1,2}}
RESULT::#run calculate(VALUE)
main::proc(){local:>RESULT+{1,2};_=local.alpha}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        self.assertIn('Foo',json.dumps(self.hover(client,path,text,'local.alpha')))
        spans=self.semantic_spans(client,path,text)
        self.assert_original_identifier_tokens(path,text,spans)

    def test_generic_reference_error_points_to_original_arithmetic(self):
        text='''package main
Foo::struct{data:[]int}
add::proc(a,b:$T)->T{return a+b}
main::proc(){x:=Foo{};_=add(x,x)}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='self-contained fixed-size')
        self.assertEqual(len(errors),1,errors)
        start=self.position(text,'a+b',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+3}})
        self.assertNotIn('__thor_',json.dumps(errors))


if __name__=='__main__':unittest.main()
