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

    def test_padding_keeps_generic_pure_hover_and_literal_completion(self):
        text='''package main
Padded::struct{_:[]int,value:int,_:^int}
add::fn(a,b:$T)->T{return a+b}
RUN::#run add(Padded{value=2},Padded{value=3})
main::proc(){
    x:=Padded{value=4}
    total:>add(x,x)
    _=total.value;_=RUN.value
    _=x+{}
    x+={}
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        for fragment in ('total.value','RUN.value'):
            self.assertIn('Padded',json.dumps(self.hover(client,path,text,fragment)))
        for fragment,offset in (('x+{}',3),('x+={}',4)):
            completed=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},
                'position':self.position(text,fragment,offset),'context':{'triggerKind':1}})
            items=completed.get('items',[]) if isinstance(completed,dict) else completed
            self.assertIn('value',[item['label'] for item in items])
            self.assertNotIn('_',[item['label'] for item in items])
        self.assert_original_identifier_tokens(path,text,self.semantic_spans(client,path,text))

    def test_padding_edits_do_not_hide_named_field_diagnostics(self):
        text='''package main
Padded::struct{_:int,value:int,_:bool}
add::proc(a,b:$T)->T{return a+b}
main::proc(){x:=Padded{};_=add(x,x)}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)

        changed=text.replace('_:int','_:map[int]int')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        broken=changed.replace('value:int','value:[]int')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':3},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(path.as_uri(),contains='self-contained fixed-size')
        start=self.position(broken,'a+b',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+3}})
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':4},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_padding_expansion_purity_error_underlines_the_call(self):
        text='''package main
Padded::struct{_:[]int,number:int}
inspect::fn(value:Padded)->int{buffer,_:=expand_values(value);_=buffer;return 0}
main::proc(){}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='reference-containing padded records')
        self.assertEqual(len(errors),1,errors)
        start=self.position(text,'expand_values(value)',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+len('expand_values(value)')}})


if __name__=='__main__':unittest.main()
