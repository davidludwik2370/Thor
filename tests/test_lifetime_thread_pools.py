"""Pool completion bounds asynchronous borrows; opaque ownership stays visible."""
import unittest
from tests import test_lifetime_summaries as base
class ThreadPoolTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeSummaryTests.setUpClass.__func__)
    setUp=base.LifetimeSummaryTests.setUp
    invoke=base.LifetimeSummaryTests.invoke
    vet=base.LifetimeSummaryTests.vet
    codes=base.LifetimeSummaryTests.codes
    profiled=base.LifetimeSummaryTests.profiled
    prefix='import "core:thread"\n'
    callback='job::proc(t:thread.Task){p:=(^int)(t.data);p^+=1}\n'

    def test_known_callback_and_joined_borrow(self):
        code=self.callback+'main::proc(){p:=new(int);defer free(p);pool:thread.Pool;thread.pool_init(&pool,context.allocator,2);defer thread.pool_destroy(&pool);thread.pool_start(&pool);thread.pool_add_task(&pool,context.allocator,job,p);thread.pool_finish(&pool)}'
        self.assertEqual(self.codes(code,prefix=self.prefix),[])

    def test_payload_freed_before_finish_is_detected(self):
        code=self.callback+'main::proc(){p:=new(int);pool:thread.Pool;thread.pool_init(&pool,context.allocator,2);defer thread.pool_destroy(&pool);thread.pool_start(&pool);thread.pool_add_task(&pool,context.allocator,job,p);free(p);thread.pool_finish(&pool)}'
        self.assertIn('thor/lifetime',self.codes(code,prefix=self.prefix))

    def test_stack_payload_cannot_leave_before_completion(self):
        code=self.callback+'main::proc(){pool:thread.Pool;thread.pool_init(&pool,context.allocator,2);defer thread.pool_destroy(&pool);thread.pool_start(&pool);{n:=0;thread.pool_add_task(&pool,context.allocator,job,&n)};thread.pool_finish(&pool)}'
        self.assertIn('thor/lifetime',self.codes(code,prefix=self.prefix))

    def test_destroy_requires_finished_workers(self):
        self.assertIn('thor/lifetime',self.codes('main::proc(){pool:thread.Pool;thread.pool_init(&pool,context.allocator,2);thread.pool_start(&pool);thread.pool_destroy(&pool)}',prefix=self.prefix))

    def test_runtime_callbacks_and_retaining_callbacks_stay_unverified(self):
        for callback,decl in [('callback',''),('job','saved:rawptr\njob::proc(t:thread.Task){saved=t.data}\n')]:
            code=decl+'work::proc(callback:thread.Task_Proc){pool:thread.Pool;thread.pool_init(&pool,context.allocator,2);defer thread.pool_destroy(&pool);thread.pool_start(&pool);n:=0;thread.pool_add_task(&pool,context.allocator,'+callback+',&n);thread.pool_finish(&pool)}\nmain::proc(){}'
            self.assertIn('thor/lifetime-unknown',self.codes(code,prefix=self.prefix))

    def test_pool_alias_calls_and_copied_pool_are_distinct(self):
        code='start::thread.pool_start\nfinish::thread.pool_finish\nmain::proc(){pool:thread.Pool;thread.pool_init(&pool,context.allocator,1);start(&pool);finish(&pool);thread.pool_destroy(&pool)}'
        self.assertEqual(self.codes(code,prefix=self.prefix),[])
        code='main::proc(){pool:thread.Pool;thread.pool_init(&pool,context.allocator,1);copy:=pool;thread.pool_destroy(&copy);thread.pool_destroy(&pool)}'
        self.assertIn('thor/lifetime-unknown',self.codes(code,prefix=self.prefix))

    def test_scheduling_time_boolean_cannot_hide_a_cleanup_branch(self):
        code='Job::struct{release:bool}\njob::proc(t:thread.Task){p:=(^Job)(t.data);if !p.release{free(p)}}\nmain::proc(){p:=new(Job);p.release=true;pool:thread.Pool;thread.pool_init(&pool,context.allocator,1);defer thread.pool_destroy(&pool);thread.pool_start(&pool);thread.pool_add_task(&pool,context.allocator,job,p);p.release=false;thread.pool_finish(&pool);free(p)}'
        self.assertIn('thor/lifetime-unknown',self.codes(code,prefix=self.prefix))

    def test_nil_payload_is_allowed_only_when_data_is_not_used(self):
        setup='main::proc(){pool:thread.Pool;thread.pool_init(&pool,context.allocator,1);defer thread.pool_destroy(&pool);thread.pool_start(&pool);thread.pool_add_task(&pool,context.allocator,job,nil);thread.pool_finish(&pool)}'
        self.assertEqual(self.codes('job::proc(_:thread.Task){}\n'+setup,prefix=self.prefix),[])
        self.assertIn('thor/lifetime-unknown',self.codes(self.callback+setup,prefix=self.prefix))

    def test_generic_callback_alias_and_reordered_named_arguments(self):
        code='job::proc(t:$T){p:=(^int)(t.data);p^+=1}\ncallback::job\nmain::proc(){p:=new(int);defer free(p);pool:thread.Pool;thread.pool_init(thread_count=1,pool=&pool,allocator=context.allocator);defer thread.pool_destroy(&pool);thread.pool_start(&pool);thread.pool_add_task(data=p,procedure=callback,allocator=context.allocator,pool=&pool);thread.pool_finish(&pool)}'
        self.assertEqual(self.codes(code,prefix=self.prefix),[])

if __name__=='__main__':unittest.main()
