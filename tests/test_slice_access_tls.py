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

    def test_constant_literal_storage_keeps_unsaved_diagnostic_and_token_positions(self):
        text='''// 😀 source positions
package main
GLOBAL :> []int{1,2}
main::proc(){
    values :> []int{3,4}
    if values[1]+GLOBAL[0]!=5 {panic("values")}
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,path,text)
        values=[span for span in spans if span[3]=='values']
        self.assertEqual(len(values),2,spans)
        self.assertTrue(all(span[4]&readonly for span in values),values)
        changed=text.replace('if values[1]+GLOBAL[0]!=5 {panic("values")}','values[0]=9')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
                                           'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        relevant=[error for error in errors if 'read-only slice' in error['message']]
        self.assertTrue(relevant,errors)
        self.assertEqual(relevant[0]['range']['start'],self.position(changed,'values[0]=9',0),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))

    def test_scalar_generic_copies_do_not_get_slice_mutation_hints(self):
        text='package main\ncopy_value::proc(value:$T)->T{local:=value;return local}\nmain::proc(){_=copy_value(7)}\n'
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        self.assertFalse(any('#mut' in hint['label'] for hint in self.hints(client,path)))

    def test_array_component_readers_and_foreign_results_recover_unsaved(self):
        text='''// 😀 original source
package main
import rl "vendor:raylib"
read::proc(values:[]rl.Vector2)->int{return int(values[0].x)}
main::proc(){
    using rl
    position :> GetMousePosition()
    point:=[int(position.x),int(position.y)]
    rows :> []rl.Vector2{{1,2}}
    _=read(rows);_=point
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hints=self.hints(client,path)
        self.assertFalse(any('#mut' in hint['label'] for hint in hints),hints)
        broken=text.replace('return int(values[0].x)','values[0].x=9;return int(values[0].x)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
            'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(broken,'rows);',0) for error in errors),errors)
        self.assertTrue(any(note['location']['range']['start']==self.position(broken,'values[0].x=9',0)
                            for error in errors for note in error.get('relatedInformation',[])),errors)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':3},
            'contentChanges':[{'text':text}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_native_multiple_scalar_results_are_not_readonly_slice_aliases(self):
        text='''package main
import "core:sys/info"
main::proc(){_,cores :> info.cpu_core_count() or_panic;copy:=cores;_=copy}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hover=self.hover(client,path,text,'copy:=')['contents']['value']
        self.assertIn(': int',hover)

    def test_buffer_formatting_contracts_and_unsaved_destination_recovery(self):
        text='''// 😀 buffer formatting
package main
import "core:fmt"
format::proc(buffer:[]byte,values:[]int)->string{return fmt.bprintf(buffer,"%v",values)}
main::proc(){buffer :> []byte{0,0,0,0};values :> []int{7};_=format(buffer,values)}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(text,'buffer,values)}',0) for error in errors),errors)
        hints=[hint for hint in self.hints(client,path) if '#mut' in hint['label']]
        self.assertEqual(len(hints),1,hints)
        self.assertEqual(hints[0]['position'],self.position(text,'buffer:[]byte',0))
        corrected=text.replace('buffer :>','buffer :=')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
            'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_switch_and_comprehension_argument_errors_use_original_utf16_locations(self):
        for expression in ['switch change(values){case 2:7;case:8}',
                           'switch 2{case change(values):7;case:8}',
                           'map[int]int{change(values)=x for x in 0..<1}']:
            with self.subTest(expression=expression):
                text='// 😀\npackage main\nchange::proc(data:[]int)->int{data[0]+=1;return data[0]}\nmain::proc(){values :> []int{1};/* 😀 */result:='+expression+';_=result}\n'
                client,path=self.open(text)
                errors=client.diagnostic(path.as_uri(),contains='read-only slice')
                relevant=[error for error in errors if 'read-only slice' in error['message']]
                self.assertEqual(len(relevant),1,errors)
                self.assertEqual(relevant[0]['range'], {
                    'start': self.position(text, 'values)', 0),
                    'end': self.position(text, 'values)', len('values')),
                })
                effect = {
                    'start': self.position(text, 'data[0]+=1', 0),
                    'end': self.position(text, 'data[0]+=1', len('data[0]')),
                }
                self.assertTrue(any(note['location']['range'] == effect and note['message']
                                    for note in relevant[0].get('relatedInformation', [])), errors)
                self.assertNotIn('Thor expression',json.dumps(errors))
                corrected=text.replace('values :>','values :=')
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':corrected}]})
                client.diagnostic(path.as_uri(),empty=True)

    def test_parenthesized_optional_tuple_errors_and_status_hovers_recover_unsaved(self):
        text='''// 😀
package main
pair::proc(values:[]int)->(int,[]int,bool){return 7,values,true}
main::proc(){values :> []int{1};_,view :> (pair(values) or_panic);/* 😀 */view[0]=9}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(text,'view[0]',0) for error in errors),errors)
        corrected=text.replace('view[0]=9','_=view[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        status='package main\nmain::proc(){values,err :> make([]int,1);if err!=nil{panic("allocation")};defer delete(values);copy:=err;_=copy}\n'
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':3},'contentChanges':[{'text':status}]})
        client.diagnostic(path.as_uri(),empty=True)
        hover=self.hover(client,path,status,'err :>')['contents']['value']
        self.assertIn('Allocator_Error',hover)


if __name__=='__main__':unittest.main()
