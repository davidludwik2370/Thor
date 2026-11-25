"""Rule-set completion, including unfinished dot entries and source recovery."""
import unittest
from tests import test_tls as base
from tests import test_vet_tls as vet_base

OPTIONS={'immutable','pure','unused','semicolon','lifetime_unknown','leak_unknown'}


class VetIgnoreSetTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    actions=vet_base.VetTLSTests.actions

    def complete(self,marked,*,trigger='.',compiler=False):
        cursor=marked.index('|');text=marked.replace('|','',1)
        client,path=self.open(text,compiler=compiler)
        position=self.position(text[:cursor]+'CURSOR'+text[cursor:],'CURSOR',0)
        context={'triggerKind':2,'triggerCharacter':trigger} if trigger else {'triggerKind':1}
        result=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':position,'context':context})
        return client,path,text,result

    def test_dot_and_empty_set_show_all_supported_rules(self):
        for marker in ['.|','|',' .| ','\n .|\n']:
            with self.subTest(marker=marker):
                client,path,text,result=self.complete('package main\nmain::proc(){@(vet_ignore={'+marker+'}) value:=7;_=value}',trigger='.' if '.' in marker else None)
                self.assertEqual({i['label'] for i in result['items']},OPTIONS,result)
                self.assertTrue(result['isIncomplete'])
                self.assertIn('.',client.capabilities['completionProvider']['triggerCharacters'])
                for item in result['items']:
                    self.assertTrue(item['documentation'])
                    expected=item['label'] if '.' in marker else '.'+item['label']
                    self.assertEqual(item['textEdit']['newText'],expected)
                    self.assertNotIn('additionalTextEdits',item)
                self.assertEqual(path.read_text(),text)

    def test_filters_prefix_and_replaces_the_whole_name_without_duplicating_dot(self):
        marked='// 😀\r\npackage main\r\nmain::proc(){@(vet_ignore={.li|fetime_unknown}) value:=7;_=value}\r\n'
        _,_,text,result=self.complete(marked,trigger=None)
        self.assertEqual([i['label'] for i in result['items']],['lifetime_unknown'],result)
        edit=result['items'][0]['textEdit']
        self.assertEqual(edit['newText'],'lifetime_unknown')
        self.assertEqual(edit['range']['start'],self.position(text,'lifetime_unknown',0))
        self.assertEqual(edit['range']['end'],self.position(text,'lifetime_unknown',len('lifetime_unknown')))

    def test_existing_rules_aliases_comments_and_fields_on_both_sides(self):
        for attribute,expected in [
            ('@(vet_ignore={.immutable, .|})',OPTIONS-{'immutable'}),
            ('@(vet_ignore={.immutable, .|, .unused})',OPTIONS-{'immutable','unused'}),
            ('@(vet_ignore={.fn, .|, .leak_unknown})',OPTIONS-{'pure','leak_unknown'}),
            ('@(vet_ignore=({.unused, .|}))',OPTIONS-{'unused'}),
            ('@(vet_ignore=(({.li|fetime_unknown})))',{'lifetime_unknown'}),
            ('@(deprecated="example",vet_ignore={/* .unused } */ .|, .pure})',OPTIONS-{'pure'}),
            ('@(vet_ignore={.unused,\n // .leak_unknown }\n .|,\n},deprecated="example")',OPTIONS-{'unused'}),
        ]:
            with self.subTest(attribute=attribute):
                _,_,_,result=self.complete('package main\n'+attribute+'\nVALUE::7\nmain::proc(){}\n')
                self.assertEqual({i['label'] for i in result['items']},expected,result)

    def test_completion_works_during_parser_recovery_with_compiler_enabled(self):
        client,path,text,result=self.complete('package main\nmain::proc(){@(vet_ignore={.|}) value:=7;_=value}',compiler=True)
        self.assertEqual({i['label'] for i in result['items']},OPTIONS,result)
        symbols=client.request('textDocument/documentSymbol',{'textDocument':{'uri':path.as_uri()}})
        self.assertTrue(any(i['name']=='main' for i in symbols),symbols)
        changed=text.replace('{.}','{.unused}')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_unsaved_set_suppression_typos_and_recovery_have_original_ranges(self):
        text='// 😀\npackage main\nmain::proc(){values:=make([]int,2);_=values}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),code='thor/leak-unknown')
        ignored=text.replace('values:=','@(vet_ignore={.lifetime_unknown, .leak_unknown}) values:=')
        for version,changed,expected in [
            (2,ignored,None),
            (3,ignored.replace('.leak_unknown','.typo'),'Unknown vet check'),
            (4,ignored,None),
            (5,ignored.replace('_=values','delete(values);_=values[0]'),'Definite lifetime'),
        ]:
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            if expected is None:client.diagnostic(path.as_uri(),empty=True)
            else:
                warnings=client.diagnostic(path.as_uri(),contains=expected)
                if '.typo' in changed:
                    error=next(w for w in warnings if expected in w['message'])
                    self.assertEqual(error['range']['start'],self.position(changed,'.typo',0))
                    self.assertEqual(error['range']['end'],self.position(changed,'.typo',len('.typo')))
                actions=self.actions(client,path,changed,'values[0]' if version==5 else '.typo')
                self.assertEqual(actions,[])
        self.assertEqual(path.read_text(),text)

    def test_does_not_complete_comments_strings_other_attributes_or_expressions(self):
        for marked in [
            'package main\n// @(vet_ignore={.|})\nmain::proc(){}',
            'package main\nmain::proc(){text:=`@(vet_ignore={.|})`;_=text}',
            'package main\nmain::proc(){@(vet_ignore={/* .| */ .unused}) value:=7;_=value}',
            'package main\nmain::proc(){@(vet_ignore={".|"}) value:=7;_=value}',
            'package main\nmain::proc(){@(other={.|}) value:=7;_=value}',
            'package main\nmain::proc(){@(vet_ignore={f(.|)}) value:=7;_=value}',
            'package main\nmain::proc(){@(vet_ignore=Rules{.|}) value:=7;_=value}',
            'package main\nmain::proc(){call(vet_ignore={.|})}',
        ]:
            with self.subTest(marked=marked):
                _,_,_,result=self.complete(marked)
                items=result.get('items',[]) if isinstance(result,dict) else result or []
                self.assertFalse(any(i['label'] in {'lifetime_unknown','leak_unknown'} for i in items),items)

    def test_set_rules_are_available_for_import_annotations(self):
        _,_,_,result=self.complete('package main\n@(vet_ignore={.|})\nimport "core:thread"\nmain::proc(){}')
        self.assertEqual({i['label'] for i in result['items']},OPTIONS,result)

    def test_unfinished_attribute_at_end_of_document(self):
        for ending in ['{.|', '{.unused, .|', '({.|', '{.li|']:
            with self.subTest(ending=ending):
                _,_,_,result=self.complete('package main\n@(vet_ignore='+ending)
                expected={'lifetime_unknown'} if ending=='{.li|' else OPTIONS-({'unused'} if '.unused' in ending else set())
                self.assertEqual({i['label'] for i in result['items']},expected,result)


if __name__=='__main__':unittest.main()
