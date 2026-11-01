"""Shared parser, semantic ranges and original diagnostics for f! strings."""
import json
import unittest
from tests import test_tls as tls


class FStringTLSTests(unittest.TestCase):
    setUpClass=classmethod(tls.TLSTests.setUpClass.__func__)
    open=tls.TLSTests.open
    position=staticmethod(tls.TLSTests.position)
    hover=tls.TLSTests.hover
    semantic_spans=tls.TLSTests.semantic_spans
    assert_original_identifier_tokens=tls.TLSTests.assert_original_identifier_tokens

    def test_manual_and_automatic_literals_share_string_hovers_and_original_rename(self):
        text='''package main
consume::proc(text:string){}
main::proc(){
 named:=f!"hello {7}"
 consume(f!"manual {named}")
 consume(f`temporary {named}`)
 delete(named)
}
'''
        client,main=self.open(text)
        self.assertIn('named: string',json.dumps(self.hover(client,main,text,'named:=')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        rename=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'named:='),'newName':'message'})
        self.assertEqual(len(rename['changes'][main.as_uri()]),4)
        broken=text.replace('hello {7}','hello {missing}')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Undeclared')
        error=next(e for e in errors if 'missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'missing',0))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_nested_manual_raw_literals_and_unsaved_prefix_changes(self):
        text='''package main
consume::proc(text:string){}
main::proc(){
 value:=f!`outer {f"inner {3}"}`
 consume(f"value {value}")
 delete(value)
}
'''
        client,main=self.open(text)
        self.assertIn('value: string',json.dumps(self.hover(client,main,text,'value:=')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace('consume(f"value {value}")','consume(f!"value {missing}")')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Undeclared')
        error=next(e for e in errors if 'missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'missing',0))
        fixed=broken.replace('{missing}','{value}')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
