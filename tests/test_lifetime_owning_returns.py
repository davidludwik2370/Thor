"""Nil/allocated branches can return a monotonic owner, never a dropped owner."""
import unittest
from tests import test_lifetime_vet as base

class LifetimeOwningReturnTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def test_pure_monotonic_dynamic_returns_are_owners(self):
        for declaration,ret in [('v:[dynamic]int','return v'),('','return')]:
            sig='[dynamic]int' if declaration else '(v:[dynamic]int)'
            self.assertEqual(self.codes('load::fn(n:int)->'+sig+'{'+declaration+';for i in 0..<n{append(&v,i)};'+ret+'}\nmain::proc(){v:=load(2);_=v[0];delete(v)}'),[])

    def test_procedure_multi_results_keep_the_owning_result(self):
        self.assertEqual(self.codes('load::proc(n:int)->(v:[dynamic]int,ok:bool){for i in 0..<n{append(&v,i)};return v,true}\nmain::proc(){v,ok:=load(2);_=ok;delete(v)}'),[])

    def test_header_reassignment_does_not_receive_an_ownership_proof(self):
        codes=self.codes('flag:bool\nbad::proc()->[dynamic]int{v:[dynamic]int;append(&v,1);if flag{v=nil};return v}\nmain::proc(){}')
        self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_unreturned_owner_branch_remains_reported(self):
        codes=self.codes('flag:bool\nbad::proc()->[dynamic]int{v:[dynamic]int;append(&v,1);if flag{return nil};return v}\nmain::proc(){}')
        self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_returning_a_cleaned_header_remains_a_lifetime_issue(self):
        codes=self.codes('bad::proc()->[dynamic]int{v:[dynamic]int;append(&v,1);delete(v);return v}\nmain::proc(){}')
        self.assertTrue(any(c.startswith('thor/lifetime') for c in codes),codes)

    def test_reference_elements_do_not_gain_a_shallow_cleanup_proof(self):
        codes=self.codes('load::proc(n:int)->[dynamic][]int{v:[dynamic][]int;for i in 0..<n{append(&v,make([]int,2))};return v}\nmain::proc(){v:=load(2);delete(v)}')
        self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_opaque_header_mutation_is_not_assumed_monotonic(self):
        codes=self.codes('load::proc(n:int,change:proc(v:^[dynamic]int))->[dynamic]int{v:[dynamic]int;append(&v,1);change(&v);return v}\nmain::proc(){}')
        self.assertIn('thor/lifetime-unknown',codes)

if __name__=='__main__':unittest.main()
