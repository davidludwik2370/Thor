"""Fallback list result inference, contextual completion, and source diagnostics."""
import json
import unittest
from tests import test_tls as base


class OrElseListTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_inferred_immutable_results_and_struct_fields(self):
        text='''package main
Row::struct{number:int,text:string}
get::proc()->(int,Row,bool){return 0,{},false}
main::proc(){
    _,row:>get() or_else (_,{number=7,text="ok"})
    _=row.number
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('Row',self.hover(client,main,text,'row:>')['contents']['value'])
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'row.number',len('row.')),'context':{'triggerKind':1}})
        self.assertTrue({'number','text'}<={item['label'] for item in completion['items']},completion)
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        readonly=1<<client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        rows=[mods for _,_,_,name,mods in spans if name=='row']
        self.assertTrue(rows)
        self.assertTrue(all(mods&readonly for mods in rows),rows)
        hover=self.hover(client,main,text,'or_else')
        self.assertIn('fallback',json.dumps(hover))

    def test_each_slot_has_its_own_enum_completion(self):
        text='''package main
Left::enum{A,B}
Right::enum{First,Second}
get::proc()->(Left,Right,bool){return .A,.First,false}
main::proc(){a,b:=get() or_else (.A,.Second);_=a;_=b}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        for phrase,expected,absent in [('(.A',{'A','B'},'Second'),(',.Second',{'First','Second'},'A')]:
            with self.subTest(phrase=phrase):
                completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                    'position':self.position(text,phrase,phrase.index('.')+1),'context':{'triggerKind':1}})
                labels={item['label'] for item in completion['items']}
                self.assertTrue(expected<=labels,completion)
                self.assertNotIn(absent,labels)
        partial=text.replace('.Second);',' .);')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':partial}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(partial,' .);',2),'context':{'triggerKind':2,'triggerCharacter':'.'}})
        self.assertTrue({'First','Second'}<={item['label'] for item in completion['items']},completion)

    def test_aliased_generic_callback_preserves_contextual_result_types(self):
        text='''package main
Kind::enum{A,B}
get::proc(value:$T)->(first:int,second:T,ok:bool){return 0,value,false}
alias::get
main::proc(){_,kind:>alias(Kind.A) or_else (_,.B);_=kind}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('Kind',self.hover(client,main,text,'kind:>')['contents']['value'])
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'_,.B',3),'context':{'triggerKind':1}})
        self.assertTrue({'A','B'}<={item['label'] for item in completion['items']},completion)

    def test_mismatched_discard_and_unsaved_recovery_use_the_original_slot(self):
        text='''package main
get::proc()->(int,int,bool){return 0,0,false}
main::proc(){_="😀";_,value:>get() or_else (0,1);_=value}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='position 1 is discarded')
        error=next(error for error in errors if 'position 1 is discarded' in error['message'])
        self.assertEqual(error['range']['start'],self.position(text,'(0,1)',1))
        self.assertEqual(error['range']['end'],self.position(text,'(0,1)',2))
        self.assertNotIn('__thor',json.dumps(errors))
        changed=text.replace('(0,1)','(_,1)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn(': int',self.hover(client,main,changed,'value:>')['contents']['value'])

    def test_type_errors_and_partial_lists_do_not_expose_generated_helpers(self):
        text='''package main
get::proc()->(int,string,bool){return 0,"",false}
main::proc(){a,b:=get() or_else ("wrong","ok");_=a;_=b}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Cannot convert')
        error=next(error for error in errors if 'Cannot convert' in error['message'])
        self.assertEqual(error['range']['start'],self.position(text,'"wrong"',0))
        self.assertNotIn('__thor',json.dumps(errors))
        fixed=text.replace('("wrong","ok")','(7,"ok")')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        partial=fixed.replace('(7,"ok")','(7,)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':partial}]})
        errors=client.diagnostic(main.as_uri(),contains='at least two')
        self.assertNotIn('__thor',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
