"""Native thread API borrows must survive until a reviewed completion call."""
import unittest
from tests import test_lifetime_summaries as base
class NativeWorkerTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled
    def findings(self,body):
        return self.vet(body,prefix='import "core:thread"')
    def test_borrowed_heap_is_live_through_join_or_destroy(self):
        for end in ['thread.join(t);thread.destroy(t)','thread.destroy(t)']:
            fs=self.findings('job::proc(p:rawptr){_=((^int)(p))^}\nmain::proc(){p:=new(int);t:=thread.create_and_start_with_data(p,job);'+end+';free(p)}')
            self.assertFalse(any('retained by a native thread'in str(f['msgs'])for f in fs),fs)
    def test_cleanup_before_join_is_diagnosed(self):
        fs=self.findings('job::proc(p:rawptr){_=((^int)(p))^}\nmain::proc(){p:=new(int);t:=thread.create_and_start_with_data(p,job);free(p);thread.join(t);thread.destroy(t)}')
        self.assertTrue(any('retained by a native thread'in str(f['msgs'])for f in fs),fs)
    def test_detached_stack_borrow_and_is_done_do_not_prove_completion(self):
        for spawn,end in [('thread.run_with_data(&x,job)',''),('t:=thread.create_and_start_with_data(&x,job)','_=thread.is_done(t)')]:
            fs=self.findings('job::proc(p:rawptr){_=((^int)(p))^}\nmain::proc(){x:=7;'+spawn+';'+end+'}')
            self.assertTrue(any('outlive borrowed stack'in str(f['msgs'])for f in fs),fs)
    def test_known_empty_callback_detached_has_no_borrow(self):
        fs=self.findings('job::proc(){}\nmain::proc(){thread.run(job)}')
        self.assertEqual(fs,[])
    def test_polymorphic_value_payload_is_a_copy_but_pointer_fields_borrow(self):
        code='Job::struct{p:^int}\njob::proc(v:$T){_=v.p^}\nmain::proc(){x:=7;t:=thread.create_and_start_with_poly_data(Job{&x},job);thread.destroy(t)}'
        fs=self.findings(code)
        self.assertFalse(any('outlive borrowed stack'in str(f['msgs'])for f in fs),fs)
        fs=self.findings(code.replace('thread.destroy(t)','_=t'))
        self.assertTrue(any('outlive borrowed stack'in str(f['msgs'])for f in fs),fs)
    def test_suspended_thread_data_is_captured_at_start(self):
        for end,expected in [('thread.destroy(t);free(p)',False),('free(p);thread.destroy(t)',True)]:
            code='job::proc(t:^thread.Thread){_=((^int)(t.data))^}\nmain::proc(){p:=new(int);t:=thread.create(job);t.data=p;thread.start(t);'+end+'}'
            fs=self.findings(code)
            self.assertEqual(any('retained by a native thread'in str(f['msgs'])for f in fs),expected,fs)
    def test_thread_handle_requires_matching_destroy(self):
        fs=self.findings('job::proc(){}\nmain::proc(){t:=thread.create_and_start(job);thread.join(t);free(t)}')
        self.assertTrue(any('matching close/unload'in str(f['msgs'])for f in fs),fs)

    def test_joined_handle_payload_fields_preserve_borrowed_pointer(self):
        for argument,callback,spawn in [('p','job::proc(p:rawptr){}','create_and_start_with_data'),('Job{p}','Job::struct{p:^int}\njob::proc(v:Job){}','create_and_start_with_poly_data')]:
            code=callback+'\nmain::proc(){p:=new(int);t:=thread.'+spawn+'('+argument+',job);thread.join(t);free(p);_=((^int)(t.user_args[0]))^;thread.destroy(t)}'
            fs=self.findings(code)
            self.assertTrue(any(f['code'].startswith('thor/lifetime') for f in fs),fs)

    def test_pool_borrows_keep_freed_generations_distinct(self):
        code='job::proc(t:thread.Task){_=((^int)(t.data))^}\nmain::proc(){pool:thread.Pool;thread.pool_init(&pool,context.allocator,1);thread.pool_start(&pool);for _ in 0..<2{p:=new(int);thread.pool_add_task(&pool,context.allocator,job,p);free(p)};thread.pool_finish(&pool);thread.pool_destroy(&pool)}'
        fs,p=self.profiled('import "core:thread"\n'+code)
        self.assertTrue(any(f['code'].startswith('thor/lifetime') for f in fs),fs)
        self.assertGreater(p['counters'].get('lifetime.retained_generations',0),0,p)

    def test_failed_private_allocation_still_retained_by_detached_worker(self):
        code='job::proc(p:rawptr){_=((^int)(p))^}\nget::proc()->^int{p:=new(int);thread.run_with_data(p,job);free(p);return nil}\nmain::proc(){_=get()}'
        fs=self.findings(code)
        self.assertTrue(any('retained by a native thread'in str(f['msgs'])for f in fs),fs)

    def test_arena_reset_does_not_hide_outstanding_worker_borrow(self):
        code='job::proc(p:rawptr){_=((^int)(p))^}\nmain::proc(){arena:virtual.Arena;virtual.arena_init_growing(&arena) or_panic;defer virtual.arena_destroy(&arena);p:=new(int,allocator=virtual.arena_allocator(&arena));t:=thread.create_and_start_with_data(p,job);virtual.arena_free_all(&arena);thread.destroy(t)}'
        fs=self.vet(code,prefix='import "core:thread"\nimport "core:mem/virtual"')
        self.assertTrue(any('retained by a native thread'in str(f['msgs'])for f in fs),fs)

if __name__=='__main__':unittest.main()
