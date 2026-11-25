"""Case comparison editor queries use the selector and original source spans."""
import json
import unittest
from tests import test_tls as base


class SwitchComparisonTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_runtime_compile_and_fn_hovers_and_tokens(self):
        text='''// 😀
package main
Color::enum{Red,Blue}
Record::struct{value:int}
choose::fn(n:int)->int{return switch n{case < 0:1;case >= 0:2;case:3}}
A::#switch 2{case < 0:undefined;case != 0:Record{7};case:undefined}
main::proc(){
 color:Color=.Red
 #partial switch color{case != .Red:case .Red:case:}
 record:=switch 3{case < 0:Record{0};case >= 0:Record{7};case:Record{1}}
 _=record.value
 value:=choose(2)
 _=value;_=A.value
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('Record',json.dumps(self.hover(client,main,text,'record:=')))
        self.assertIn('int',json.dumps(self.hover(client,main,text,'value:=')))
        self.assertIn('Color',json.dumps(self.hover(client,main,text,'.Red:')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        result=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'case >= 0',6)})
        self.assertNotIn('__thor_',json.dumps(result))

    def test_enum_completion_in_statement_and_expression_prefix(self):
        for expression in (False,True):
            with self.subTest(expression=expression):
                text='''package main
Color::enum{Red,Blue}
main::proc(){color:Color=.Red;BODY}
'''.replace('BODY','value:=switch color{case != .Red:1;case:0};_=value' if expression else 'switch color{case != .Red:case:}')
                client,main=self.open(text)
                position=self.position(text,'!= .Red',4)
                result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':position,'context':{'triggerKind':2,'triggerCharacter':'.'}})
                labels={item['label'] for item in result.get('items',[]) } if isinstance(result,dict) else {item['label'] for item in (result or [])}
                self.assertTrue({'Red','Blue'}<=labels,labels)

    def test_comparison_only_inside_fstring_keeps_enum_context(self):
        text='''package main
Color::enum{Red,Blue}
main::proc(){color:Color=.Red;text:=f"{#partial switch color{case != .Red:1;case:0}}";defer delete(text)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('Color',json.dumps(self.hover(client,main,text,'.Red:')))
        result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'!= .Red',4),'context':{'triggerKind':2,'triggerCharacter':'.'}})
        items=result.get('items',[]) if isinstance(result,dict) else result or []
        self.assertTrue({'Red','Blue'}<={item['label'] for item in items},items)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))

    def test_invalid_selectorless_prefix_does_not_crash_tls(self):
        text='package main\nmain::proc(){switch {case < 2:}}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='require a value switch selector')
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('switch {','switch 1 {')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIsNone(client.process.poll())

    def test_mapped_operand_error_and_unsaved_repair(self):
        text='''// 😀
package main
main::proc(){n:=3;switch n{case < "bad":case:}}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Cannot convert')
        error=next(e for e in errors if 'Cannot convert' in e['message'])
        self.assertEqual(error['range']['start']['line'],2,error)
        self.assertGreater(error['range']['end']['character'],error['range']['start']['character'],error)
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('"bad"','4')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('int',json.dumps(self.hover(client,main,fixed,'n:=')))


if __name__=='__main__':unittest.main()
