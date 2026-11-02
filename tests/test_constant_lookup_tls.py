"""Indexed compile-time values retain value colors instead of element-type colors."""
import unittest
from tests import test_tls as base


class ConstantLookupTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_enum_indexed_constants_aliases_and_actual_type_aliases(self):
        text='''package main
Games::enum{roulette,blackjack}
TABLE::[Games]f64{.roulette=0.526,.blackjack=0.503}
GLOBAL_ODDS::TABLE[.roulette]
GLOBAL_ALIAS::GLOBAL_ODDS
main::proc(){
 GAME::Games.roulette
 HOUSE_ODDS::TABLE[GAME]
 ALIAS::HOUSE_ODDS
 WRAPPED::(TABLE[GAME])
 mutable:=TABLE[GAME]
 immutable:>TABLE[GAME]
 Actual_Type::type_of(TABLE[GAME])
 Other_Type::Actual_Type
 value:Other_Type=HOUSE_ODDS
 _=GLOBAL_ODDS+GLOBAL_ALIAS+ALIAS+WRAPPED+mutable+immutable+value
}
'''
        client,main=self.open(text)
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        modifiers=client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly=1<<modifiers.index('readonly')
        constant=1<<modifiers.index('thorCompileTime')
        for name in ['GLOBAL_ODDS','GLOBAL_ALIAS','HOUSE_ODDS','ALIAS','WRAPPED']:
            values=[s for s in spans if s[3]==name]
            self.assertGreaterEqual(len(values),2,(name,values))
            for start,end,kind,token,flags in values:
                self.assertEqual(kind,'variable',(token,kind))
                self.assertEqual(flags&(readonly|constant),readonly|constant,(token,flags))
        for name in ['Actual_Type','Other_Type']:
            self.assertTrue(all(s[2]=='type' for s in spans if s[3]==name),name)
        for span in [s for s in spans if s[3] in ['mutable','immutable']]:
            self.assertEqual(span[2],'variable',span)
            self.assertEqual(bool(span[4]&readonly),span[3]=='immutable',span)
            self.assertFalse(span[4]&constant,span)
        self.assertIn('HOUSE_ODDS :: TABLE[GAME]',self.hover(client,main,text,'HOUSE_ODDS::')['contents']['value'])
        edit=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'HOUSE_ODDS::'),'newName':'odds'})
        self.assertEqual(len(edit['changes'][main.as_uri()]),3,edit)

    def test_string_slice_matrix_and_nested_array_values(self):
        text='''package main
TEXT::"abc"
LETTER::TEXT[0]
PART::TEXT[1:]
MATRIX::matrix[2,2]f64{1,2,3,4}
CELL::MATRIX[0,1]
ROW::MATRIX[0]
NESTED::[2][2]f64{{1,2},{3,4}}
NESTED_CELL::NESTED[0][1]
Scalar::(f64)
Generic::struct($T:typeid){value:T}
Record::(Generic(int))
main::proc(){_=LETTER;_=PART;_=CELL;_=ROW;_=NESTED_CELL}
'''
        client,main=self.open(text,compiler=False)
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        constant=1<<client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('thorCompileTime')
        for name in ['LETTER','PART','CELL','ROW','NESTED_CELL']:
            values=[s for s in spans if s[3]==name]
            self.assertEqual(len(values),2,(name,values))
            for span in values:
                self.assertEqual(span[2],'variable',span)
                self.assertTrue(span[4]&constant,span)
        for name,kind in [('Scalar','type'),('Record','struct')]:
            values=[s for s in spans if s[3]==name]
            self.assertEqual(len(values),1,(name,values))
            self.assertEqual(values[0][2],kind,values)

    def test_unsaved_indexed_value_to_type_alias_and_back(self):
        text='package main\nTABLE::[2]f64{0.5,0.6}\nmain::proc(){ODDS::TABLE[0];_=ODDS}\n'
        client,main=self.open(text,compiler=False)
        for version,type_alias in [(2,True),(3,False),(4,True),(5,False)]:
            changed=text.replace('ODDS::TABLE[0]','ODDS::type_of(TABLE[0])') if type_alias else text
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},
                'contentChanges':[{'text':changed}]})
            values=[s for s in self.semantic_spans(client,main,changed) if s[3]=='ODDS']
            self.assertEqual(len(values),2,values)
            self.assertEqual([s[2] for s in values],['type' if type_alias else 'variable']*2)


if __name__=='__main__':
    unittest.main()
