"""Allocator identity is checked only where the release API selects one."""
import unittest
from tests import test_lifetime_summaries as base
class AllocatorMismatchTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    prefix='import "base:runtime"\nimport "core:mem/virtual"\n'
    setup='arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);a:=virtual.arena_allocator(&arena);'

    def test_wrong_pointer_and_slice_allocator(self):
        for alloc,release in [('new(int,allocator=a)','free(p,runtime.heap_allocator())'),('make([]int,2,allocator=a)','delete(p,allocator=runtime.heap_allocator())')]:
            fs=self.vet('main::proc(){'+self.setup+'p:='+alloc+';'+release+'}',prefix=self.prefix)
            self.assertTrue(any(f['code']=='thor/lifetime' and 'allocator does not match' in str(f['msgs'])for f in fs),fs)

    def test_matching_explicit_allocator_and_alias(self):
        self.assertEqual(self.codes('release::free\nmain::proc(){'+self.setup+'p:=new(int,allocator=a);release(allocator=a,ptr=p)}',prefix=self.prefix),[])

    def test_dynamic_array_uses_embedded_allocator(self):
        self.assertEqual(self.codes('main::proc(){'+self.setup+'p:=make([dynamic]int,2,allocator=a);context.allocator=runtime.heap_allocator();delete(p)}',prefix=self.prefix),[])

    def test_distinct_local_arenas_are_distinct_allocators(self):
        code='main::proc(){'+self.setup+'other:virtual.Arena;virtual.arena_init_growing(&other) or_panic;defer virtual.arena_destroy(&other);b:=virtual.arena_allocator(&other);p:=new(int,allocator=a);free(p,b)}'
        self.assertIn('thor/lifetime',self.codes(code,prefix=self.prefix))

    def test_opaque_allocator_does_not_invent_a_definite_mismatch(self):
        fs=self.vet('work::proc(a:runtime.Allocator){p:=new(int,allocator=a);free(p)}\nmain::proc(){}',prefix=self.prefix)
        self.assertFalse(any('allocator does not match' in str(f['msgs']) and f['certainty']=='definite'for f in fs),fs)

    def test_defer_and_scoped_cleanup_capture_the_registration_context(self):
        for declaration in ['p:=new(int);defer free(p);','p~:=new(int);']:
            self.assertEqual(self.codes('main::proc(){'+self.setup+'context.allocator=a;'+declaration+'context.allocator=runtime.heap_allocator()}',prefix=self.prefix),[])

    def test_immediate_cleanup_still_uses_current_context(self):
        fs=self.vet('main::proc(){'+self.setup+'context.allocator=a;p:=new(int);context.allocator=runtime.heap_allocator();free(p)}',prefix=self.prefix)
        self.assertTrue(any('allocator does not match'in str(f['msgs'])for f in fs),fs)

    def test_explicit_standard_slice_cleanup_checks_selected_allocator(self):
        fs=self.vet('main::proc(){'+self.setup+'p:=make([]u8,3,allocator=a);mem.delete_slice(p,allocator=runtime.heap_allocator())}',prefix=self.prefix+'import "core:mem"\n')
        self.assertTrue(any('allocator does not match'in str(f['msgs'])for f in fs),fs)

    def test_soa_dynamic_array_uses_its_embedded_allocator(self):
        code='Person::struct{n:int}\nmain::proc(){'+self.setup+'p:=make(#soa[dynamic]Person,2,allocator=a);context.allocator=runtime.heap_allocator();delete(p)}'
        self.assertEqual(self.codes(code,prefix=self.prefix),[])

    def test_field_expression_is_a_positional_cleanup_target(self):
        for release in ['free(holder.pointer,a)','free(holder.pointer,runtime.heap_allocator())']:
            fs=self.vet('Box::struct{pointer:^int}\nmain::proc(){'+self.setup+'holder:=Box{new(int,allocator=a)};'+release+';_=holder.pointer^}',prefix=self.prefix)
            self.assertIn('thor/lifetime',[f['code']for f in fs],fs)
            mismatch=any('allocator does not match'in str(f['msgs'])for f in fs)
            self.assertEqual(mismatch,'heap_allocator' in release,fs)

if __name__=='__main__':unittest.main()
