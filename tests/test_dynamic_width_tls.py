"""Dynamic width expressions participate in original-source editor services."""
import json
import unittest
from tests import test_tls as base


class DynamicWidthTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_width_hover_definition_rename_and_unsaved_diagnostics(self):
        text='''// 😀
package main
main::proc(){
 width:>6
 value:=12.5
 text:=f"{value:{width}.2f}|{value:-{width+2}.1f}"
 delete(text)
}
'''
        client,main=self.open(text)
        self.assertIn('width: int',json.dumps(self.hover(client,main,text,'width}.2f')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        at=self.position(text,'width}.2f')
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':at})
        self.assertIn(main.as_uri(),json.dumps(definition))
        edit=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},'position':at,'newName':'padding'})
        self.assertEqual(len(edit['changes'][main.as_uri()]),3,edit)
        for version,changed,message in [
            (2,text.replace('width:>6','width:>"bad"'),'width must have an integer type'),
            (4,text.replace('width}.2f','missing}.2f'),'Undeclared name: missing'),
        ]:
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            errors=client.diagnostic(main.as_uri(),contains=message)
            needle='missing}' if 'missing' in message else 'width}.2f'
            error=next(e for e in errors if message in e['message'])
            self.assertEqual(error['range']['start'],self.position(changed,needle,0))
            self.assertNotIn('Thor expression',error['message'])
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version+1},'contentChanges':[{'text':text}]})
            client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)
