"""Branch locations retain native types and original unsaved diagnostics."""
import json
import unittest
from tests import test_tls as support
from tests import test_rust_compiler as native


class BranchLocationTLSTests(unittest.TestCase):
    open = support.TLSTests.open
    hover = support.TLSTests.hover
    position = staticmethod(support.TLSTests.position)

    def test_location_fields_completion_and_unsaved_placement_recovery(self):
        text = '''// 😀 original source
package main
main::proc(){
 defer {
  loc := #branch_location
  _ = loc.line
 }
}
'''
        client, main = self.open(text, compiler_command=native.RUST)
        client.diagnostic(main.as_uri(), empty=True)
        hover = self.hover(client, main, text, 'loc.line')
        self.assertIsNotNone(hover)
        self.assertIn('Source_Code_Location', json.dumps(hover))
        field = client.request('textDocument/hover', {'textDocument': {'uri':main.as_uri()},
            'position': self.position(text, 'loc.line', 5)})
        self.assertIn('i32', json.dumps(field))
        partial = text.replace('_ = loc.line', '_ = loc.')
        client.send('textDocument/didChange', {'textDocument': {'uri':main.as_uri(),'version':2},
            'contentChanges': [{'text':partial}]})
        completion = client.request('textDocument/completion', {'textDocument': {'uri':main.as_uri()},
            'position': self.position(partial, '_ = loc.', len('_ = loc.')),
            'context': {'triggerKind':2,'triggerCharacter':'.'}})
        self.assertTrue({'file_path','line','column','procedure'} <= {item['label'] for item in completion['items']}, completion)
        invalid = text.replace('defer {', '{')
        client.send('textDocument/didChange', {'textDocument': {'uri':main.as_uri(),'version':3},
            'contentChanges': [{'text':invalid}]})
        errors = client.diagnostic(main.as_uri(), contains='#branch_location may only be used')
        error = next(error for error in errors if '#branch_location may only be used' in error['message'])
        self.assertEqual(error['range']['start'], self.position(invalid, '#branch_location', 0))
        self.assertNotIn('__thor_', json.dumps(errors))
        client.send('textDocument/didChange', {'textDocument': {'uri':main.as_uri(),'version':4},
            'contentChanges': [{'text':text}]})
        client.diagnostic(main.as_uri(), empty=True)
        self.assertEqual(main.read_text(), text)
