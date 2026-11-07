"""Real TLS scope, hover, rename, semantic tokens and compiler diagnostics."""
import json
import unittest
from tests import test_tls as base

class ComprehensionTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_types_scope_and_rename_before_the_binding(self):
        text='''package main
main::proc(){
values:=[1,2,3]
offset:=4
result:>[x+offset for x in values if x>1]
growing:=[dynamic]int{x for x in values}
unique:=set[int]{x for x in values}
lookup:=map[int]int{x=x*x for x in values}
_=result;_=growing;_=unique;_=lookup
}
'''
        client,main=self.open(text,compiler=False)
        for word,typ in [('result;','[]int'),('growing;','[dynamic]int'),('unique;','set[int]'),('lookup\n','map[int]int'),('x+offset','int')]:
            with self.subTest(word=word):
                hover=self.hover(client,main,text,word)
                self.assertIsNotNone(hover)
                self.assertIn(typ,hover['contents']['value'])
        params={'textDocument':{'uri':main.as_uri()},'position':self.position(text,'x+offset')}
        prepared=client.request('textDocument/prepareRename',params)
        self.assertIsNotNone(prepared)
        edit=client.request('textDocument/rename',{**params,'newName':'item'})
        changes=edit.get('changes',{}).get(main.as_uri(),[])
        if not changes:changes=[e for change in edit.get('documentChanges',[]) for e in change.get('edits',[])]
        self.assertEqual(len(changes),3,edit)
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        modifiers=client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly=1 << modifiers.index('readonly')
        entries=[(kind,mods) for _,_,kind,name,mods in spans if name=='x']
        self.assertTrue(entries)
        self.assertTrue(all(kind=='variable' and mods&readonly for kind,mods in entries),entries)

    def test_source_name_shadowing_and_output_field_completion(self):
        text='''package main
Item::struct{value:int}
main::proc(){
x:=[Item{1},Item{2}]
result:=[x.value for x in x if x.value>0]
_=result
}
'''
        client,main=self.open(text,compiler=False)
        result=self.hover(client,main,text,'result\n')
        self.assertIsNotNone(result)
        self.assertIn('[]int',result['contents']['value'])
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'in x if',3)})
        self.assertIn('"line": 3',json.dumps(definition))
        completions=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'x.value for',2),'context':{'triggerKind':1}})
        self.assertIn('value',json.dumps(completions))

    def test_constant_extents_flattening_and_typed_bindings(self):
        text='''package main
EVENS::[x*x for x in 0..<10 if x%2==0]
main::proc(){
rows:=[[1,2],[3,4]]
flat:=[..row for row in rows]
floating:=[x/2 for x:f64 in 1..=3]
indices:=[i for value,(i:i8) in flat]
_=EVENS;_=flat;_=floating;_=indices
}
'''
        client,main=self.open(text,compiler=False)
        for word,typ in [('EVENS;','[5]int'),('flat;','[]int'),('floating;','[]f64'),('indices\n','[]i8')]:
            with self.subTest(word=word):
                hover=self.hover(client,main,text,word)
                self.assertIsNotNone(hover)
                self.assertIn(typ,hover['contents']['value'])
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))

    def test_record_output_hover_and_mutable_reference_binding_tokens(self):
        text='''package main
Item::struct{value:int}
main::proc(){
values:=[Item{1},Item{2}]
copy:=[x for x in values]
references:=[x.value for &x in values]
_=copy;_=references
}
'''
        client,main=self.open(text,compiler=False)
        hover=self.hover(client,main,text,'copy;')
        self.assertIsNotNone(hover)
        self.assertIn('[]Item',hover['contents']['value'])
        self.assertIn('[]int',json.dumps(self.hover(client,main,text,'references\n')))
        spans=self.semantic_spans(client,main,text)
        readonly=1<<client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        start=text.index('references:=')
        end=text.index('\n',start)
        references=[(kind,mods) for offset,_,kind,name,mods in spans if name=='x' and start<=offset<end]
        self.assertTrue(references)
        self.assertTrue(all(kind=='variable' and not mods&readonly for kind,mods in references),references)

    def test_explicit_and_contextual_enum_outputs(self):
        text='''package main
Color::enum{Red,Blue}
main::proc(){
explicit:=[]Color{.Red for x in 0..<3}
contextual:[]Color=[.Blue for x in 0..<2]
_=explicit;_=contextual
}
'''
        client,main=self.open(text,compiler=False)
        for word in ['.Red for','.Blue for']:
            completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,word,2),'context':{'triggerKind':1}})
            self.assertIn('Red',json.dumps(completion))
            self.assertIn('Blue',json.dumps(completion))
            hover=self.hover(client,main,text,word)
            self.assertIn('Color',json.dumps(hover))

    def test_incomplete_comprehensions_recover_without_stale_scopes(self):
        for expression in ['[x for','[x for x in]','[x for x in values if]','map[int]int{x= for x in values}']:
            with self.subTest(expression=expression):
                text='package main\nmain::proc(){values:=[1,2];result:='+expression+'\n_=result}\n'
                client,main=self.open(text,compiler=False)
                client.request('textDocument/documentSymbol',{'textDocument':{'uri':main.as_uri()}})
                repaired='package main\nmain::proc(){values:=[1,2];result:=[x for x in values];_=result}\n'
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':repaired}]})
                self.assertIn('[]int',json.dumps(self.hover(client,main,repaired,'result}')))
                self.assert_original_identifier_tokens(main,repaired,self.semantic_spans(client,main,repaired))

    def test_diagnostics_and_unsaved_recovery(self):
        text='package main\nmain::proc(){\nvalues:=[1,2]\nresult:=[missing(x) for x in values]\n_=result\n}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Undeclared name: missing')
        error=next(e for e in errors if 'Undeclared name: missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'missing',0))
        fixed=text.replace('missing(x)','x*x')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[]int',json.dumps(self.hover(client,main,fixed,'result\n')))

if __name__=='__main__':unittest.main()
