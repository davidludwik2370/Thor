"""Pure callback types use original-source LSP signatures and diagnostics."""
import json
import unittest
from tests import test_tls as base

class PureCallbackTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)
    semantic_spans=base.TLSTests.semantic_spans

    def test_types_aliases_generics_and_result_completion(self):
        text='''package main
Record::struct{value:int}
Callback::fn(x:int)->Record
Alias::Callback
make_record::fn(x:int)->Record{return {x}}
apply::fn(x:$T, callback:fn(T)->Record)->Record{return callback(x)}
main::proc(){callback:Alias=make_record;result:=apply(7,callback);_=result.value}
'''
        for compiler in [False,True]:
            with self.subTest(compiler=compiler):
                client,main=self.open(text,compiler=compiler)
                if compiler:client.diagnostic(main.as_uri(),empty=True)
                for needle in ['Callback::','Alias::','callback:Alias','callback(x)']:
                    value=json.dumps(self.hover(client,main,text,needle))
                    self.assertTrue('fn' in value or 'pure function' in value,value)
                    self.assertNotIn('__thor_',value)
                value=json.dumps(self.hover(client,main,text,'result:='))
                self.assertIn('Record',value)
                completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'result.value',7)})
                items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
                self.assertIn('value',[item['label'] for item in items])

    def test_impure_assignment_diagnostic_and_unsaved_recovery(self):
        text='''// 😀 original locations
package main
Callback::fn(int)->int
square::fn(x:int)->int{return x*x}
ordinary::proc(x:int)->int{return x}
main::proc(){callback:Callback=ordinary;_=callback(3)}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='callback contract')
        error=next(error for error in errors if 'callback contract' in error['message'])
        self.assertEqual(error['range']['start'],self.position(text,'ordinary;',0))
        fixed=text.replace('=ordinary;','=square;')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('pure function',json.dumps(self.hover(client,main,fixed,'callback:Callback')))

    def test_constant_generic_callback_signature_hides_native_witnesses(self):
        text='''package main
Callback::fn(int)->int
square::fn(x:$T)->T{return x*x}
work::fn($callback:Callback)->int{return #run callback(7)}
ANSWER::#run work(square)
main::proc(){_=ANSWER}
'''
        client,main=self.open(text,compiler=True)
        client.diagnostic(main.as_uri(),empty=True)
        value=json.dumps(self.hover(client,main,text,'work::'))
        self.assertIn('callback',value)
        self.assertIn('Callback',value)
        self.assertNotIn('__thor_',value)
        self.assertNotIn('callback_tag',value)

    def test_vet_trusts_pure_callback_global_effects_and_rechecks_unsaved_types(self):
        text='''package main
Callback::fn(int)->int
square::fn(x:int)->int{x*x}
apply::proc(value:int,callback:Callback)->int{return callback(value)}
main::proc(){_=apply(3,square)}
'''
        client,main=self.open(text,checker_args='--vet=lifetime,leak --unverified --comptime=required')
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('Callback::fn','Callback::proc')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
                                             'contentChanges':[{'text':changed}]})
        findings=client.diagnostic(main.as_uri(),code='thor/lifetime-unknown')
        finding=next(item for item in findings if item.get('code')=='thor/lifetime-unknown')
        self.assertIn('global reference state',finding['message'])
        self.assertEqual(finding['range']['start'],self.position(changed,'callback(value)',0))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},
                                             'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

if __name__=='__main__':unittest.main()
