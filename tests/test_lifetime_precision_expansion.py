"""Bounded precision proofs retain genuine reference and cleanup effects."""
import unittest
from tests import test_lifetime_summaries as base

class PrecisionExpansionTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def test_field_only_record_helpers_are_cached(self):
        code='Box::struct{number:int,unused:^int}\nread::proc(b:Box)->int{return b.number}\nmain::proc(){guard~:=new(int,allocator=context.allocator);p:=new(int);b:=Box{7,p};free(p);'+''.join('_=read(b);'for _ in range(100))+'}'
        fs,profile=self.profiled(code)
        self.assertEqual(fs,[])
        self.assertGreater(profile['counters'].get('lifetime.projection_summary_applied',0),90,profile)

    def test_nested_projection_named_forwarding_and_generic_alias(self):
        code='Box::struct($T:typeid){n:T,p:^int}\nOuter::struct{b:Box(int)}\nread::proc(x:$T)->int{return x.b.n}\nalias::read\nforward::proc(x:Outer)->int{return alias(x=x)}\nmain::proc(){p:=new(int);x:=Outer{{7,p}};free(p);_=forward(x)}'
        self.assertEqual(self.codes(code),[])

    def test_used_reference_fields_are_not_erased(self):
        code='Box::struct{n:int,p:^int}\nread::proc(b:Box)->int{return b.p^}\nmain::proc(){p:=new(int);b:=Box{7,p};free(p);_=read(b)}'
        self.assertIn('thor/lifetime',self.codes(code))

    def test_reference_stores_and_returned_fields_are_not_summarized_away(self):
        for body in ['saved=b.p','_=b.p^']:
            code='Box::struct{n:int,p:^int}\nsaved:^int\nread::proc(b:Box){'+body+'}\nmain::proc(){p~:=new(int);read({7,p})}'
            fs=self.vet(code)
            if body.startswith('saved'):self.assertTrue(any(f['code'].startswith('thor/lifetime')for f in fs),fs)
        self.assertIn('thor/lifetime',self.codes('Box::struct{p:^int}\nget::proc(b:Box)->^int{return b.p}\nmain::proc(){p:=new(int);b:=Box{p};v:=get(b);free(p);_=v^}'))

    def test_encoded_addresses_in_used_inline_fields_keep_provenance(self):
        code='Box::struct{bits:uintptr,unused:^int}\nread::proc(b:Box)->uintptr{return b.bits}\nsaved:uintptr\nwork::proc(){p~:=new(int);saved=read({uintptr(p),nil})}\nmain::proc(){work()}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes('saved:uintptr\nwork::proc(){p~:=new(int);saved=uintptr(p)+0}\nmain::proc(){work()}')))

    def test_completed_child_scratch_does_not_expand_loop_state(self):
        code='helper::proc()->int{a:=[1,2];p:=&a[0];return p^}\nmain::proc(){n:=0;for _ in 0..<1000{n+=helper()};_=n}'
        self.assertEqual(self.codes(code),[])

    def test_native_when_defers_and_locals_use_the_enclosing_scope(self):
        for choice in ['true','false']:
            code='main::proc(){when '+choice+'{p:=new(int);defer free(p)}else{p:=new(int);defer free(p)};p^=7;_=p^}'
            self.assertEqual(self.codes(code),[])
        self.assertIn('thor/lifetime',self.codes('main::proc(){view:^int;{when true{p~:=new(int);view=p}};_=view^}'))
        self.assertIn('thor/lifetime',self.codes('main::proc(){when true{p:=new(int);defer free(p)};free(p)}'))
        self.assertIn('thor/lifetime',self.codes('main::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);when true{context.allocator=virtual.arena_allocator(&arena)};p:=new(int);virtual.arena_free_all(&arena);_=p^}',prefix='import "core:mem/virtual"'))

    def test_empty_slice_literals_have_no_borrowed_stack_allocation(self):
        for typ in ['int','u8','^int']:
            code='empty::proc()->[]'+typ+'{return []'+typ+'{}}\nmain::proc(){v:=empty();_=v}'
            self.assertEqual(self.codes(code),[],code)
        self.assertEqual(self.codes('empty::proc(out:^[]int){out^=[]int{}}\nmain::proc(){v:[]int;empty(&v);_=v}'),[])
        self.assertIn('thor/lifetime',self.codes('identity::proc(v:[]int)->[]int{return v}\nbad::proc()->[]int{return identity([]int{7})}\nmain::proc(){v:=bad();_=v[0]}'))
        self.assertIn('thor/lifetime',self.codes('head::proc(v:[]int)->[]int{return v[:0]}\nbad::proc()->[]int{v:=[]int{7};return head(v)}\nmain::proc(){v:=bad();_=v}'))

    def test_destroyed_caller_record_does_not_export_its_dead_contents(self):
        declaration='Box::struct{items:[]int}\ndestroy::proc(b:^Box){delete(b.items);free(b)}\n'
        self.assertEqual(self.codes(declaration+'main::proc(){}'),[])
        self.assertEqual(self.codes(declaration+'main::proc(){b:=new(Box);b.items=make([]int,2);destroy(b)}'),[])
        self.assertIn('thor/lifetime',self.codes(declaration+'main::proc(){b:=new(Box);b.items=make([]int,2);destroy(b);_=b.items}'))
        self.assertIn('thor/lifetime',self.codes('Box::struct{items:[]int}\ndestroy::proc(b:^Box){delete(b.items)}\nmain::proc(){}'))
        self.assertIn('thor/leak',self.codes('Box::struct{items:[]int}\ndestroy::proc(b:^Box){b.items=make([]int,2,allocator=runtime.heap_allocator());free(b)}\nmain::proc(){}',prefix='import "base:runtime"'))

    def test_descendant_views_and_pending_caller_arguments_are_not_discarded(self):
        code='bad::proc()->^int{a:=7;return (^int)(rawptr(&a))}\nloop::proc()->int{n:=0;for _ in 0..<10{a:=3;n+=(&a)^};return n}\nread::proc(p:^int,n:int)->int{return p^+n}\nmain::proc(){_=read(bad(),loop())}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_private_failed_allocation_returned_as_nil_is_not_a_freed_result(self):
        code='get::proc(ok:bool)->^int{p:=new(int);if !ok{free(p);return nil};return p}\nwork::proc(ok:bool){p:=get(ok);if p!=nil{_=p^;free(p)}}\nmain::proc(){work(true);work(false)}'
        self.assertFalse(any('released' in str(f['msgs'])for f in self.vet(code)))

    def test_failure_cleanup_cannot_erase_retained_or_returned_dangling_alias(self):
        for result,store in [('p',''),('nil','saved=p;')]:
            code='saved:^int\nget::proc(ok:bool)->^int{p:=new(int);if !ok{'+store+'free(p);return '+result+'};return p}\nmain::proc(){p:=get(false);_=p;_=saved}'
            self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_two_exclusive_swapped_headers_keep_distinct_owners(self):
        code='swap::proc(a,b:^$T){a^,b^=b^,a^}\nmain::proc(){a:=make([dynamic]int,1,2);b:=make([dynamic]int,0,2);defer delete(a);defer delete(b);for _ in 0..<8{for v in a{append(&b,v)};swap(&a,&b);resize(&b,0)}}'
        fs,p=self.profiled(code)
        self.assertEqual(fs,[])
        self.assertGreater(p['counters'].get('lifetime.header_pair_normalized',0),0,p)

    def test_swapped_header_views_and_fake_swap_remain_visible(self):
        for body in ['a^,b^=b^,a^','b^=a^']:
            code='swap::proc(a,b:^$T){'+body+'}\nmain::proc(){a:=make([dynamic]int,1,2);b:=make([dynamic]int,0,2);view:=a[:];defer delete(a);defer delete(b);for _ in 0..<8{append(&b,7);swap(&a,&b);resize(&b,0)};_=view[0]}'
            self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code)))

    def test_demand_projections_expand_only_borrowed_parameter_shapes(self):
        code='A::struct{values:[][][][]int}\nread::proc(a:^A)->int{return a.values[0][0][0][0]}\nmain::proc(){}'
        fs,p=self.profiled(code)
        self.assertEqual(fs,[])
        self.assertGreater(p['counters'].get('lifetime.demand_expansions',0),0,p)

    def test_demand_projections_do_not_revive_concrete_freed_buffers(self):
        code='A::struct{values:[][][][]int}\nread::proc(a:^A)->int{return a.values[0][0][0][0]}\nmain::proc(){v:=make([]int,1);a:=A{[][][][]int{[][][]int{[][]int{v}}}};delete(v);_=read(&a)}'
        self.assertIn('thor/lifetime',self.codes(code))

if __name__=='__main__':unittest.main()
