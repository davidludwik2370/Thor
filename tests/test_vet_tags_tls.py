"""File-vet completion operates on original headers, including unfinished edits."""
import json
import unittest
from tests import test_tls as base
from tests.test_native_vet import NATIVE_TAGS
OPTIONS=NATIVE_TAGS | {"no_alloc"}


class VetTagTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def complete(self,marked,trigger=None,compiler=False):
        at=marked.index('|');text=marked.replace('|','',1)
        client,main=self.open(text,compiler=compiler)
        context={'triggerKind':2,'triggerCharacter':trigger} if trigger else {'triggerKind':1}
        result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text[:at]+'CURSOR'+text[at:],'CURSOR',0),'context':context})
        return client,main,text,result

    def test_bare_header_and_separator_triggers_offer_supported_options(self):
        for header,trigger in [('#+vet|',None),('#+vet |',' '),('#+vet\t|',None),
                               ('#+vet no_alloc,|',','),('#+vet no_alloc, |',' '),
                               ('#+vet no_alloc |',' ')]:
            with self.subTest(header=header):
                client,_,_,result=self.complete(header+'\npackage main\nmain::proc(){}',trigger)
                expected=set(OPTIONS)
                if 'no_alloc' in header:expected.remove('no_alloc')
                self.assertEqual({item['label'] for item in result['items']},expected)
                self.assertTrue(result['isIncomplete'])
                self.assertIn(' ',client.capabilities['completionProvider']['triggerCharacters'])
                self.assertIn(',',client.capabilities['completionProvider']['triggerCharacters'])
                if header=='#+vet|':
                    self.assertTrue(all(item['textEdit']['newText']==' '+item['label'] for item in result['items']))

    def test_native_option_inventory_matches_installed_native_names(self):
        _,_,_,result=self.complete('#+vet |\npackage main\nmain::proc(){}')
        expected=OPTIONS
        self.assertEqual({item['label'] for item in result['items']},expected)
        for item in result['items']:
            self.assertTrue(item.get('documentation'),item)
            self.assertIn('File vet policy',item['detail'])
            self.assertNotIn('additionalTextEdits',item)
        no_alloc=next(item for item in result['items'] if item['label']=='no_alloc')
        self.assertIn('allocation',json.dumps(no_alloc['documentation']))

    def test_filtering_and_full_token_replacement_preserve_original_ranges(self):
        for header,word,label in [('#+vet no|','no','no_alloc'),
                                  ('#+vet se|icolon','seicolon','semicolon'),
                                  ('#+vet wh|en-shadowing','when-shadowing','when-shadowing')]:
            with self.subTest(header=header):
                _,_,text,result=self.complete('// 😀 header\r\n'+header+'\r\npackage main\r\nmain::proc(){}')
                self.assertEqual([item['label'] for item in result['items']],[label])
                edit=result['items'][0]['textEdit']
                start=self.position(text,word,0)
                self.assertEqual(edit,{'range':{'start':start,'end':{**start,'character':start['character']+len(word)}},'newText':label})

    def test_existing_names_on_both_sides_and_native_opt_outs(self):
        for header,expected in [('#+vet no_alloc, |, when-shadowing',OPTIONS-{'no_alloc','when-shadowing'}),
                                ('#+vet !se|',{'!semicolon'}),
                                ('#+vet !|',{'!'+name for name in NATIVE_TAGS}),
                                ('#+vet semicolon, !|',{'!'+name for name in NATIVE_TAGS-{'semicolon'}})]:
            with self.subTest(header=header):
                _,_,_,result=self.complete(header+'\npackage main\nmain::proc(){}')
                self.assertEqual({item['label'] for item in result['items']},expected)

    def test_comments_strings_and_other_tags_do_not_offer_vet_options(self):
        for marked in ['// #+vet |\npackage main\nmain::proc(){}',
                       '/* #+vet | */\npackage main\nmain::proc(){}',
                       '#+vet // |\npackage main\nmain::proc(){}',
                       '#+vet /* | */\npackage main\nmain::proc(){}',
                       '#+build |\npackage main\nmain::proc(){}',
                       '#+veto |\npackage main\nmain::proc(){}',
                       'package main\nmain::proc(){text:="#+vet |";_=text}',
                       'package main\nmain::proc(){/* #+vet | */}']:
            with self.subTest(marked=marked):
                _,_,_,result=self.complete(marked,' ')
                self.assertEqual(result['items'],[])

    def test_unfinished_supported_headers_do_not_report_bundle_errors(self):
        for header in ['#+vet','#+vet no','#+vet se','#+vet semicolon, no','#+vet !wh','#+vet no \r']:
            with self.subTest(header=header):
                client,main=self.open(header+'\npackage main\nmain::proc(){}')
                client.diagnostic(main.as_uri(),empty=True)

    def test_completed_unsupported_options_and_typos_keep_their_errors(self):
        for header in ['#+vet no_aloc','#+vet no-such-policy']:
            with self.subTest(header=header):
                client,main=self.open(header+'\npackage main\nmain::proc(){}')
                client.diagnostic(main.as_uri(),contains='Invalid vet flag name:')

    def test_completed_native_tags_keep_native_errors_and_opt_outs(self):
        for header in ('#+vet unused','#+vet cast','#+vet deprecated','#+vet explicit-allocators'):
            with self.subTest(header=header):
                client,main=self.open(header+'\npackage main\nmain::proc(){}')
                client.diagnostic(main.as_uri(),empty=True)
        client,main=self.open('#+vet unused\npackage main\nmain::proc(){x:=1}')
        errors=client.diagnostic(main.as_uri(),contains="'x' declared but not used")
        self.assertTrue(any(e['range']['start']['line']==2 for e in errors),errors)
        client,main=self.open('#+vet unused\n#+vet !unused\npackage main\nmain::proc(){x:=1}')
        errors=client.diagnostic(main.as_uri())
        self.assertTrue(all(e['severity']!=1 for e in errors),errors)

    def test_unsaved_selection_enables_real_contract_diagnostics_and_recovery(self):
        text='#+vet \npackage main\nmain::proc(){_=make([]int,1)}'
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        prefix=text.replace('#+vet ','#+vet no')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':prefix}]})
        result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(prefix,'#+vet no',8),'context':{'triggerKind':3}})
        self.assertEqual([item['label'] for item in result['items']],['no_alloc'])
        client.diagnostic(main.as_uri(),empty=True)
        selected=prefix.replace('#+vet no','#+vet '+result['items'][0]['textEdit']['newText'])
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':selected}]})
        client.diagnostic(main.as_uri(),contains='no_alloc:')
        fixed=selected.replace('make([]int,1)','[1,2]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_native_policies_check_thor_expression_reads_and_recover(self):
        for tag,body,message,needle in [
            ('nil-deref','p:^int;_=f"{p^}"','always nil','p^'),
            ('uninitialized','x:int=---;_=f"{x}"','before anything','{x}'),
            ('cast','x:=1;_=[cast(int)x for _ in 0..<3]','Unneeded cast','cast'),
        ]:
            with self.subTest(tag=tag):
                text='#+vet '+tag+'\npackage main\nmain::proc(){'+body+'}\n'
                client,main=self.open(text)
                diagnostics=client.diagnostic(main.as_uri(),contains=message)
                self.assertTrue(any(d['range']['start']['line']==2 for d in diagnostics),diagnostics)
                fixed=text.replace('p:^int','x:=1;p:=&x').replace('x:int=---','x:=1').replace('cast(int)x','x')
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
                diagnostics=client.diagnostic(main.as_uri(),empty=True)
                self.assertTrue(all(d['severity']!=1 for d in diagnostics),diagnostics)



if __name__=='__main__':unittest.main()
