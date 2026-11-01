"""Typed binding editor behavior over the actual TLS stdio connection."""
import json
import unittest
from tests import test_tls as base


class TypedLoopTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_hover_readonly_tokens_alias_definition_and_index_rename(self):
        text='''package main
Counter::distinct i8
main::proc(){
    values:=[10,20]
    for value,(index:Counter) in values { _=value; _=int(index) }
    for number:f64,ordinal in 1..=3 { _=number; _=ordinal }
    for &cell,(offset:u8) in values { cell+=int(offset) }
}
'''
        client,main=self.open(text)
        for fragment,expected in [('value,','value: int'),('index:','index: Counter'),('number:','number: f64'),('ordinal in','ordinal: int'),('offset:','offset: u8')]:
            self.assertIn(expected,json.dumps(self.hover(client,main,text,fragment)))
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        readonly=1<<client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        for _,_,kind,name,modifiers in spans:
            if name in {'value','index','number','ordinal','offset','cell'}:
                self.assertEqual(kind,'variable',name)
                self.assertEqual(bool(modifiers&readonly),name!='cell',name)
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'Counter)')})
        self.assertIn('"line": 1',json.dumps(definition))
        position=self.position(text,'index:')
        prepared=client.request('textDocument/prepareRename',{'textDocument':{'uri':main.as_uri()},'position':position})
        self.assertIsNotNone(prepared)
        edit=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},'position':position,'newName':'ordinal'})
        self.assertEqual(len(edit['changes'][main.as_uri()]),2,edit)
        self.assertTrue(all(change['newText']=='ordinal' for change in edit['changes'][main.as_uri()]))

    def test_unsaved_annotation_errors_ranges_and_recovery(self):
        text='''// original 😀 header
package main
main::proc(){
    values:=[10,20]
    for value,(index:i8) in values { _=value; _=index }
    for number:f64 in 1..=3 { _=number }
}
'''
        client,main=self.open(text)
        self.assertIn('index: i8',json.dumps(self.hover(client,main,text,'index:')))
        changed=text.replace('index:i8','index:f64')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='indices require an integer type')
        self.assertEqual(len(errors),1,errors)
        start=self.position(changed,'f64)',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+3}})
        self.assertNotIn('Thor expression',errors[0]['message'])
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('index: i8',json.dumps(self.hover(client,main,text,'index:')))
