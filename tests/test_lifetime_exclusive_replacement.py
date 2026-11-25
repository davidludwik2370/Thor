"""Exclusive owner replacement folds generations; aliases and leaks do not."""
import unittest
from tests import test_lifetime_summaries as base


class ExclusiveReplacementTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def problem(self,codes):self.assertTrue(any(c.startswith('thor/lifetime') for c in codes),codes)

    def test_exclusive_slice_reset_keeps_a_live_current_generation(self):
        body='work::proc(flag:bool){v:=make([]int,2);defer delete(v);for _ in 0..<10{if flag{delete(v);v=make([]int,2)};_=v[0]}}\nmain::proc(){work(true)}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.exclusive_replacements',0),0,profile)

    def test_dynamic_owning_helper_and_interleaved_resets(self):
        body='fresh::proc()->[dynamic]int{v:[dynamic]int;append(&v,1);return v}\nwork::proc(flag:bool){a:=fresh();b:=fresh();defer{delete(a);delete(b)};for _ in 0..<10{for &n in a{n+=1};if flag{delete(a);delete(b);a=fresh();b=fresh()};_=a[0]+b[0]}}\nmain::proc(){work(true)}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.exclusive_replacements',0),0,profile)

    def test_fresh_make_return_can_replace_an_exclusive_slice(self):
        self.assertEqual(self.codes('fresh::proc(n:int)->[]int{return make([]int,n)}\nmain::proc(){v:=fresh(2);defer delete(v);for _ in 0..<10{delete(v);v=fresh(2);_=v[0]}}'),[])

    def test_captured_old_alias_is_never_revived_by_replacement(self):
        self.problem(self.codes('main::proc(){v:=make([]int,2);alias:=v[:];for _ in 0..<2{delete(v);v=make([]int,2);_=alias[0]};delete(v)}'))

    def test_nested_fixed_array_slice_and_range_addresses_keep_old_lifetimes(self):
        for declaration,retain in [('v:=make([][2]int,2)','alias:=v[0][:]'),('v:=make([][2]int,2)','alias:[]int;for &row in v{alias=row[:]}')]:
            self.problem(self.codes('main::proc(){'+declaration+';'+retain+';delete(v);v=make([][2]int,2);_=alias[0];delete(v)}'))
        self.problem(self.codes('main::proc(){v:=make([]int,2);alias:^int;for &n in v{alias=&n};delete(v);v=make([]int,2);_=alias^;delete(v)}'))

    def test_reset_with_a_live_range_alias_stays_invalid(self):
        self.problem(self.codes('main::proc(){v:=make([]int,2);for &n in v{delete(v);v=make([]int,2);_=n};delete(v)}'))

    def test_missing_delete_and_conditional_delete_still_report_leaks(self):
        for reset in ['v=make([]int,2)','if flag{delete(v)};v=make([]int,2)']:
            codes=self.codes('work::proc(flag:bool){v:=make([]int,2,allocator=runtime.heap_allocator());for _ in 0..<2{'+reset+'};delete(v)}\nmain::proc(){work(true)}',prefix='import "base:runtime"')
            self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_extra_delete_is_not_hidden_by_a_fresh_generation(self):
        self.problem(self.codes('main::proc(){v:=make([]int,2);for _ in 0..<2{delete(v);delete(v);v=make([]int,2)};delete(v)}'))

    def test_global_and_opaque_retention_prevent_exclusive_proofs(self):
        self.problem(self.codes('saved:[]int\nmain::proc(){v:=make([]int,2);saved=v;delete(v);v=make([]int,2);_=saved[0];delete(v)}'))
        self.assertIn('thor/lifetime-unknown',self.codes('work::proc(callback:proc([]int)){v:=make([]int,2);callback(v);delete(v);v=make([]int,2);delete(v)}\nmain::proc(){}'))

    def test_dropped_nested_owner_and_integer_address_are_not_inline_elements(self):
        for typ,value in [('[]int','make([]int,2)'),('uintptr','uintptr(new(int))')]:
            codes=self.codes('main::proc(){v:=make([]'+typ+',2);v[0]='+value+';delete(v);v=make([]'+typ+',2);delete(v)}')
            self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_borrowed_or_different_allocator_storage_is_not_folded(self):
        self.problem(self.codes('work::proc(input:[]int){v:=input;delete(v);v=make([]int,2);delete(v)}\nmain::proc(){v:=make([]int,2);work(v);_=v[0]}'))


if __name__=='__main__':unittest.main()
