"""Memory diagnostics retain original call ranges and unsaved recovery."""
import unittest
from tests import test_lifetime_contracts_tls as base
class MemoryTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeContractsTLSTests.setUpClass.__func__)
    open=base.LifetimeContractsTLSTests.open
    position=staticmethod(base.LifetimeContractsTLSTests.position)
    change=base.LifetimeContractsTLSTests.change

    def test_buffer_extent_call_and_recovery(self):
        text='// 😀\npackage main\nimport "core:mem"\nmain::proc(){a:[4]u8;mem.zero(raw_data(a[:]),5)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if 'byte extent'in w['message'])
        needle='mem.zero(raw_data(a[:]),5)'
        self.assertEqual(warning['range']['start'],self.position(text,needle,0))
        self.assertEqual(warning['range']['end'],self.position(text,needle,len(needle)))
        self.change(client,path,text.replace('a[:]),5','a[:]),4'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_termination_alias_and_recovery(self):
        text='package main\nsize::proc(p:cstring)->int{return len(p)}\nalias::size\nmain::proc(){a:=[3]u8{65,66,67};_=alias(cstring(raw_data(a[:])))}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if 'zero terminator'in w['message'])
        self.assertEqual(warning['range']['start'],self.position(text,'alias(cstring',0))
        self.change(client,path,text.replace('65,66,67','65,66,0'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_allocator_mismatch_and_recovery(self):
        text='package main\nimport "base:runtime"\nimport "core:mem/virtual"\nmain::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);a:=virtual.arena_allocator(&arena);p:=new(int,allocator=a);free(p,runtime.heap_allocator())}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if 'allocator does not match'in w['message'])
        self.assertEqual(warning['range']['start'],self.position(text,'free(p,runtime',0))
        self.change(client,path,text.replace('free(p,runtime.heap_allocator())','free(p,a)'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_pool_payload_lifetime_and_recovery(self):
        text='package main\nimport "core:thread"\njob::proc(t:thread.Task){p:=(^int)(t.data);p^+=1}\nmain::proc(){p:=new(int);pool:thread.Pool;thread.pool_init(&pool,context.allocator,1);defer thread.pool_destroy(&pool);thread.pool_start(&pool);thread.pool_add_task(&pool,context.allocator,job,p);free(p);thread.pool_finish(&pool)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any('lifetime'in w['message'] or 'storage'in w['message']for w in warnings))
        self.change(client,path,text.replace('free(p);thread.pool_finish(&pool)','thread.pool_finish(&pool);free(p)'))
        client.diagnostic(path.as_uri(),empty=True)

if __name__=='__main__':unittest.main()
