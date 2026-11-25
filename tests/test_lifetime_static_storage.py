"""Persistent native static slots must not become fresh local slots per call."""
import unittest
from tests import test_lifetime_vet as base

class LifetimeStaticStorageTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def test_static_literal_backing_outlives_the_call(self):
        self.assertEqual(self.codes('get::proc()->[]int{@static v:>[]int{1,2};return v}\nmain::proc(){v:>get();_=v[0];_=get()[1]}'),[])

    def test_static_pointer_retention_survives_repeated_calls(self):
        codes=self.codes('get::proc(p:^int)->^int{@static saved:^int;old:=saved;saved=p;return old}\nmain::proc(){p:=new(int);_=get(p);free(p);_=get(nil)^}')
        self.assertIn('thor/lifetime',codes)

    def test_static_headers_retain_scoped_references(self):
        codes=self.codes('stash::proc(p:^int){@static saved:^int;saved=p}\nmain::proc(){p~:=new(int);stash(p)}')
        self.assertIn('thor/lifetime',codes)

    def test_generic_static_slots_do_not_overlap(self):
        codes=self.codes('saved::proc(p:^$T)->^T{@static value:^T;old:=value;value=p;return old}\nmain::proc(){a:=new(int);b:=new(f64);_=saved(a);_=saved(b);_=saved((^int)(nil));free(a);_=saved((^f64)(nil))^;free(b)}')
        self.assertEqual(codes,[])

if __name__=='__main__':unittest.main()
