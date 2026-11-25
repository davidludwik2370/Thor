"""Reuse native diagnostics; model atomic storage effects without a race checker."""
import json
import unittest
from tests import test_lifetime_summaries as base
class NativeMemoryCheckTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled
    def test_existing_nil_initialization_and_atomic_checks_keep_native_errors(self):
        cases=[('-vet-nil-deref','p:^int;fmt.println(p^)','nil'),
               ('-vet-uninitialized','x:int=---;fmt.println(x)','before'),
               ('-vet-atomic-access','_=intrinsics.atomic_add(&global,1);fmt.println(global)','atomically')]
        for flag,body,needle in cases:
            self.source.write_text('package main\nimport "core:fmt"\nimport "base:intrinsics"\nglobal:int\nmain::proc(){'+body+'}\n')
            p=self.invoke('rust','vet',flags=['--vet=lifetime,leak',flag,'-json-errors','-thread-count:1'])
            self.assertEqual(p.returncode,1,p.stdout+p.stderr)
            errors=json.loads(p.stderr)['errors']
            self.assertTrue(any(needle in str(e['msgs']).lower()for e in errors),errors)
            self.assertFalse(any(e.get('code','').startswith('thor/lifetime')for e in errors),errors)
    def test_known_scalar_atomics_are_not_opaque_retaining_calls(self):
        code='x:int\nmain::proc(){intrinsics.atomic_store(&x,8);_=intrinsics.atomic_load(&x);_=intrinsics.atomic_add(&x,2);_,_=intrinsics.atomic_compare_exchange_strong(&x,10,12)}'
        fs,p=self.profiled('import "base:intrinsics"\n'+code)
        self.assertEqual(fs,[])
        self.assertGreater(p['counters'].get('lifetime.atomic_effects',0),3,p)
    def test_atomic_pointer_store_preserves_scoped_escape(self):
        code='saved:^int\nmain::proc(){p~:=new(int);intrinsics.atomic_store(&saved,p)}'
        self.assertTrue(any(c.startswith('thor/lifetime')for c in self.codes(code,prefix='import "base:intrinsics"')))
    def test_atomic_load_after_cleanup_still_warns(self):
        code='main::proc(){p:=new(int);free(p);_=intrinsics.atomic_load(p)}'
        self.assertIn('thor/lifetime',self.codes(code,prefix='import "base:intrinsics"'))
    def test_atomic_store_initializes_only_its_own_heap_range(self):
        for size,expected in [(8,False),(16,True)]:
            code='main::proc(){b,_:=mem.alloc_bytes_non_zeroed('+str(size)+');defer delete(b);p:=([ ^]u64)(raw_data(b));intrinsics.atomic_store(&p[0],7);_=p['+('1'if expected else'0')+']}'
            fs=self.vet(code,prefix='import "base:intrinsics"\nimport "core:mem"')
            self.assertEqual(any('uninitialized heap'in str(f['msgs'])for f in fs),expected,fs)
if __name__=='__main__':unittest.main()
