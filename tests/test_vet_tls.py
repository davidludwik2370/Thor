"""Opt-in vet diagnostics and safe fixes on live, unsaved TLS documents."""
import unittest
from tests import test_tls as base


class VetTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)

    def actions(self, client, path, text, word):
        position = self.position(text, word, 0)
        return client.request('textDocument/codeAction', {
            'textDocument': {'uri': path.as_uri()}, 'range': {'start': position, 'end': position},
            'context': {'diagnostics': []}}) or []

    def test_code_actions_before_compiler_diagnostics_keep_other_requests_working(self):
        text = 'package main\nmain::proc(){value:=1;_=value}\n'
        client,path = self.open(text,compiler=False)
        for _ in range(3):
            actions = self.actions(client,path,text,'value:=')
            self.assertEqual(actions, [])
            symbols = client.request('textDocument/documentSymbol',{'textDocument':{'uri':path.as_uri()}})
            self.assertTrue(any(item['name']=='main' for item in symbols),symbols)
            tokens = client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})
            self.assertTrue(tokens['data'],tokens)

    def test_code_actions_for_a_file_without_vet_findings(self):
        text = 'package main\nmain::proc(){value:>1;_=value}\n'
        client,path = self.open(text,vet=True,refresh=True)
        client.diagnostic(path.as_uri(),empty=True)
        while not any(request['method']=='workspace/inlayHint/refresh' for request in client.server_requests):
            client.next()
        actions = self.actions(client,path,text,'value:>')
        self.assertEqual(actions, [])
        changed = text.replace('value:>','value:=')
        client.send('textDocument/didChange', {'textDocument': {'uri':path.as_uri(),'version':2},
                                             'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),code='thor/prefer-immutable')
        actions = self.actions(client,path,changed,'value:=')
        self.assertTrue(any(item['title']=='Make declaration immutable' for item in actions))

    def test_warnings_codes_unicode_ranges_quick_fixes_and_unsaved_recovery(self):
        text = '// 😀 header\npackage main\nget::proc(x:int)->int{return x+1}\nmain::proc(){/* 😀 */value:=get(1);_=value}\n'
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/prefer-fn')
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(all(item['severity'] == 2 for item in errors))
        immutable = next(item for item in errors if item['code'] == 'thor/prefer-immutable')
        self.assertEqual(immutable['range']['start'], self.position(text, 'value:=', 0))
        for word, title, token, offset, replacement in [
            ('value:=','Make declaration immutable','value:=',len('value:'),'>'),
            ('get::','Declare as fn','proc(x',0,'fn'),
        ]:
            with self.subTest(title=title):
                actions = self.actions(client,path,text,word)
                action = next(item for item in actions if item['title'] == title)
                edits = action['edit']['changes'][path.as_uri()]
                self.assertEqual(len(edits), 1)
                self.assertEqual(edits[0]['newText'], replacement)
                self.assertEqual(edits[0]['range']['start'], self.position(text,token,offset))
        changed = text.replace('get::proc','get::fn').replace('value:=','value:>')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(),'version':2},
                                             'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(), empty=True)
        self.assertEqual(path.read_text(), text)

    def test_stale_warning_cannot_offer_a_fix_for_a_changed_document(self):
        text = 'package main\nmain::proc(){value:=1;_=value}\n'
        client,path = self.open(text,vet=True)
        client.diagnostic(path.as_uri(),code='thor/prefer-immutable')
        changed = text.replace('_=value','value=2;_=value')
        client.send('textDocument/didChange', {'textDocument': {'uri':path.as_uri(),'version':2},
                                             'contentChanges':[{'text':changed}]})
        actions = self.actions(client,path,changed,'value:=')
        self.assertFalse(any(item['title']=='Make declaration immutable' for item in actions),actions)
        client.diagnostic(path.as_uri(),empty=True)


if __name__ == '__main__':
    unittest.main()
