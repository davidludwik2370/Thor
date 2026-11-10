"""Prepared scalar reuse keeps reference invalidation boundaries intact."""
import unittest
import shutil
from tests import test_rust_cached_execution as cached
from tests import test_rust_compiler as native


class RustPreparedExecutionTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(cached.RustCachedExecutionTests.setUpClass.__func__)
    setUp=cached.RustCachedExecutionTests.setUp
    write=cached.RustCachedExecutionTests.write
    def invoke(self,**kwargs):
        # Exercise the intermediate prepared-key policy independently of the
        # still earlier verified-site lookup.
        shutil.rmtree(self.cache/'scalar-v1',ignore_errors=True)
        return cached.RustCachedExecutionTests.invoke(self,**kwargs)

    def test_scalar_reuse_survives_unrelated_runtime_body_edits(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.write();self.invoke(flags=flags,output='6\n')
                self.write(body='unused:=17;_=unused;fmt.println(VALUE)')
                warm=self.invoke(flags=flags,output='6\n')
                self.assertGreater(warm['cache']['prepared_hits'],0)
                self.assertEqual(warm['cache']['stats']['writes'],0)

    def test_dependency_edit_invalidates_prepared_result(self):
        self.write();self.invoke()
        self.write(prefix='work::proc(n:int)->int{return n+3}\nVALUE::#run work(4)')
        changed=self.invoke(output='7\n')
        self.assertEqual(changed['cache']['prepared_hits'],0)
        self.assertGreater(changed['cache']['stats']['writes'],0)

    def test_header_edit_invalidates_prepared_result(self):
        prefix='BASE::2\nwork::proc(n:int)->int{return n+BASE}\nVALUE::#run work(4)'
        self.write(prefix=prefix);self.invoke()
        self.write(prefix=prefix.replace('BASE::2','BASE::3'))
        changed=self.invoke(output='7\n')
        self.assertEqual(changed['cache']['prepared_hits'],0)

    def test_purity_failure_is_not_hidden_by_prepared_result(self):
        self.write();self.invoke()
        self.write(prefix='state:int\nwork::proc(n:int)->int{return n+state}\nVALUE::#run work(4)')
        self.invoke(error='runtime storage')

    def test_resource_contents_invalidate_prepared_result(self):
        resource=self.root/'data.txt';resource.write_text('abc')
        self.write(prefix='DATA::#load("data.txt",string)\nwork::proc()->int{return 3}\nVALUE::#run work()')
        self.invoke(output='3\n')
        resource.write_text('abcdef')
        changed=self.invoke(output='3\n')
        self.assertEqual(changed['cache']['prepared_hits'],0)
        self.assertGreater(changed['cache']['stats']['writes'],0)

    def test_corrupt_prepared_metadata_falls_back_to_contents(self):
        self.write();self.invoke()
        records=list((self.cache/'prepared-v1').glob('*.json'))
        self.assertTrue(records)
        for record in records:record.write_text('{')
        warm=self.invoke(output='6\n')
        self.assertEqual(warm['cache']['prepared_hits'],0)
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_missing_contents_target_is_recomputed(self):
        self.write();self.invoke()
        records=list((self.cache/'eval-v1').glob('*.json'))
        for record in records:record.unlink()
        warm=self.invoke(output='6\n')
        self.assertEqual(warm['cache']['prepared_hits'],0)
        self.assertGreater(warm['cache']['stats']['writes'],0)
        self.assertEqual(warm['cache']['stats']['misses'],1)

    def test_imported_helper_body_edit_invalidates_result(self):
        directory=self.root/'lib';directory.mkdir()
        source=directory/'lib.thor'
        source.write_text('package lib\nwork::proc(n:int)->int{return n+2}\n')
        self.write(prefix='import "lib"\nVALUE::#run lib.work(4)');self.invoke()
        source.write_text('package lib\nwork::proc(n:int)->int{return n+3}\n')
        changed=self.invoke(output='7\n')
        self.assertEqual(changed['cache']['prepared_hits'],0)

    def test_generic_results_keep_the_emitted_helper_policy(self):
        self.write(body='fmt.println(generic(4))',prefix='work::proc(n:int)->int{return n+2}\n'
            'generic::proc($N:int)->int{return #run work(N)}')
        self.invoke();warm=self.invoke(output='6\n')
        self.assertEqual(warm['cache']['prepared_hits'],0)
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_aggregate_results_keep_the_emitted_helper_policy(self):
        self.write(body='fmt.println(VALUE)',prefix='work::proc()->[2]int{return {2,4}}\nVALUE::#run work()')
        self.invoke();warm=self.invoke(output='[2, 4]\n')
        self.assertEqual(warm['cache']['prepared_hits'],0)
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_source_procedure_directive_keeps_the_emitted_helper_policy(self):
        self.write(prefix='work::proc()->int{return len(#procedure)}\nVALUE::#run work()')
        self.invoke();warm=self.invoke()
        self.assertEqual(warm['cache']['prepared_hits'],0)
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_disabled_cache_writes_no_prepared_record(self):
        self.write();packet=self.invoke(enabled=False,output='6\n')
        exists=(self.cache/'prepared-v1').exists()
        self.assertFalse(exists)
        self.assertEqual(packet['cache']['prepared_hits'],0)


if __name__=='__main__':
    unittest.main()
