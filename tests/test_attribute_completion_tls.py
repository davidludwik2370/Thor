"""Attribute suggestions through TLS's automatic and manual completion paths."""
import re
import unittest

from tests import test_tls as base


class AttributeCompletionTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)

    def complete(self, text, marker, *, trigger='@'):
        client, path = self.open(text, compiler=False)
        result = client.request('textDocument/completion', {
            'textDocument': {'uri': path.as_uri()},
            'position': self.position(text, marker, len(marker)),
            'context': {'triggerKind': 2, 'triggerCharacter': trigger} if trigger else {'triggerKind': 1},
        })
        return client, result

    def test_typing_at_sign_offers_documented_native_and_thor_attributes(self):
        client, result = self.complete('package main\n@\nmain :: proc() {}\n', '\n@')
        self.assertIn('@', client.capabilities['completionProvider']['triggerCharacters'])
        items = {item['label']: item for item in result['items']}
        self.assertTrue({'test', 'private', 'static', 'deprecated', 'require_results',
                         'link_name', 'unused', 'vet_ignore', 'no_alloc'} <= items.keys(), result)
        reference = (base.ROOT / 'docs/attributes-and-directives.md').read_text()
        supported = reference.split('## Obsolete, removed, and reserved forms', 1)[0]
        self.assertEqual(set(items), set(re.findall(r'id="attr-([\w]+)"', supported)))
        self.assertNotIn('main', items)
        for item in items.values():
            self.assertTrue(item.get('documentation'), item)
            self.assertFalse(item.get('insertText', item['label']).startswith('@'), item)

    def test_bare_and_parenthesized_attributes_offer_the_same_names(self):
        labels = []
        for marker in ('@', '@(', '@no', '@(no'):
            with self.subTest(marker=marker):
                _, result = self.complete('package main\n'+marker+'\nmain :: proc() {}\n',
                                          '\n'+marker, trigger=None)
                names = {item['label'] for item in result['items']}
                self.assertIn('no_alloc', names)
                labels.append(names)
        for names in labels[1:]:
            self.assertEqual(names, labels[0])

    def test_at_sign_inside_comments_and_strings_does_not_offer_attributes(self):
        for code in ('// @', '/* @ */', '/* @', 'TEXT :: "@"', 'TEXT :: `@`',
                     'TEXT :: "@', 'TEXT :: `@', 'TEXT :: f"@"',
                     'TEXT :: f"{len("text")} @"'):
            with self.subTest(code=code):
                _, result = self.complete('package main\n'+code+'\nmain :: proc() {}\n', '@')
                self.assertNotIn('no_alloc', {item['label'] for item in result['items']}, result)

    def test_unsaved_at_sign_at_end_of_file_completes_immediately(self):
        text = 'package main\n'
        client, path = self.open(text, compiler=False)
        changed = text + '@'
        client.send('textDocument/didChange', {
            'textDocument': {'uri': path.as_uri(), 'version': 2},
            'contentChanges': [{'text': changed}],
        })
        result = client.request('textDocument/completion', {
            'textDocument': {'uri': path.as_uri()},
            'position': self.position(changed, '@', 1),
            'context': {'triggerKind': 2, 'triggerCharacter': '@'},
        })
        self.assertIn('no_alloc', {item['label'] for item in result['items']}, result)
        self.assertEqual(path.read_text(), text)


if __name__ == '__main__':
    unittest.main()
