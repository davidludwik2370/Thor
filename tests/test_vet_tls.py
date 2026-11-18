"""Opt-in vet diagnostics and safe fixes on live, unsaved TLS documents."""
import json
import subprocess
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

    def test_strict_build_keeps_editor_warnings_and_quick_fixes_at_the_same_source_range(self):
        text = ('// 😀 header\npackage main\nimport "core:fmt"\n'
                'main::proc(){/* 😀 */value:=7;fmt.println(value)}\n')
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/prefer-immutable')
        warning = next(error for error in errors if error['code']=='thor/prefer-immutable')
        self.assertEqual(warning['severity'], 2)
        result = subprocess.run([str(base.THOR), 'build', str(path), '--vet=immutable', '-json-errors',
                                 '-out:'+str(path.parent/'application')],
                                cwd=path.parent, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        packet = json.loads(result.stderr)
        self.assertEqual(packet['error_count'], 1, packet)
        error = packet['errors'][0]
        self.assertEqual(error['type'], 'error')
        self.assertEqual(error['code'], warning['code'])
        self.assertEqual(error['pos']['file'], str(path))
        self.assertEqual(warning['range']['start'], {
            'line':error['pos']['line']-1, 'character':error['pos']['column']-1})
        actions = self.actions(client, path, text, 'value:=')
        self.assertTrue(any(action['title']=='Make declaration immutable' for action in actions), actions)
        self.assertEqual(path.read_text(), text)

    def test_unnecessary_semicolon_warning_and_quick_fix_follow_unsaved_changes(self):
        text = ('package main\nmain::proc(){\n'
                '    /* 😀 */ value:=1;\n    _=value\n}\n')
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(),code='thor/unnecessary-semicolon')
        warning = next(error for error in errors if error['code']=='thor/unnecessary-semicolon')
        self.assertEqual(warning['severity'],2)
        self.assertEqual(warning['range']['start'],self.position(text,'1;',1))
        self.assertEqual(warning['tags'],[1])
        actions = self.actions(client,path,text,';')
        action = next(action for action in actions if action['title']=='Remove unnecessary semicolon')
        edit = action['edit']['changes'][path.as_uri()][0]
        self.assertEqual(edit['newText'],'')
        self.assertEqual(edit['range'],warning['range'])
        changed = text.replace('value:=1;','value:=1; _=0')
        client.notifications.clear()
        client.send('textDocument/didChange', {
            'textDocument':{'uri':path.as_uri(),'version':2}, 'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(self.actions(client,path,changed,';'),[])
        self.assertEqual(path.read_text(),text)

    def test_printed_slice_immutable_advice_and_fix_follow_unsaved_mutation(self):
        text='package main\nimport "core:fmt"\nmain::proc(){values:=[]int{1};fmt.println(values)}\n'
        client,path=self.open(text,vet=True)
        errors=client.diagnostic(path.as_uri(),code='thor/prefer-immutable')
        self.assertEqual([error['code'] for error in errors],['thor/prefer-immutable'])
        self.assertEqual(errors[0]['range']['start'],self.position(text,'values:=',0))
        actions=self.actions(client,path,text,'values:=')
        action=next(action for action in actions if action['title']=='Make declaration immutable')
        edits=action['edit']['changes'][path.as_uri()]
        self.assertEqual(len(edits),1)
        self.assertEqual(edits[0]['newText'],'>')
        changed=text.replace('fmt.println(values)','values[0]=2;fmt.println(values)')
        client.notifications.clear()
        client.send('textDocument/didChange',{
            'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        actions=self.actions(client,path,changed,'values:=')
        self.assertEqual(actions,[])
        self.assertEqual(path.read_text(),text)

    def test_unused_bindings_have_warning_ranges_and_no_automatic_removal(self):
        text = ('// 😀 header\npackage main\n'
                'helper::fn(parameter:int)->int{return 1}\n'
                'main::proc(){/* 😀 */value:>helper(2);_=0}\n')
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/unused')
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(all(error['severity'] == 2 and error['tags'] == [1] for error in errors))
        for word in ('parameter:int', 'value:>'):
            position = self.position(text, word, 0)
            matching = [error for error in errors if error['range']['start'] == position]
            self.assertEqual(len(matching), 1, errors)
            self.assertEqual(self.actions(client, path, text, word), [])
        changed = text.replace('return 1', 'return parameter').replace('_=0', '_=value')
        client.notifications.clear()
        client.send('textDocument/didChange', {
            'textDocument': {'uri': path.as_uri(), 'version': 2},
            'contentChanges': [{'text': changed}]})
        client.diagnostic(path.as_uri(), empty=True)
        self.assertEqual(path.read_text(), text)

    def test_unused_warning_replaces_immutable_advice_and_quick_fixes(self):
        text = ('package main\nimport "core:fmt"\n'
                'main::proc(){/* 😀 */x:=(1+2)*5;fmt.println("ready")}\n')
        client, path = self.open(text, vet=True)
        for version, changed, code in [
            (1, text, 'thor/unused'),
            (2, text.replace('fmt.println("ready")', 'fmt.println(x)'), 'thor/prefer-immutable'),
            (3, text, 'thor/unused'),
        ]:
            if version > 1:
                client.notifications.clear()
                client.send('textDocument/didChange', {
                    'textDocument': {'uri': path.as_uri(), 'version': version},
                    'contentChanges': [{'text': changed}]})
            errors = client.diagnostic(path.as_uri(), code=code)
            self.assertEqual([error['code'] for error in errors], [code])
            actions = self.actions(client, path, changed, 'x:=')
            if code == 'thor/unused':
                self.assertEqual(actions, [])
            else:
                self.assertTrue(any(action['title'] == 'Make declaration immutable' for action in actions))
        self.assertEqual(path.read_text(), text)

    def test_write_only_locals_and_unused_parameters_recover_on_explicit_discards(self):
        text = ('package main\n'
                'helper::fn(parameter:int)->int{return 1}\n'
                'main::proc(){/* 😀 */value:=helper(2);value=3;value+=1}\n')
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/unused')
        self.assertEqual([error['code'] for error in errors], ['thor/unused']*2)
        for word in ('parameter:int', 'value:='):
            matching = [error for error in errors
                        if error['range']['start'] == self.position(text, word, 0)]
            self.assertEqual(len(matching), 1, errors)
            self.assertEqual((matching[0]['severity'], matching[0]['tags']), (2, [1]))
            self.assertEqual(self.actions(client, path, text, word), [])
        changed = text.replace('return 1', '_=parameter;return 1').replace(
            'value+=1', 'value+=1;_=value')
        client.notifications.clear()
        client.send('textDocument/didChange', {
            'textDocument': {'uri': path.as_uri(), 'version': 2},
            'contentChanges': [{'text': changed}]})
        client.diagnostic(path.as_uri(), empty=True)
        self.assertEqual(path.read_text(), text)

    def test_discard_only_suppression_and_other_use_immutable_quick_fixes(self):
        text = ('package main\n'
                'main::proc(){/* 😀 */value:=(1+2)*5;_=value}\n')
        client, path = self.open(text, vet=True)
        client.diagnostic(path.as_uri(), empty=True)
        self.assertEqual(self.actions(client, path, text, 'value:='), [])
        imported = text.replace('package main\n', 'package main\nimport "core:fmt"\n')
        used = imported.replace('_=value', '_=value;fmt.println(value)')
        moved = imported.replace('_=value', 'fmt.println(value);_=((value))')
        for version, changed in ((2, used), (3, moved), (4, text)):
            client.notifications.clear()
            client.send('textDocument/didChange', {
                'textDocument': {'uri': path.as_uri(), 'version': version},
                'contentChanges': [{'text': changed}]})
            if version in (2, 3):
                snapshot = 14695981039346656037
                for byte in changed.encode():
                    snapshot = ((snapshot ^ byte)*1099511628211) & ((1 << 64)-1)
                errors = client.diagnostic(path.as_uri(), code='thor/prefer-immutable',
                                           snapshot=f'{snapshot:016x}')
                self.assertEqual([error['code'] for error in errors], ['thor/prefer-immutable'])
                self.assertEqual(errors[0]['range']['start'], self.position(used, 'value:=', 0))
                actions = self.actions(client, path, changed, 'value:=')
                self.assertTrue(any(action['title'] == 'Make declaration immutable' for action in actions))
            else:
                client.diagnostic(path.as_uri(), empty=True)
                self.assertEqual(self.actions(client, path, changed, 'value:='), [])
        self.assertEqual(path.read_text(), text)

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
        text = 'package main\nmain::proc(){value:>1;if value!=1{panic("result")}}\n'
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
        text = '// 😀 header\npackage main\nget::proc(x:int)->int{return x+1}\nmain::proc(){/* 😀 */value:=get(1);if value!=2{panic("result")}}\n'
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
        text = 'package main\nmain::proc(){value:=1;if value!=1{panic("result")}}\n'
        client,path = self.open(text,vet=True)
        client.diagnostic(path.as_uri(),code='thor/prefer-immutable')
        changed = text.replace('if value!=1','value=2;if value!=2')
        client.send('textDocument/didChange', {'textDocument': {'uri':path.as_uri(),'version':2},
                                             'contentChanges':[{'text':changed}]})
        actions = self.actions(client,path,changed,'value:=')
        self.assertFalse(any(item['title']=='Make declaration immutable' for item in actions),actions)
        client.diagnostic(path.as_uri(),empty=True)

    def test_ineligible_value_shapes_leave_valid_pure_quick_fixes_available(self):
        text='''package main
import "core:fmt"
pointer::proc(value:^int)->int{return value^}
mapping::proc()->map[int]int{return nil}
prints::proc(){fmt.println(1)}
reader::proc(value:[]int)->int{return value[0]}
main::proc(){}
'''
        client,path=self.open(text,checker_args='--vet=pure')
        errors=client.diagnostic(path.as_uri(),code='thor/prefer-fn')
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(text,'reader::',0))
        actions=self.actions(client,path,text,'reader::')
        action=next(item for item in actions if item['title']=='Declare as fn')
        edit=action['edit']['changes'][path.as_uri()][0]
        self.assertEqual(edit['range']['start'],self.position(text,'proc(value:[]int)',0))
        self.assertEqual(edit['newText'],'fn')
        symbols=client.request('textDocument/documentSymbol',{'textDocument':{'uri':path.as_uri()}})
        self.assertTrue(any(item['name']=='reader' for item in symbols),symbols)


if __name__ == '__main__':
    unittest.main()
