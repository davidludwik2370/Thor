"""Compiler-inferred #mut hints and permission diagnostics over real LSP."""
import json
import unittest

from tests import test_tls as base


class SliceAccessTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans

    def hints(self,client,path):
        return client.request('textDocument/inlayHint',{'textDocument':{'uri':path.as_uri()},
            'range':{'start':{'line':0,'character':0},'end':{'line':100,'character':0}}}) or []

    def test_grouped_inferred_and_explicit_parameter_hints(self):
        text='''// 😀 byte and UTF-16 positions
package main
copy_values::proc(/* 😀 */source,destination:[]int){copy(destination,source)}
explicit::proc(a,#mut b:[]int){_=a;_=b}
main::proc(){a:=[]int{1};b:=[]int{0};copy_values(a,b);explicit(a,b)}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hints=[hint for hint in self.hints(client,path) if '#mut' in hint['label']]
        self.assertEqual(len(hints),1,hints)
        self.assertEqual(hints[0]['position'],self.position(text,'destination:',0))
        hover=self.hover(client,path,text,'copy_values(a')['contents']['value']
        self.assertIn('#mut destination',hover)
        self.assertNotIn('#mut source',hover)
        signature=client.request('textDocument/signatureHelp',{'textDocument':{'uri':path.as_uri()},
            'position':self.position(text,'copy_values(a,b)',len('copy_values(a,'))})
        self.assertIn('#mut destination',signature['signatures'][0]['label'])

    def test_argument_error_notes_and_unsaved_recovery(self):
        text='''// 😀 original source
package main
change::proc(data:[]int){/* 😀 */data[0]=9}
main::proc(){values :> []int{1};/* 😀 */change(values)}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        relevant=[error for error in errors if 'read-only slice' in error['message']]
        self.assertEqual(relevant[0]['range']['start'],self.position(text,'values)',0),errors)
        self.assertTrue(any(note['location']['range']['start']==self.position(text,'data[0]=9',0)
                            for error in relevant for note in error.get('relatedInformation',[])),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        corrected=text.replace('values :>','values :=')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_hints_disappear_when_unsaved_body_becomes_reader(self):
        text='package main\nwork::proc(a,b:[]int){b[0]=a[0]}\nmain::proc(){}\n'
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        self.assertTrue(any('#mut' in hint['label'] for hint in self.hints(client,path)))
        corrected=text.replace('b[0]=a[0]','_=a[0]+b[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertFalse(any('#mut' in hint['label'] for hint in self.hints(client,path)))

    def test_annotation_rename_preserves_directive_and_source_positions(self):
        text='package main\nwork::proc(a,#mut b:[]int){b[0]=a[0]}\nmain::proc(){}\n'
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        renamed=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},
            'position':self.position(text,'b:[]int'),'newName':'destination'})
        edits=renamed.get('changes',{}).get(path.as_uri(),[])
        self.assertEqual(len(edits),2,renamed)
        self.assertTrue(all(edit['newText']=='destination' for edit in edits))

    def test_nested_readonly_members_and_independent_copies_have_correct_modifiers(self):
        text='''package main
Leaf::struct{value:int}
Row::struct{count:int,items:[]Leaf}
main::proc(){
    rows :> []Row{{1,[]Leaf{{7}}}}
    _=rows[0].count
    row:=rows[0]
    row.count=9
    _=row.items[0].value
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,path,text)
        properties={start:(word,bool(flags&readonly)) for start,_,kind,word,flags in spans if kind=='property'}
        for needle,word,expected in [('rows[0].count','count',True),('row.count=9','count',False),('row.items[0].value','value',True)]:
            offset=len(text[:text.index(needle)+needle.index(word)].encode())
            self.assertEqual(properties.get(offset),(word,expected),properties)

    def test_refresh_responses_do_not_crash_the_server(self):
        text='package main\nwork::proc(a:[]int){a[0]=1}\nmain::proc(){}\n'
        client,path=self.open(text,refresh=True)
        client.diagnostic(path.as_uri(),empty=True)
        self.assertTrue(any('#mut' in hint['label'] for hint in self.hints(client,path)))
        self.hints(client,path) # Barrier after responses to refresh requests.
        methods={request['method'] for request in client.server_requests}
        self.assertIn('workspace/inlayHint/refresh',methods)
        self.assertIn('workspace/semanticTokens/refresh',methods)
        self.assertIsNone(client.process.poll())

    def test_scalar_generic_copies_do_not_get_slice_mutation_hints(self):
        text='package main\ncopy_value::proc(value:$T)->T{local:=value;return local}\nmain::proc(){_=copy_value(7)}\n'
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        self.assertFalse(any('#mut' in hint['label'] for hint in self.hints(client,path)))


if __name__=='__main__':unittest.main()
