"""Loop slots and inline records do not consume reference-depth budgets."""
import unittest
from tests import test_lifetime_summaries as base
class LoopPrecisionTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def test_nested_inline_records_keep_slice_provenance(self):
        self.assertEqual(self.codes('A::struct{b:struct{c:struct{values:[]int}}}\nread::proc(a:A)->int{return a.b.c.values[0]}\nmain::proc(){}'),[])
        self.assertIn('thor/lifetime',self.codes('A::struct{b:struct{c:struct{values:[]int}}}\nread::proc(a:A)->int{return a.b.c.values[0]}\nmain::proc(){v:=make([]int,2);a:A;a.b.c.values=v;delete(v);_=read(a)}'))

    def test_private_dead_address_slots_do_not_accumulate_in_loops(self):
        code='read::proc(p:^int)->int{return p^}\nwork::proc(v:[]int){for n in v{p:=n;_=read(&p)}}\nmain::proc(){v~:=make([]int,10);work(v)}'
        findings,profile=self.profiled(code)
        self.assertEqual(findings,[])

    def test_retained_dead_slot_and_deferred_capture_still_fail(self):
        self.assertIn('thor/lifetime',self.codes('saved:^int\nwork::proc(v:[]int){for n in v{x:=n;saved=&x};_=saved^}\nmain::proc(){v~:=make([]int,2);work(v)}'))
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes('read::proc(p:^int){_=p^}\nmain::proc(){saved:^int;for _ in 0..<2{x:=7;saved=&x};read(saved)}')))

    def test_allocator_metadata_keeps_expired_arena_slots_observable(self):
        code='main::proc(){for _ in 0..<2{saved:runtime.Allocator;{arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);context.allocator=virtual.arena_allocator(&arena);saved=context.allocator};context.allocator=saved;p:=new(int);_=p}}'
        fs=self.vet(code,prefix='import "base:runtime"\nimport "core:mem/virtual"')
        self.assertTrue(any(f['code'].startswith('thor/lifetime')for f in fs),fs)

if __name__=='__main__':unittest.main()
