"""++ editing through TLS's real stdio transport."""
import json
import unittest

from tests import test_tls as base


class ConcatenationTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens
    unused_imports=base.TLSTests.unused_imports

    def test_selected_import_used_by_concatenation_stays_active(self):
        text='package main\nimport "values" / A, UNUSED\nALL::A ++ [3]\nmain::proc(){_=ALL}\n'
        client,main=self.open(text,files={'values/main.thor':'package values\nA::[1,2]\nUNUSED::7\n'},
                              compiler=False,unused_imports=True)
        errors=self.unused_imports(client,main)
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(text,'UNUSED\n',0))

    def test_hover_lengths_chains_empty_operands_nested_arrays_and_slices(self):
        text='''package main
A::[1,2]
B::[3]
ALL::A ++ B ++ [4]
S::[]int{1} ++ []int{2}
ROWS::[[1,2]] ++ [[3,4]]
EMPTY::[] ++ A
SMALL::[1]i8{2}
main::proc(){local:=ALL;rows:=ROWS;empty:=EMPTY;small:=[0] ++ [1] ++ SMALL;_=local;_=S;_=rows;_=empty;_=small}
'''
        client,main=self.open(text)
        for word,typ in [('local;','[4]int'),('_=S','[]int'),('_=rows','[2][2]int'),('_=empty','[2]int'),('_=small','[3]i8')]:
            with self.subTest(word=word):
                position=self.position(text,word,2 if word.startswith('_=') else 0)
                hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':position})
                self.assertIsNotNone(hover)
                self.assertIn(typ,hover['contents']['value'])
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        legend=client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        constant=1 << legend.index('thorCompileTime')
        entries=[(kind,mods) for _,_,kind,name,mods in spans if name=='ALL']
        self.assertTrue(entries)
        self.assertTrue(all(kind=='variable' and mods&constant for kind,mods in entries),entries)

    def test_enum_context_completion_and_original_rename_references(self):
        text='''package main
Color::enum{Red,Blue}
A::[1,2]
ALL::A ++ [3]
COLORS:[2]Color:[.Red] ++ [.Blue]
main::proc(){_=ALL;_=COLORS;_=A}
'''
        client,main=self.open(text)
        completions=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'.Red',2),'context':{'triggerKind':1}})
        self.assertIn('Red',json.dumps(completions))
        self.assertIn('Blue',json.dumps(completions))
        params={'textDocument':{'uri':main.as_uri()},'position':self.position(text,'A::',0)}
        prepared=client.request('textDocument/prepareRename',params)
        self.assertIsNotNone(prepared)
        edit=client.request('textDocument/rename',{**params,'newName':'FIRST'})
        changes=edit.get('changes',{}).get(main.as_uri(),[])
        if not changes:
            changes=[e for change in edit.get('documentChanges',[]) for e in change.get('edits',[])]
        self.assertEqual(len(changes),3,edit)
        self.assertTrue(all(change['newText']=='FIRST' for change in changes))

    def test_source_diagnostics_and_unsaved_constant_recovery(self):
        text='package main\nmain::proc(){\n// 😀\nvalues:>[1]\n_=[2] ++ values\n}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='requires compile-time constant operands')
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(text,'values\n',0))
        self.assertNotIn('Thor expression',json.dumps(errors))
        changed=text.replace('values:>','values::')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assert_original_identifier_tokens(main,changed,self.semantic_spans(client,main,changed))

    def test_element_mismatch_reports_source_types(self):
        text='package main\nA::[1]int{1}\nB::[1]f64{2}\nALL::A ++ B\nmain::proc(){_=ALL}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='matching element types')
        diagnostic=next(e for e in errors if 'matching element types' in e['message'])
        self.assertEqual(diagnostic['range']['start'],self.position(text,'++ B',3))
        self.assertIn('int',diagnostic['message'])
        self.assertIn('f64',diagnostic['message'])
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_cyclic_and_incomplete_edited_constants_do_not_stall_tls(self):
        for declaration in ['A::A ++ [1]','A::B ++ [1]\nB::A ++ [2]','A::[1] ++']:
            with self.subTest(declaration=declaration):
                text='package main\n'+declaration+'\nmain::proc(){_=A}\n'
                client,main=self.open(text,compiler=False)
                client.request('textDocument/documentSymbol',{'textDocument':{'uri':main.as_uri()}})
                changed='package main\nA::[1] ++ [2]\nmain::proc(){values:=A;_=values}\n'
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
                hover=self.hover(client,main,changed,'values}')
                self.assertIsNotNone(hover)
                self.assertIn('[2]int',hover['contents']['value'])


if __name__=='__main__':unittest.main()
