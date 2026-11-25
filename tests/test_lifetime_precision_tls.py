"""Memory proof diagnostics keep full user ranges and recover after edits."""
import unittest
from tests import test_lifetime_contracts_tls as base
class PrecisionTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeContractsTLSTests.setUpClass.__func__)
    open=base.LifetimeContractsTLSTests.open
    position=staticmethod(base.LifetimeContractsTLSTests.position)
    change=base.LifetimeContractsTLSTests.change
    def test_typed_raw_access_range_and_unsaved_recovery(self):
        text='// 😀\npackage main\nimport "core:mem"\nmain::proc(){p,_:=mem.alloc(4);defer free(p);_=((^u64)(p))^}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime')
        d=next(d for d in ds if 'byte extent'in d['message'])
        expression='((^u64)(p))^'
        self.assertEqual(d['range']['start'],self.position(text,expression,0))
        self.assertEqual(d['range']['end'],self.position(text,expression,len(expression)))
        self.change(client,path,text.replace('mem.alloc(4)','mem.alloc(8)'))
        client.diagnostic(path.as_uri(),empty=True)
    def test_partial_initialization_and_recovery(self):
        text='package main\nimport "core:mem"\nmain::proc(){b,_:=mem.alloc_bytes_non_zeroed(8);defer delete(b);p:=(^u64)(raw_data(b));_=p^}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any('uninitialized heap'in d['message']for d in ds),ds)
        self.change(client,path,text.replace('_=p^','p^=7;_=p^'))
        client.diagnostic(path.as_uri(),empty=True)
    def test_byte_transport_warns_on_later_interpretation_and_recovers(self):
        text='package main\nimport "core:mem"\nmain::proc(){a,_:=mem.alloc_bytes_non_zeroed(8);defer delete(a);b:=make([]u8,8);defer delete(b);mem.copy(raw_data(b),raw_data(a),8);_=((^u64)(raw_data(b)))^}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        diagnostics=client.diagnostic(path.as_uri(),code='thor/lifetime')
        reads=[d for d in diagnostics if 'uninitialized heap'in d['message']]
        self.assertEqual(len(reads),1,diagnostics)
        expression='((^u64)(raw_data(b)))^'
        self.assertEqual(reads[0]['range']['start'],self.position(text,expression,0))
        self.assertEqual(reads[0]['range']['end'],self.position(text,expression,len(expression)))
        self.change(client,path,text.replace(';_=((^u64)', ';mem.zero(raw_data(b),8);_=((^u64)'))
        client.diagnostic(path.as_uri(),empty=True)
    def test_temporary_generation_and_recovery(self):
        text='package main\nimport "core:fmt"\nmain::proc(){s:=fmt.tprint(7);free_all(context.temp_allocator);fmt.println(s)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime-unknown')
        self.assertTrue(any('storage'in d['message']or'lifetime'in d['message']for d in ds),ds)
        self.change(client,path,text.replace('free_all(context.temp_allocator);fmt.println(s)','fmt.println(s);free_all(context.temp_allocator)'))
        client.diagnostic(path.as_uri(),empty=True)
    def test_unused_record_pointer_does_not_block_field_summary(self):
        text='package main\nBox::struct{n:int,p:^int}\nread::proc(b:Box)->int{return b.n}\nmain::proc(){p:=new(int);b:=Box{7,p};free(p);_=read(b)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        self.change(client,path,text.replace('return b.n','return b.p^'))
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(ds)
    def test_native_worker_borrow_and_recovery(self):
        text='package main\nimport "core:thread"\njob::proc(p:rawptr){_=((^int)(p))^}\nmain::proc(){p:=new(int);t:=thread.create_and_start_with_data(p,job);free(p);thread.destroy(t)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime-unknown')
        self.assertTrue(any('native thread'in d['message']for d in ds),ds)
        self.change(client,path,text.replace('free(p);thread.destroy(t)','thread.destroy(t);free(p)'))
        client.diagnostic(path.as_uri(),empty=True)
    def test_when_branch_storage_uses_enclosing_scope(self):
        text='package main\nmain::proc(){when true{p:=new(int);defer free(p)};p^=7;_=p^}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        self.change(client,path,text.replace(';p^=7',';free(p);p^=7'))
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any('lifetime'in d['message']or'released'in d['message']for d in ds),ds)
    def test_immutable_for_post_highlights_update_and_recovers(self):
        text='package main\nmain::proc(){for index:>0;index<2;index+=1{}}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        ds=client.diagnostic(path.as_uri(),contains="immutable storage 'index'")
        error=next(d for d in ds if "immutable storage 'index'"in d['message'])
        self.assertEqual(error['range']['start'],self.position(text,'index+=1',0))
        self.assertEqual(error['range']['end'],self.position(text,'index+=1',len('index')))
        self.change(client,path,text.replace('index:>0','index:=0'))
        client.diagnostic(path.as_uri(),empty=True)
    def test_empty_slice_return_has_no_false_borrow_and_recovers(self):
        text='package main\nempty::proc()->[]int{return []int{}}\nmain::proc(){v:=empty();_=v}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        self.change(client,path,text.replace('[]int{}','[]int{7}'))
        client.diagnostic(path.as_uri(),contains='unsafe to return')
        self.change(client,path,text,3)
        client.diagnostic(path.as_uri(),empty=True)
    def test_native_layout_literal_has_hover_and_type_errors_recover(self):
        text='package main\nmain::proc(){row:=#row_major matrix[2,2]f32{1,2,3,4};_=row[0,0]}\n'
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hover=client.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':self.position(text,'row:=',1)})
        self.assertIn('matrix',str(hover))
        self.assertIn('f32',str(hover))
        self.change(client,path,text.replace('{1,2,3,4}','{1,false,3,4}'))
        client.diagnostic(path.as_uri(),contains='bool')
        self.change(client,path,text,3)
        client.diagnostic(path.as_uri(),empty=True)
    def test_implicit_enum_specialization_retains_type_and_recovers(self):
        text='package main\nE::enum{a=5,b=9}\nunrelated::fn(c:byte)->bool{return c==0}\nmain::proc(){check::proc($e:E){when e==.a{value::int(e);if value!=5{panic("enum")}}};check(.a)}\n'
        client,path=self.open(text,checker_args='--comptime=required')
        client.diagnostic(path.as_uri(),empty=True)
        hover=client.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':self.position(text,'$e:E',1)})
        self.assertIn('E',str(hover))
        self.change(client,path,text.replace('check(.a)','check(.missing)'))
        client.diagnostic(path.as_uri(),contains='missing')
        self.change(client,path,text,3)
        client.diagnostic(path.as_uri(),empty=True)
    def test_required_import_is_preserved_by_unused_diagnostics_and_actions(self):
        text='package main\n@(require) import "core:thread"\nmain::proc(){}\n'
        client,path=self.open(text,unused_imports=True,checker_args='--vet=unused')
        client.diagnostic(path.as_uri(),empty=True)
        actions=client.request('textDocument/codeAction',{'textDocument':{'uri':path.as_uri()},
            'range':{'start':{'line':0,'character':0},'end':{'line':3,'character':0}},'context':{'diagnostics':[]}})
        self.assertNotIn('remove unused import',str(actions).lower())
        self.change(client,path,text.replace('@(require) ',''))
        client.diagnostic(path.as_uri(),contains='unused import')
if __name__=='__main__':unittest.main()
