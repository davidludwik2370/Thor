"""Real stdio TLS coverage for built-in arbitrary-precision numbers."""
import json
import unittest
from tests import test_tls as tls


class BigTLSTests(unittest.TestCase):
    setUpClass=classmethod(tls.TLSTests.setUpClass.__func__)
    open=tls.TLSTests.open
    position=staticmethod(tls.TLSTests.position)
    hover=tls.TLSTests.hover
    semantic_spans=tls.TLSTests.semantic_spans
    assert_original_identifier_tokens=tls.TLSTests.assert_original_identifier_tokens

    def test_logical_hovers_properties_and_optional_constructors(self):
        text='''package main
identity::proc(value:Integer)->Integer{return value}
main::proc(){
 huge:Integer=12345678901234567890123456789012345678901234567890
 rational:=Rational(22,13)
 parsed,ok:=Integer("123")
 ratio,valid:=Rational("0.(3)")
 component:=rational.numerator
 pointer:=&rational
 denominator:=pointer.denominator
 result:=identity(huge)
 _=parsed;_=ok;_=ratio;_=valid;_=component;_=denominator;_=result
}
'''
        client,main=self.open(text,compiler=False)
        for name,expected in [('huge:','huge: Integer'),('rational:=','rational: Rational'),
                              ('parsed,','parsed: Integer'),('ok:=','ok: bool'),
                              ('ratio,','ratio: Rational'),('valid:=','valid: bool'),
                              ('component:=','component: Integer'),('denominator:=','denominator: Integer'),
                              ('result:=','result: Integer'),('identity::','proc(value: Integer) -> Integer')]:
            with self.subTest(name=name):
                hover=json.dumps(self.hover(client,main,text,name))
                self.assertIn(expected,hover)
                self.assertNotIn('_value',hover)
                self.assertNotIn('__thor_',hover)
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rational.numerator',len('rational.')+1)})
        self.assertIn('numerator: Integer',json.dumps(hover))
        self.assertIn('independent owned',json.dumps(hover))
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rational.numerator',len('rational.')+1)})
        self.assertIn('/big/',json.dumps(definition))
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        for token in ['Integer','Rational']:
            self.assertTrue(any(t==token and kind in {'struct','type'} for _,_,kind,t,_ in spans),spans)
        numerator=next(span for span in spans if span[3]=='numerator')
        modifiers=client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        self.assertTrue(numerator[4] & (1 << modifiers.index('readonly')))
        self.assertNotIn(numerator[2],{'struct','type'})
        signature=client.request('textDocument/signatureHelp',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'Rational(22,13)',len('Rational(22,'))})
        self.assertIn('numerator, denominator',json.dumps(signature))

    def test_property_completion_hides_storage_and_rename_keeps_source_ranges(self):
        text='package main\nmain::proc(){\n rational:=Rational(22,13)\n n:=rational.numerator\n _=n\n}\n'
        client,main=self.open(text,compiler=False)
        complete=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rational.numerator',len('rational.'))})
        items=complete.get('items',[]) if isinstance(complete,dict) else complete
        names={item['label'] for item in items}
        self.assertIn('numerator',names)
        self.assertIn('denominator',names)
        self.assertFalse(any(name.startswith('_') for name in names),names)
        result=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rational:='),'newName':'fraction'})
        edits=result['changes'][main.as_uri()]
        self.assertEqual(len(edits),2)
        self.assertEqual({e['range']['start']['line'] for e in edits},{2,3})
        for fragment,offset in [('Rational(',1),('rational.numerator',len('rational.')+1)]:
            prepared=client.request('textDocument/prepareRename',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,fragment,offset)})
            self.assertIsNone(prepared)


    def test_properties_report_mapped_errors_and_recover_after_edit(self):
        text='package main\nmain::proc(){\n r:=Rational(22,13)\n r.numerator=12\n delete(r)\n}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='read-only')
        error=next(e for e in errors if 'read-only' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'r.numerator',0))
        fixed=text.replace('r.numerator=12','n:=r.numerator;delete(n)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_native_annotations_are_presented_logically_only_in_thor(self):
        text='package main\nimport "library"\nmain::proc(){value:=library.create();_=value}\n'
        library='package library\nimport "thor:big"\ncreate::proc()->big.Integer{return big.from_integer(123)}\n'
        client,main=self.open(text,{'library/main.odin':library},compiler=False)
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'library.create',len('library.')+1)})
        self.assertIn('-> Integer',json.dumps(hover))
        self.assertNotIn('big.Integer',json.dumps(hover))
        native=main.parent/'library/main.odin'
        client.send('textDocument/didOpen',{'textDocument':{'uri':native.as_uri(),'languageId':'odin','version':1,'text':library}})
        hover=client.request('textDocument/hover',{'textDocument':{'uri':native.as_uri()},'position':self.position(library,'create::')})
        self.assertIn('big.Integer',json.dumps(hover))

    def test_user_names_shadow_predefined_types(self):
        text='package main\nInteger::struct{value:int}\nmain::proc(){number:=Integer{3};_=number.value}\n'
        client,main=self.open(text,compiler=False)
        hover=json.dumps(self.hover(client,main,text,'number:='))
        self.assertIn('Integer',hover)
        self.assertIn('value',json.dumps(self.hover(client,main,text,'Integer::')))
