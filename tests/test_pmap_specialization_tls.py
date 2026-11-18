"""Dispatch selection preserves public TLS types, signatures and unsaved updates."""
import json
import unittest
from tests import test_tls as base


class ParallelMapSpecializationTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)

    def fields(self,client,main,text):
        result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rows[0].item.value',len('rows[0].item.')),'context':{'triggerKind':1}})
        return {item['label'] for item in result['items']}

    def test_unsaved_static_to_factory_switch_preserves_nested_result_fields(self):
        text='''package main
import "thor:basic"
Result::struct{value:int}
make::proc(x:$T)->(item:Result,ok:bool){return Result{int(x)},true}
other::proc(x:int)->(item:Result,ok:bool){return Result{x*2},true}
factory::proc()->proc(int)->(item:Result,ok:bool){return other}
main::proc(){
    rows:=basic.pmap([]int{2,3},make)
    _=rows[0].item.value
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('value',self.fields(client,main,text))
        initial=self.hover(client,main,text,'rows:=')
        self.assertIn('[]struct',json.dumps(initial))
        for version in range(2,5):
            changed=text.replace('},make)','},factory())') if version%2==0 else text
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            client.diagnostic(main.as_uri(),empty=True)
            self.assertIn('value',self.fields(client,main,changed))
            hover=self.hover(client,main,changed,'rows:=')
            self.assertIn('[]struct',json.dumps(hover))
            self.assertNotIn('_pmap_static',json.dumps(hover))

    def test_polymorphic_run_result_and_public_signature_remain_visible(self):
        text='''package main
import "thor:parallel"
twice::proc(x:$T)->(number:T,ok:bool){return x*2,true}
alias::twice
main::proc(){
    rows:=#run parallel.pmap([]i16{2,3},alias,workers=2)
    _=rows[0].number
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[]struct',json.dumps(self.hover(client,main,text,'rows:=')))
        field=self.hover(client,main,text,'number\n')
        self.assertIn('i16',json.dumps(field))
        hover=self.hover(client,main,text,'alias,')
        self.assertTrue(hover and hover.get('contents'),hover)
        public=self.hover(client,main,text,'pmap([]')
        self.assertTrue(public and public.get('contents'),public)
        self.assertNotIn('_pmap_static',json.dumps(public))
        self.assertNotIn('__thor_',json.dumps(public))
