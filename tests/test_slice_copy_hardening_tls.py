"""Live copy-boundary diagnostics, tokens and inferred contracts."""
import unittest

from tests import test_tls as base


class SliceCopyHardeningTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans

    def test_generic_scalar_result_declarations_keep_their_resolved_types(self):
        text='''package main
Id::distinct int
Status::enum{ok}
Box::struct($T:typeid){id:Id,value:T}
attach::proc(box:^Box($T))->(Id,Status){return box.id,.ok}
prepare::proc(box:^Box($T)){
    id,status :> attach(box)
    copy:=id
    box.id=copy
    _=status
}
main::proc(){values:=[1]int{7};box:=Box([]int){Id(9),values[:]};prepare(&box)}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hover=self.hover(client,path,text,'id,status')['contents']['value']
        self.assertIn('Id',hover)

    def test_void_output_view_permissions_report_the_caller_write_and_recover(self):
        text='''// 😀 original source
package main
Row::struct{items:[]int}
fill::proc(out:^Row){view :> []int{7};out^=Row{view}}
forward::proc(out:^Row){fill(out)}
main::proc(){row:Row;forward(&row);/* 😀 */row.items[0]=9}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(text,'row.items[0]=9',0)
                            for error in errors),errors)
        corrected=text.replace('row.items[0]=9','_=row.items[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
                    'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_union_output_views_keep_caller_locations_and_unsaved_recovery(self):
        text='''// 😀 original source
package main
Row::struct{items:[]int}
Other::struct{count:int}
Choice::union{Row,Other}
publish::proc(out:^Choice,input:Choice){
 switch value in input {case Row:out^=value;case Other:out^=value}
}
main::proc(){
 input:>Choice(Row{[]int{7}})
 out:Choice;publish(&out,input)
 switch value in out {case Row:/* 😀 */value.items[0]=9;case Other:}
}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(text,'value.items[0]=9',0)
                            for error in errors),errors)
        corrected=text.replace('value.items[0]=9','_=value.items[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
                    'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_soa_row_comparisons_and_unsaved_errors_keep_original_locations(self):
        text='''// 😀 original source
package main
import "thor:basic"
main::proc(){
    numbers:=[2]int{1,2};words:=[2]string{"one","two"}
    values:=basic.zip(number=numbers[:],word=words[:])
    for &row,index in values {
        if row.number!=index+1 || row.word!=words[index] {panic("SOA comparison")}
        row.number+=10
    }
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        broken=text.replace('index+1','index+missing')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},
                    'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(path.as_uri(),contains='Undeclared name: missing')
        self.assertTrue(any(error['range']['start']==self.position(broken,'missing',0)
                            for error in errors),errors)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':3},
                    'contentChanges':[{'text':text}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_inline_copies_have_mutable_properties_and_readonly_nested_views(self):
        text='''// 😀 original source
package main
import "core:c"
Row::struct{count:int,items:[]int}
main::proc(){
    rows :> []Row{{7,[]int{1}}}
    _=rows[0].count
    copied:=rows[0]
    copied.count=9
    _=copied.items[0]
    copied.items=make([]int,1)
    copied.items[0]=9
    width :> c.long(7)
    number:=width
    _=number
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,path,text)
        properties={start:(word,bool(flags&readonly)) for start,_,kind,word,flags in spans if kind=='property'}
        for needle,expected in [('rows[0].count',True),('copied.count=9',False)]:
            offset=len(text[:text.index(needle)+needle.index('count')].encode())
            self.assertEqual(properties.get(offset),('count',expected),properties)
        hover=self.hover(client,path,text,'copied.count=9')['contents']['value']
        self.assertIn('Row',hover)

    def test_default_slice_parameter_reports_local_violation_and_recovers_unsaved(self):
        text='''// 😀 input
package main
use::proc(values:=[]int{7}){view :> values;/* 😀 */view[0]=9}
main::proc(){use()}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(text,'view[0]=9',0) for error in errors),errors)
        corrected=text.replace('view[0]=9','_=view[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':corrected}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_soa_contracts_are_column_specific_and_keep_original_locations(self):
        text='''// 😀 original source
package main
write::proc(values:#soa[]$T){values.b[0]=9}
read::proc(values:#soa[]$T)->int{row:=values[0];copy:=row.a;return copy}
main::proc(){
    readonly :> []int{1}
    writable:=[]int{2}
    _=read(soa_zip(a=readonly))
    write(soa_zip(a=readonly,b=writable))
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)

        hints=client.request('textDocument/inlayHint',{'textDocument':{'uri':path.as_uri()},
            'range':{'start':{'line':0,'character':0},'end':{'line':30,'character':0}}}) or []
        mut=[hint for hint in hints if '#mut' in hint['label']]
        self.assertEqual(len(mut),1,mut)
        self.assertEqual(mut[0]['position'],self.position(text,'values:#soa',0))
        broken=text.replace('write(soa_zip(a=readonly,b=writable))','write(soa_zip(a=writable,b=readonly))')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(path.as_uri(),contains='read-only slice')
        self.assertTrue(any(error['range']['start']==self.position(broken,'soa_zip(a=writable',0) for error in errors),errors)
        self.assertTrue(any(note['location']['range']['start']==self.position(broken,'values.b[0]=9',0)
                            for error in errors for note in error.get('relatedInformation',[])),errors)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_inferred_mut_does_not_confuse_generic_copies_or_pointer_targets(self):
        text='''package main
Node::struct{x:int}
reader::proc(values:[]$T){copy:=values[0];_=copy}
writer::proc(values:[]$T,value:T){values[0]=value}
pointee::proc(values:[]^Node){values[0].x=9}
main::proc(){node:=Node{1};nodes :> []^Node{&node};pointee(nodes)}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hints=client.request('textDocument/inlayHint',{'textDocument':{'uri':path.as_uri()},
            'range':{'start':{'line':0,'character':0},'end':{'line':20,'character':0}}}) or []
        mut=[hint for hint in hints if '#mut' in hint['label']]
        self.assertEqual(len(mut),1,mut)
        self.assertEqual(mut[0]['position'],self.position(text,'values:[]$T,value:T',0))


if __name__=='__main__':unittest.main()
