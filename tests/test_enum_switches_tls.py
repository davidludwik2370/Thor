"""Original-source editor behavior for partial and exhaustive enum expressions."""
import json
import unittest
from tests import test_tls as base


class EnumSwitchTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_partial_and_exhaustive_hover_tokens_definition_and_diagnostic_recovery(self):
        text='''// 😀 original header
package main
Outcome::enum{inGame,blackWon,whiteWon,draw}
main::proc(){
    outcome:Outcome=.blackWon
    complete:=switch outcome{
        case .blackWon:"BLACK WINS"
        case .whiteWon:"WHITE WINS"
        case .draw:"DRAW"
        case .inGame:panic("unreachable")
    }
    partial:=(#partial switch outcome{case .blackWon:"BLACK WINS";case:panic("unreachable")})
    _=complete;_=partial
}
'''
        client,main=self.open(text)
        for binding in ['complete:=','partial:=']:
            self.assertIn('string',json.dumps(self.hover(client,main,text,binding)))
        at=self.position(text,'.blackWon:"BLACK WINS"',2)
        self.assertIn('Outcome',json.dumps(client.request('textDocument/hover',{
            'textDocument':{'uri':main.as_uri()},'position':at})))
        target=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':at})
        self.assertIn(main.as_uri(),json.dumps(target))
        at=self.position(text,'outcome{case .blackWon',len('outcome{case .')+1)
        self.assertIn('Outcome',json.dumps(client.request('textDocument/hover',{
            'textDocument':{'uri':main.as_uri()},'position':at})))
        target=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':at})
        self.assertIn(main.as_uri(),json.dumps(target))
        edits=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'partial:='),'newName':'partial_message'})
        self.assertEqual(len(edits['changes'][main.as_uri()]),2,edits)
        self.assertTrue(all(text.splitlines()[edit['range']['start']['line']][edit['range']['start']['character']:
            edit['range']['end']['character']]=='partial' for edit in edits['changes'][main.as_uri()]))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace('case .inGame:panic("unreachable")','')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Unhandled')
        error=next(e for e in errors if 'Unhandled' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'switch outcome',0))
        self.assertIn('inGame',error['message'])
        self.assertNotIn('Thor expression',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_partial_multi_result_hover_and_missing_default_error(self):
        text='''package main
Kind::enum{A,B}
main::proc(){
    kind:Kind=.A
    value,text:=#partial switch kind{case .A:7,"ok";case:panic("bad")}
    _=value;_=text
}
'''
        client,main=self.open(text)
        self.assertIn('int',json.dumps(self.hover(client,main,text,'value,text:=')))
        self.assertIn('string',json.dumps(self.hover(client,main,text,'text:=')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace(';case:panic("bad")','')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='#partial switch expression requires a default')
        error=next(e for e in errors if '#partial switch expression requires a default' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'#partial switch',0))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
