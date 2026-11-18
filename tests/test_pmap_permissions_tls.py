"""Purity corrections retain inferred results and original-source diagnostics."""
import json
import unittest
from tests import test_tls as base


class ParallelMapPermissionTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)

    def test_generic_fresh_slice_hover_and_unsaved_borrowed_write_recovery(self):
        text='''package main
import "thor:basic"
allocate::proc(x:$T)->[]T{values:=make([]T,2);values[0],values[1]=x,x*2;return values}
compute::fn(n:i16)->[]i16{rows:=basic.pmap([]i16{n},allocate);return rows[0]}
main::proc(){n:i16=3;value:=compute(n);_=value[0];delete(value)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        info=self.hover(client,main,text,'value:=')
        self.assertIn('[]i16',json.dumps(info))
        broken=text.replace('allocate::proc(x:$T)->[]T{values:=make([]T,2);values[0],values[1]=x,x*2;return values}',
                            'allocate::proc(x:$T)->[]T{values:=make([]T,2);x[0]=9;return values}').replace('n:i16)->[]i16{rows:=basic.pmap([]i16{n}',
                            'n:[]int)->[][]int{rows:=basic.pmap([][]int{n}').replace('n:i16=3','n:=[]int{3}')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='borrowed')
        error=next(error for error in errors if 'borrowed' in error['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'basic.pmap',0))
        self.assertNotIn('__thor_',error['message'])
        self.assertTrue(error.get('relatedInformation'),error)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[]i16',json.dumps(self.hover(client,main,text,'value:=')))

    def test_inline_generic_callback_keeps_nested_result_fields(self):
        text='''package main
import "thor:basic"
compute::fn(x:i16)->int{
    rows:=basic.pmap([]i16{x},(proc(v:$T)->(values:[]T,ok:bool){data:=make([]T,1);data[0]=v;return data,true}))
    _=rows[0].values
    return int(rows[0].values[0])
}
main::proc(){n:i16=3;_=compute(n)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        value=self.hover(client,main,text,'values\n')
        self.assertIn('[]i16',json.dumps(value))
        result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rows[0].values',len('rows[0].')),'context':{'triggerKind':1}})
        self.assertTrue({'values','ok'}<={item['label'] for item in result['items']},result)
