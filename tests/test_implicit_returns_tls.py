"""Implicit fn returns retain original source ranges and contextual editor types."""
import json
import unittest
from tests import test_tls as base


class ImplicitReturnTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_hover_completion_navigation_and_original_tokens(self):
        text='''package main
Mode::enum{First,Second}
quad::fn(x:int)->int{4*x}
choose::fn()->Mode{.Second}
Record::struct{value:int}
build::fn(x:$T)->Record{Record{x}}
main::proc(){n:=3;answer:=quad(n);result:=build(n);_=answer;_=result.value;_=choose()}
'''
        for compiler in [False,True]:
            with self.subTest(compiler=compiler):
                client,main=self.open(text,compiler=compiler)
                if compiler:client.diagnostic(main.as_uri(),empty=True)
                function_hover=self.hover(client,main,text,'quad::')
                tail_hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'4*x',2)})
                result_hover=self.hover(client,main,text,'result:=')
                self.assertIn('fn',json.dumps(function_hover))
                self.assertIn('int',json.dumps(tail_hover))
                self.assertIn('Record',json.dumps(result_hover))
                completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'.Second',1)})
                items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
                self.assertIn('First',[item['label'] for item in items])
                definitions=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'4*x',2)})
                if isinstance(definitions,dict):definitions=[definitions]
                self.assertTrue(any(item['range']['start']==self.position(text,'x:int',0) for item in definitions or []),definitions)
                spans=self.semantic_spans(client,main,text)
                self.assert_original_identifier_tokens(main,text,spans)

    def test_unsaved_type_error_points_at_the_tail_expression_and_recovers(self):
        text='''// 😀 original offsets
package main
quad::fn(x:int)->int{"wrong"}
main::proc(){n:=4;_=quad(n)}
'''
        client,main=self.open(text,compiler=True)
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(error for error in errors if 'Cannot' in error['message'])
        self.assertEqual(error['range']['start'],self.position(text,'"wrong"',0))
        self.assertNotIn('__thor_',error['message'])
        fixed=text.replace('"wrong"','4*x')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(fixed,'4*x',2)})
        self.assertIn('int',json.dumps(hover))

    def test_enum_completion_while_the_tail_expression_is_incomplete(self):
        text='package main\nMode::enum{First,Second}\nchoose::fn()->Mode{.}\nmain::proc(){}\n'
        client,main=self.open(text,compiler=False)
        position=self.position(text,'Mode{.}',len('Mode{.'))
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':position,'context':{'triggerKind':2,'triggerCharacter':'.'}})
        items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
        self.assertIn('First',[item['label'] for item in items])
        self.assertIn('Second',[item['label'] for item in items])

    def test_imported_generic_implicit_fn_and_unsaved_overlay(self):
        helper='''package helper
Record::struct{value:int}
build::fn(x:$T)->Record{Record{x}}
'''
        text='''package main
import "helper"
main::proc(){n:=4;result:=helper.build(n);_=result.value}
'''
        client,main=self.open(text,{'helper/helper.thor':helper},compiler=True)
        client.diagnostic(main.as_uri(),empty=True)
        result_hover=self.hover(client,main,text,'result:=')
        self.assertIn('Record',json.dumps(result_hover))
        imported=main.parent/'helper/helper.thor'
        modified=helper.replace('Record{x}','Record{x+1}')
        client.send('textDocument/didOpen',{'textDocument':{'uri':imported.as_uri(),'languageId':'thor','version':1,'text':modified}})
        client.diagnostic(imported.as_uri(),empty=True)
        function_hover=self.hover(client,imported,modified,'build::')
        self.assertIn('fn',json.dumps(function_hover))
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'result.value',7)})
        items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
        self.assertIn('value',[item['label'] for item in items])

    def test_switch_tails_keep_result_context_navigation_and_tokens(self):
        text='''// 😀 original source positions
package main
Mode::enum{First,Second}
Record::struct{value:int}
choose::fn(x:int)->Record{switch x{case < 0:{x};case:{x+1}}}
mode::fn(x:Mode)->Mode{#partial switch x{case .First:.First;case:.Second}}
compiled::fn(x:$T)->T{#switch{case T==int:x+1;case:undefined}}
main::proc(){n:=3;result:=choose(n);number:=compiled(n);_=result.value;_=number;_=mode(.First)}
'''
        for compiler in [False,True]:
            with self.subTest(compiler=compiler):
                client,main=self.open(text,compiler=compiler)
                if compiler:client.diagnostic(main.as_uri(),empty=True)
                self.assertIn('Record',json.dumps(self.hover(client,main,text,'result:=')))
                self.assertIn('int',json.dumps(self.hover(client,main,text,'number:=')))
                completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'.Second',1)})
                items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
                self.assertTrue({'First','Second'}<={item['label'] for item in items},items)
                definitions=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'{x+1}',1)})
                if isinstance(definitions,dict):definitions=[definitions]
                self.assertTrue(any(item['range']['start']==self.position(text,'x:int',0) for item in definitions or []),definitions)
                self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))

    def test_switch_arm_diagnostics_and_unsaved_recovery(self):
        text='''// 😀
package main
choose::fn(x:int)->int{switch x{case < 0:"wrong";case:x}}
main::proc(){n:=3;_=choose(n)}
'''
        client,main=self.open(text,compiler=True)
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(item for item in errors if 'Cannot' in item['message'])
        self.assertEqual(error['range']['start'],self.position(text,'"wrong"',0))
        self.assertNotIn('__thor_',error['message'])
        fixed=text.replace('"wrong"','-x')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(fixed,'-x',1)})
        self.assertIn('int',json.dumps(hover))

    def test_incomplete_enum_switch_tail_retains_completion(self):
        text='package main\nMode::enum{First,Second}\nchoose::fn(x:int)->Mode{switch x{case 0:.;case:.Second}}\nmain::proc(){}\n'
        original=text.replace('0:.;case:.Second','0:return .First;case:return .Second')
        client,main=self.open(original,compiler=False)
        self.hover(client,main,original,'choose::')
        # Reuse the document parser at the same switch offset. A cached failed
        # statement attempt belongs to the old source snapshot, not this edit.
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':text}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'0:.;',3),'context':{'triggerKind':2,'triggerCharacter':'.'}})
        items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
        self.assertTrue({'First','Second'}<={item['label'] for item in items},items)


if __name__=='__main__':unittest.main()
