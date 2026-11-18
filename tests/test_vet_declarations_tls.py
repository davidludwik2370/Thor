"""Unused declaration warnings and import-only corrections on unsaved buffers."""
import unittest
from tests import test_vet_tls as base


class VetDeclarationTLSTests(unittest.TestCase):
    setUpClass = base.VetTLSTests.__dict__['setUpClass']
    open = base.VetTLSTests.open
    position = staticmethod(base.VetTLSTests.position)
    actions = base.VetTLSTests.actions

    def test_unused_import_warning_and_fix_and_type_warning_recover_on_source_uses(self):
        text = '// 😀\npackage main\nimport "core:fmt"\nUnused::struct{x:int}\nmain::proc(){}\n'
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/unused-import')
        warnings = {error['code']: error for error in errors}
        self.assertTrue({'thor/unused-import','thor/unused-type'} <= warnings.keys(), errors)
        for code, word in [('thor/unused-import','import "'),('thor/unused-type','Unused::')]:
            self.assertEqual(warnings[code]['severity'], 2)
            self.assertEqual(warnings[code]['tags'], [1])
            self.assertEqual(warnings[code]['range']['start'], self.position(text,word,0))
        actions = self.actions(client,path,text,'import "')
        action = next(action for action in actions if action['title']=='Remove unused import')
        edit = action['edit']['changes'][path.as_uri()][0]
        self.assertEqual(edit['newText'], '')
        self.assertEqual(edit['range'], warnings['thor/unused-import']['range'])
        changed = text.replace('main::proc(){}','main::proc(){fmt.println(Unused{1})}')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
            'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(), text)

    def test_unused_type_is_warning_only_and_has_no_removal_action(self):
        text = 'package main\nUnused::struct{x:int}\nmain::proc(){}\n'
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/unused-type')
        self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])
        self.assertEqual(self.actions(client,path,text,'Unused::'), [])

    def test_native_type_alias_warning_keeps_the_import_and_original_range(self):
        text = 'package main\nimport "core:time"\nUnused::time.Duration\nmain::proc(){}\n'
        client, path = self.open(text, vet=True)
        errors = client.diagnostic(path.as_uri(), code='thor/unused-type')
        self.assertEqual([error['code'] for error in errors], ['thor/unused-type'])
        self.assertEqual(errors[0]['range']['start'], self.position(text,'Unused::',0))
        self.assertEqual(self.actions(client,path,text,'Unused::'), [])


if __name__ == '__main__':
    unittest.main()
