"""Postfix unwraps through TLS, including unsaved edits and evaluation errors."""
import json
import unittest
from tests import test_tls as base


class OrPanicTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)
    hover = base.TLSTests.hover
    semantic_spans = base.TLSTests.semantic_spans
    unused_imports = base.TLSTests.unused_imports
    assert_original_identifier_tokens = base.TLSTests.assert_original_identifier_tokens

    def test_migrated_member_hover_and_postfix_keyword_are_distinct(self):
        text = '''package main
or_panic :: distinct i16
Item :: struct { or_panic: int }
get :: proc() -> (Item, bool) { return Item{or_panic=7}, true }
main :: proc() {
    item := get() or_panic
    _ = item.or_panic
    _ = typeid_of(or_panic)
}
'''
        client, main = self.open(text)
        for token, offset, expected in [('item.or_panic', 6, 'int'),
                                        ('typeid_of(or_panic)', 12, 'i16'),
                                        ('get() or_panic', 7, 'Unwraps an optional result')]:
            with self.subTest(token=token):
                result = client.request('textDocument/hover', {
                    'textDocument': {'uri': main.as_uri()}, 'position': self.position(text, token, offset)})
                self.assertIsNotNone(result)
                self.assertIn(expected, result['contents']['value'])

    def test_inferred_types_immutability_and_keyword_hover(self):
        text = '''package main
get::proc()->(int,bool){return 7,true}
pair::proc()->(int,string,bool){return 3,"hello",true}
main::proc(){
 value:>get() or_panic
 a,b:=pair() or_panic
 _=value;_=a;_=b
}
'''
        client, main = self.open(text)
        for word, typ in [('_=value', 'int'), ('_=a', 'int'), ('_=b', 'string')]:
            value = client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()},
                                                        'position': self.position(text, word, 2)})
            self.assertIsNotNone(value)
            self.assertIn(': '+typ, value['contents']['value'])
        hover = self.hover(client, main, text, 'or_panic')
        self.assertIn('Unwraps an optional result', hover['contents']['value'])
        spans = self.semantic_spans(client, main, text)
        self.assert_original_identifier_tokens(main, text, spans)
        legend = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        immutable = 1 << legend.index('readonly')
        values = [(kind, mods) for _, _, kind, name, mods in spans if name == 'value']
        self.assertTrue(values)
        self.assertTrue(all(kind == 'variable' and mods & immutable for kind, mods in values), values)

    def test_operand_rename_references_and_selected_import_usage(self):
        text = 'package main\nimport "library" / get, unused\nmain::proc(){value:=get() or_panic;_=value}\n'
        client, main = self.open(text, files={'library/main.thor':
            'package library\nget::proc()->(int,bool){return 7,true}\nunused::proc(){}\n'}, unused_imports=True)
        errors = self.unused_imports(client, main)
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['range']['start'], self.position(text, 'unused\n', 0))
        params = {'textDocument': {'uri': main.as_uri()}, 'position': self.position(text, 'value:=', 1)}
        self.assertIsNotNone(client.request('textDocument/prepareRename', params))
        edits = client.request('textDocument/rename', {**params, 'newName': 'answer'})
        self.assertEqual(len(edits['changes'][main.as_uri()]), 2, edits)

    def test_inner_error_is_precise_and_unsaved_recovery_clears_it(self):
        text = '// 😀 source spans\npackage main\nmain::proc(){value:=missing() or_panic;_=value}\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), contains='Undeclared name: missing')
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['range']['start'], self.position(text, 'missing()', 0))
        self.assertNotIn('Thor expression', json.dumps(errors))
        changed = text.replace('main::proc()', 'missing::proc()->(int,bool){return 7,true}\nmain::proc()')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                                             'contentChanges': [{'text': changed}]})
        client.diagnostic(main.as_uri(), empty=True)
        self.assertIn('value: int', json.dumps(self.hover(client, main, changed, 'value}')))

    def test_compile_time_failure_retains_details_and_original_related_location(self):
        text = '''package main
get::fn()->(int,bool){return 0,false}
choose::fn()->int{return get() or_panic}
VALUE::#run choose()
main::proc(){_=VALUE}
'''
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), contains='unexpected failure')
        error = next(e for e in errors if 'unexpected failure' in e['message'])
        self.assertIn('expression: get()', error['message'])
        self.assertIn('error: false', error['message'])
        related = error.get('relatedInformation', [])
        self.assertTrue(related, error)
        self.assertTrue(any(r['location']['uri'] == main.as_uri() and
                            r['location']['range']['start'] == self.position(text, 'get() or_panic', 0)
                            for r in related), error)

    def test_keyword_completion_and_incomplete_operand_recovery(self):
        text = 'package main\nget::proc()->(int,bool){return 7,true}\nmain::proc(){_=get() or_pa}\n'
        client, main = self.open(text, compiler=False)
        completion = client.request('textDocument/completion', {'textDocument': {'uri': main.as_uri()},
            'position': self.position(text, 'or_pa}', 5), 'context': {'triggerKind': 1}})
        self.assertIn('or_panic', json.dumps(completion))
        changed = text.replace('_=get() or_pa', 'value:=get() or_panic;_=value')
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                                             'contentChanges': [{'text': changed}]})
        self.assertIn('value: int', json.dumps(self.hover(client, main, changed, 'value}')))


if __name__ == '__main__': unittest.main()
