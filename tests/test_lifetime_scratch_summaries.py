"""Local scratch proofs never publish a retained or unprotected owner."""
import unittest
from tests import test_lifetime_summaries as base


class ScratchSummaryTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def test_stack_views_and_owned_scratch_are_cached(self):
        body='''import "core:fmt"
work::proc(n:int)->int {
 bytes:[32]u8
 view:=bytes[:]
 _=fmt.bprint(view,n)
 p:=new(int)
 defer free(p)
 p^=n
 values~:=make([]int,2)
 values[0]=p^
 return values[0]
}
main::proc(){guard~:=make([]int,1,allocator=context.allocator);'''+''.join('_=work(7);'for _ in range(100))+'}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.summary_applied',0),90,profile)
        self.assertGreater(profile['counters'].get('lifetime.summary_scratch',0),0,profile)

    def test_early_exit_without_registered_cleanup_still_leaks(self):
        findings=self.vet('work::proc(stop:bool)->int{p:=new(int);if stop{return 0};defer free(p);return p^}\nmain::proc(){_=work(true)}')
        self.assertTrue(any(f['code'].startswith('thor/leak')for f in findings),findings)

    def test_global_retention_and_integer_addresses_are_not_scratch(self):
        for decl,store in [('saved:^int','saved=p'),('saved:uintptr','saved=uintptr(p)')]:
            findings=self.vet(decl+'\nwork::proc(){p:=new(int);defer free(p);'+store+'}\nmain::proc(){work()}')
            self.assertTrue(any(f['code'].startswith('thor/lifetime')for f in findings),findings)

    def test_double_cleanup_and_returned_views_stay_visible(self):
        self.assertIn('thor/lifetime',self.codes('work::proc(){p:=new(int);defer free(p);free(p)}\nmain::proc(){work()}'))
        self.assertIn('thor/lifetime',self.codes('work::proc()->[]int{p:=make([]int,2);defer delete(p);return p[:]}\nmain::proc(){_=work()}'))

    def test_alias_cleanup_and_opaque_callback_do_not_get_a_proof(self):
        self.assertIn('thor/lifetime',self.codes('work::proc(){p:=new(int);defer free(p);alias:=p;free(alias)}\nmain::proc(){work()}'))
        self.assertIn('thor/lifetime-unknown',self.codes('work::proc(callback:proc(p:^int)){p~:=new(int);callback(p)}\nmain::proc(){}'))

    def test_generic_scratch_and_alias_recheck_borrowed_inputs(self):
        self.assertIn('thor/lifetime',self.codes('work::proc(v:[]$T)->T{p~:=new(T);p^=v[0];return p^}\nalias::work\nmain::proc(){v:=make([]int,2);_=alias(v);delete(v);_=alias(v)}'))

    def test_fixed_capacity_mutations_have_cached_nonallocating_effects(self):
        code='Deck::[dynamic;8]int\nwork::proc()->int{v:Deck;append(&v,7);n:=pop(&v);clear(&v);return n}\nmain::proc(){guard~:=make([]int,1,allocator=context.allocator);'+''.join('_=work();'for _ in range(100))+'}'
        findings,profile=self.profiled(code)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.summary_applied',0),90,profile)

    def test_reference_elements_and_discarded_nested_owners_are_not_inline(self):
        fs=self.vet('work::proc(){v:[dynamic;2]^int;p:=new(int);append(&v,p);_=pop(&v)}\nmain::proc(){work()}')
        self.assertTrue(any(f['code'].startswith('thor/leak')for f in fs),fs)


if __name__=='__main__':unittest.main()
