"""Whole-element mapping stays visible through TLS's original-source AST."""
import json
import unittest
from tests import test_tls as base


class ParallelMapInputTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)

    def complete(self,client,main,text,phrase,offset):
        return client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,phrase,offset),'context':{'triggerKind':1}})['items']

    def test_scalar_and_whole_struct_output_types_and_fields(self):
        text='''package main
import b "thor:basic"
Row::struct{x:int,label:string}
twice::proc(x:int)->int{return x*2}
identity::proc(x:$T)->T{return x}
main::proc(){
    numbers:=b.pmap([]int{2,3},twice)
    rows:=b.pmap([]Row{{7,"ok"}},identity)
    _=numbers[0];_=rows[0].x
    delete(numbers);delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[]int',self.hover(client,main,text,'numbers:=')['contents']['value'])
        self.assertIn('Row',self.hover(client,main,text,'rows:=')['contents']['value'])
        names={item['label'] for item in self.complete(client,main,text,'rows[0].x',len('rows[0].'))}
        self.assertTrue({'x','label'}<=names,names)
        hover=self.hover(client,main,text,'pmap([]int')
        self.assertTrue(hover and hover.get('contents'),hover)
        self.assertNotIn('__thor_pmap_spread',json.dumps(hover))
        signature=client.request('textDocument/signatureHelp',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'[]int{2,3},twice',len('[]int{2,3},')),'context':{'triggerKind':1,'isRetrigger':False}})
        self.assertTrue(signature and signature.get('signatures'),signature)
        self.assertNotIn('__thor_pmap_spread',json.dumps(signature))

    def test_inline_callback_preserves_nested_struct_result_types(self):
        text='''package main
import "thor:basic"
Row::struct{number:int}
main::proc(){
    rows:=basic.pmap([]Row{{7}},proc(value:$T)->(item:T,ok:bool){return value,true})
    _=rows[0].item.number
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        fields={item['label'] for item in self.complete(client,main,text,'rows[0].item.number',len('rows[0].item.'))}
        self.assertIn('number',fields)

    def test_unsaved_input_mode_change_and_ambiguity_recovery(self):
        text='''package main
import "thor:basic"
Row::struct{a,b:int}
whole::proc(value:Row)->int{return value.a+value.b}
parts::proc(a,b:int)->int{return a*b}
calculate::proc{whole,parts}
main::proc(){
    _="😀";rows:=basic.pmap([]Row{{2,3}},whole)
    _=rows[0];delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('}},whole)','}},calculate)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        diagnostics=client.diagnostic(main.as_uri(),contains='Ambiguous pmap callback')
        error=next(d for d in diagnostics if 'Ambiguous pmap callback' in d['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'basic.pmap',0))
        self.assertNotIn('__thor',json.dumps(diagnostics))
        fixed=changed.replace('}},calculate)','}},parts)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[]int',self.hover(client,main,fixed,'rows:=')['contents']['value'])

    def test_zero_argument_callback_and_explicit_run_have_inferred_types(self):
        text='''package main
import "thor:basic"
Row::struct{number:int}
identity::proc(row:$T)->T{return row}
constant::proc()->int{return 7}
main::proc(){
    values:=basic.pmap([]struct{}{{},{}},constant)
    rows:=#run basic.pmap([]Row{{7}},identity)
    _=values[0];_=rows[0].number
    delete(values);delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[]int',self.hover(client,main,text,'values:=')['contents']['value'])
        self.assertIn('Row',self.hover(client,main,text,'rows:=')['contents']['value'])
        self.assertIn('number',{item['label'] for item in self.complete(client,main,text,'rows[0].number',len('rows[0].'))})
