"""Selective-import suggestions use public package members and original edits."""
import json
import unittest
from tests import test_tls as base


LIBRARY='''package library
// Alpha accepts a number.
alpha::proc(value:int)->int{return value}
alpine::proc(){}
alias::alpha
Beta::struct{value:int}
ANSWER::42
café::proc(){}
@(private) for_int::proc(value:int)->int{return value}
@(private) for_string::proc(value:string)->string{return value}
overloaded::proc{for_int,for_string}
@(private) hidden::proc(){}
@(private="file") file_hidden::proc(){}
'''


class ImportCompletionTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def complete(self,marked,trigger='/',**options):
        at=marked.index('|');text=marked.replace('|','',1)
        client,main=self.open(text,{'library/main.thor':LIBRARY},compiler=False,**options)
        context={'triggerKind':2,'triggerCharacter':trigger} if trigger else {'triggerKind':1}
        result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text[:at]+'CURSOR'+text[at:],'CURSOR',0),'context':context})
        return client,main,text,at,result

    def test_empty_and_trailing_comma_imports_suggest_public_members_only(self):
        for clause,trigger in [('import "library" /|','/'),('import lib "library" / |','/'),
                               ('import _ "library" / alpha, |',','),('import "library" / alpha,|',','),
                               ('import "library" / |',' '),('import "library" / alpha, |',' ')]:
            with self.subTest(clause=clause):
                client,main,text,at,result=self.complete('package main\n'+clause+'\nmain::proc(){}\n',trigger)
                items={item['label']:item for item in result['items']}
                self.assertTrue({'alpine','alias','Beta','ANSWER','café'}<=items.keys(),items.keys())
                for name in ['hidden','file_hidden','main','proc','library']:
                    self.assertNotIn(name,items)
                if 'alpha,' in clause:self.assertNotIn('alpha',items)
                else:self.assertIn('alpha',items)
                self.assertTrue(result['isIncomplete'])
                self.assertIn(',',client.capabilities['completionProvider']['triggerCharacters'])
                for item in items.values():
                    self.assertEqual(item['textEdit']['newText'],item['label'],item)
                    self.assertNotIn('additionalTextEdits',item)
                    self.assertNotEqual(item.get('insertTextFormat'),2,item)

    def test_prefix_filtering_docs_and_original_utf16_replacement(self):
        for prefix,expected in [('alp',{'alpha','alpine'}),('caf',{'café'})]:
            client,main,text,at,result=self.complete('package main\nimport "library" / /* 😀 */ '+prefix+'|\nmain::proc(){}\n',None)
            items={item['label']:item for item in result['items']}
            self.assertEqual(set(items),expected)
            for item in items.values():
                edit=item['textEdit']
                start=self.position(text,prefix,0)
                self.assertEqual(edit['range'],{'start':start,'end':{**start,'character':start['character']+len(prefix)}})
            if 'alpha' in items:self.assertIn('Alpha accepts a number',json.dumps(items['alpha']))

    def test_member_list_matches_namespace_completion_and_preserves_overloads(self):
        source='package main\nimport lib "library"\nmain::proc(){_=lib.|}\n'
        _,_,_,_,normal=self.complete(source,'.')
        _,_,_,_,selected=self.complete('package main\nimport "library" / |\nmain::proc(){}\n')
        self.assertEqual({item['label'] for item in selected['items']},{item['label'] for item in normal['items']})
        overloaded=next(item for item in selected['items'] if item['label']=='overloaded')
        self.assertEqual(overloaded['textEdit']['newText'],'overloaded')

    def test_multiline_comments_and_names_on_both_sides_of_cursor(self):
        for clause,trigger in [
            ('import "library" /\n    |','/'),
            ('import "library" / alpha, // previous\n    |',','),
            ('import "library" /* path comment */ / alpha, /* next */ |',','),
            ('import "library" / alpha, al|, Beta',None),
        ]:
            with self.subTest(clause=clause):
                _,_,_,_,result=self.complete('package main\n'+clause+'\nmain::proc(){}\n',trigger)
                names={item['label'] for item in result['items']}
                self.assertIn('alpine',names)
                if 'alpha,' in clause:self.assertNotIn('alpha',names)
                if ', Beta' in clause:self.assertNotIn('Beta',names)

    def test_standard_and_thor_libraries(self):
        for path,name in [('core:fmt','println'),('thor:basic','print'),('thor:sets','to_set')]:
            with self.subTest(path=path):
                _,_,_,_,result=self.complete('package main\nimport "'+path+'" / |\nmain::proc(){}\n')
                self.assertIn(name,{item['label'] for item in result['items']})

    def test_unsaved_import_path_changes_refresh_member_lists(self):
        client,main,_,_,_=self.complete('package main\nimport "library" / |\nmain::proc(){}\n')
        for version,path,expected in [(2,'core:fmt','println'),(3,'thor:basic','print'),(4,'library','alpha')]:
            source='package main\nimport "'+path+'" / \nmain::proc(){}\n'
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':source}]})
            result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(source,' / ',3),'context':{'triggerKind':2,'triggerCharacter':'/'}})
            self.assertIn(expected,{item['label'] for item in result['items']})

    def test_separators_in_comments_code_and_ended_lists_do_not_complete(self):
        for marked,trigger in [
            ('import "library" / alpha // , |',','),
            ('import "library" / /* , | */','/'),
            ('import "library" / alpha\n// , |',','),
            ('import "library" / alpha\nmain::proc(){_=8 / |}','/'),
            ('import "library"\nmain::proc(){call(1, |)}',','),
            ('import "library"\nmain::proc(){value:=1; |}',' '),
            ('import "library"\n/ |','/'),
            ('import * "library" / |','/'),
            ('import "missing" / |','/'),
        ]:
            with self.subTest(marked=marked):
                _,_,_,_,result=self.complete('package main\n'+marked+'\n',trigger)
                self.assertEqual(result['items'],[],result)


if __name__=='__main__':
    unittest.main()
