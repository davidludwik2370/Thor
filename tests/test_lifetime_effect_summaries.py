"""Graph-neutral scalar writes are cached; reference writes still need tracing."""
import unittest
from tests import test_lifetime_summaries as base

class CachedWriterTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled

    def test_repeated_slice_writers_reuse_verified_effects(self):
        body='fill::proc(v:[]int){for &n in v{n+=1}}\nmain::proc(){v:=make([]int,2,allocator=context.allocator);defer delete(v);'+''.join('fill(v);' for _ in range(100))+'}'
        findings,profile=self.profiled(body)
        self.assertEqual(findings,[])
        self.assertGreater(profile['counters'].get('lifetime.writer_summary_applied',0),90,profile['counters'])

    def test_direct_pointer_record_and_index_writes_recheck_lifetimes(self):
        for code in ['V::struct{x:int}\nwrite::proc(p:^V){p.x=7}\nmain::proc(){p:=new(V);write(p);free(p);write(p)}',
                     'write::proc(v:[]int){v[0]=7}\nmain::proc(){v:=make([]int,2);write(v);delete(v);write(v)}']:
            self.assertIn('thor/lifetime',self.codes(code))

    def test_recursive_scalar_writers_are_verified_together(self):
        self.assertEqual(self.codes('a::proc(p:^int,n:int){if n<=0{return};p^+=1;b(p,n-1)}\nb::proc(p:^int,n:int){if n<=0{return};p^+=1;a(p,n-1)}\nmain::proc(){p:=new(int);a(p,5);free(p)}'),[])

    def test_global_scalar_state_is_not_reference_retention(self):
        self.assertEqual(self.codes('count:int\nstep::proc(n:int)->int{count+=n;return count}\nmain::proc(){v~:=make([]int,2);_=step(1);_=step(2)}'),[])

    def test_reference_valued_global_stores_remain_traced(self):
        self.assertIn('thor/lifetime',self.codes('saved:[]int\nset::proc(v:[]int){saved=v}\nmain::proc(){v~:=make([]int,2);set(v)}'))

    def test_writer_cannot_erase_integer_address_provenance(self):
        codes=self.codes('write::proc(p:^uintptr){p^=0}\nmain::proc(){p:=new(int);bits:=uintptr(p);write(&bits)}')
        self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)

    def test_global_integer_address_reads_bypass_value_summary(self):
        codes=self.codes('bits:uintptr\nread::proc()->uintptr{return bits}\nmain::proc(){p:=new(int);bits=uintptr(p);copy:=read();free(p);_=((^int)(copy))^}')
        self.assertIn('thor/lifetime',codes)

    def test_global_initializer_calls_are_not_skipped(self):
        codes=self.codes('init::proc "contextless"()->int{context=runtime.default_context();v:=make([]int,2,allocator=runtime.heap_allocator());_=v;return 1}\nstate:=init()\nread::proc()->int{return state}\nmain::proc(){_=read()}',prefix='import "base:runtime"')
        self.assertIn('thor/leak',codes)

    def test_disabled_assertion_arguments_are_not_cached_as_reads_or_writes(self):
        body='read::proc(v:[]int){assert(v[0]==1)}\nmain::proc(){v:=make([]int,2);delete(v);read(v)}'
        self.assertIn('thor/lifetime',self.codes(body))
        self.assertEqual(self.codes(body,flags=['-disable-assert']),[])

    def test_static_dead_branches_do_not_require_live_input_storage(self):
        for statement in ['when false{_=v[0]}','if false{_=v[0]}','_=false && v[0]==1','_=true || v[0]==1','_=false?v[0]:0']:
            with self.subTest(statement=statement):
                self.assertEqual(self.codes('read::proc(v:[]int){'+statement+'}\nmain::proc(){v:=make([]int,2);delete(v);read(v)}'),[])

if __name__=='__main__':unittest.main()
