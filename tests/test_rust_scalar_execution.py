"""Scalar site records validate bindings, bodies and external inputs on reuse."""
import shutil
import unittest
from tests import test_rust_cached_execution as cached
from tests import test_rust_compiler as native


class RustScalarExecutionTests(unittest.TestCase):
    maxDiff=None
    setUpClass=classmethod(cached.RustCachedExecutionTests.setUpClass.__func__)
    setUp=cached.RustCachedExecutionTests.setUp
    write=cached.RustCachedExecutionTests.write
    invoke=cached.RustCachedExecutionTests.invoke

    def discard_prepared(self):
        shutil.rmtree(self.cache/'prepared-v1',ignore_errors=True)

    def test_straight_line_site_reuses_before_dependency_extraction(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.write();self.invoke(flags=flags,output='6\n');self.discard_prepared()
                warm=self.invoke(flags=flags,output='6\n')
                self.assertGreater(warm['cache']['scalar_reused'],0)
                self.assertEqual(warm['cache']['stats']['writes'],0)

    def test_unrelated_runtime_body_edit_preserves_site_record(self):
        self.write();self.invoke();self.discard_prepared()
        self.write(body='other:=12;_=other;fmt.println(VALUE)')
        warm=self.invoke(output='6\n')
        self.assertGreater(warm['cache']['scalar_reused'],0)

    def test_local_constant_binding_edit_invalidates_site_record(self):
        body='INPUT::4;fmt.println(#run work(INPUT))'
        self.write(body=body,prefix='work::proc(n:int)->int{return n+2}')
        self.invoke(output='6\n');self.discard_prepared()
        self.write(body=body.replace('INPUT::4','INPUT::7'),prefix='work::proc(n:int)->int{return n+2}')
        changed=self.invoke(output='9\n')
        self.assertEqual(changed['cache']['scalar_reused'],0)

    def test_runtime_shadow_cannot_reuse_constant_binding_result(self):
        body='INPUT::4;fmt.println(#run work(INPUT))'
        self.write(body=body,prefix='work::proc(n:int)->int{return n+2}');self.invoke();self.discard_prepared()
        self.write(body=body.replace('INPUT::4','INPUT:=4'),prefix='work::proc(n:int)->int{return n+2}')
        self.invoke(error='runtime storage is not a constant')

    def test_transitive_helper_body_change_invalidates_site_record(self):
        prefix='leaf::proc(n:int)->int{return n+2}\nwork::proc(n:int)->int{return leaf(n)}\nVALUE::#run work(4)'
        self.write(prefix=prefix);self.invoke();self.discard_prepared()
        self.write(prefix=prefix.replace('n+2','n+3'))
        changed=self.invoke(output='7\n')
        self.assertEqual(changed['cache']['scalar_reused'],0)

    def test_new_purity_violation_cannot_reuse_site_record(self):
        self.write();self.invoke();self.discard_prepared()
        self.write(prefix='state:int\nwork::proc(n:int)->int{return n+state}\nVALUE::#run work(4)')
        self.invoke(error='runtime storage')

    def test_duplicate_expression_in_different_procedures_keeps_bindings(self):
        self.write(body='fmt.println(one(),two())',prefix='work::proc(n:int)->int{return n+2}\n'
            'one::proc()->int{N::4;return #run work(N)}\n'
            'two::proc()->int{N::7;return #run work(N)}')
        self.invoke(output='6 9\n');self.discard_prepared()
        warm=self.invoke(output='6 9\n')
        self.assertEqual(warm['cache']['scalar_reused'],2)

    def test_conditional_scope_remains_on_existing_cache_path(self):
        self.write(body='if true{fmt.println(#run work(4))}',prefix='work::proc(n:int)->int{return n+2}')
        self.invoke();warm=self.invoke(output='6\n')
        self.assertEqual(warm['cache']['scalar_reused'],0)

    def test_using_source_remains_on_existing_cache_path(self):
        self.write(body='using fmt;println(VALUE)')
        self.invoke();warm=self.invoke(output='6\n')
        self.assertEqual(warm['cache']['scalar_reused'],0)

    def test_missing_contents_target_rebuilds_site_record(self):
        self.write();self.invoke();self.discard_prepared()
        packets=list((self.cache/'eval-v1').glob('*.json'))
        for packet in packets:packet.unlink()
        changed=self.invoke(output='6\n')
        self.assertEqual(changed['cache']['scalar_reused'],0)
        self.assertGreater(changed['cache']['stats']['writes'],0)
        self.assertEqual(changed['cache']['stats']['misses'],1)
        warm=self.invoke(output='6\n')
        self.assertGreater(warm['cache']['scalar_reused'],0)

    def test_malformed_site_record_is_optional(self):
        self.write();self.invoke();self.discard_prepared()
        records=list((self.cache/'scalar-v1').glob('*.json'))
        self.assertTrue(records)
        for record in records:record.write_text('{')
        warm=self.invoke(output='6\n')
        self.assertEqual(warm['cache']['scalar_reused'],0)
        self.assertGreater(warm['cache']['stats']['hits'],0)

    def test_resource_changes_invalidate_site_record(self):
        resource=self.root/'data';resource.write_text('first')
        self.write(prefix='DATA::#load("data",string)\nwork::proc()->int{return 3}\nVALUE::#run work()')
        self.invoke();self.discard_prepared();resource.write_text('second')
        changed=self.invoke(output='3\n')
        self.assertEqual(changed['cache']['scalar_reused'],0)

    def test_header_attribute_change_invalidates_site_record(self):
        self.write();self.invoke();self.discard_prepared()
        self.write(prefix='@(require_results)\nwork::proc(n:int)->int{return n+2}\nVALUE::#run work(4)')
        changed=self.invoke(output='6\n')
        self.assertEqual(changed['cache']['scalar_reused'],0)

    def test_flag_changes_do_not_reuse_the_old_site(self):
        self.write();self.invoke();self.discard_prepared()
        changed=self.invoke(flags=native.UNCHECKED,output='6\n')
        self.assertEqual(changed['cache']['scalar_reused'],0)

    def test_platform_excluded_project_files_keep_valid_site_result(self):
        library=self.root/'sdk/sample';library.mkdir(parents=True)
        (library/'sample.odin').write_text('package sample\nwork::proc()->int{return 6}\n')
        inactive=library/'inactive_windows.odin'
        inactive.write_text('package sample\nunused::1\n')
        self.source.write_text('package main\nimport lib "extras:sample"\nVALUE::#run lib.work()\nmain::proc(){_=VALUE}\n')
        flags=['-debug','-collection:extras='+str(library.parent)]
        self.invoke(flags=flags);self.discard_prepared()
        warm=self.invoke(flags=flags)
        self.assertGreater(warm['cache']['scalar_reused'],0)
        inactive.write_text('package sample\nunused::2\n')
        changed=self.invoke(flags=flags)
        self.assertGreater(changed['cache']['scalar_reused'],0)
        self.assertEqual(changed['results'][0][1]['value'],'6')

    def test_retargeted_raw_collection_path_invalidates_site(self):
        for name,value in [('one',6),('two',7)]:
            directory=self.root/name/'sample';directory.mkdir(parents=True)
            (directory/'sample.odin').write_text(f'package sample\nwork::proc()->int{{return {value}}}\n')
        link=self.root/'sdk';link.symlink_to(self.root/'one',target_is_directory=True)
        self.source.write_text('package main\nimport lib "extras:sample"\nVALUE::#run lib.work()\nmain::proc(){_=VALUE}\n')
        flags=['-debug','-collection:extras='+str(link)]
        self.invoke(flags=flags);self.discard_prepared()
        warm=self.invoke(flags=flags)
        self.assertGreater(warm['cache']['scalar_reused'],0)
        link.unlink();link.symlink_to(self.root/'two',target_is_directory=True)
        changed=self.invoke(flags=flags)
        self.assertEqual(changed['cache']['scalar_reused'],0)
        self.assertEqual(changed['results'][0][1]['value'],'7')

    def test_build_tag_edits_that_activate_native_files_invalidate_site(self):
        library=self.root/'sdk/sample';library.mkdir(parents=True)
        (library/'sample.odin').write_text('package sample\nwork::proc()->int{return 6}\n')
        inactive=library/'tagged.odin'
        inactive.write_text('#+ignore\npackage sample\nUNUSED::1\n')
        self.source.write_text('package main\nimport lib "extras:sample"\nVALUE::#run lib.work()\nmain::proc(){_=VALUE}\n')
        flags=['-debug','-collection:extras='+str(library.parent)]
        self.invoke(flags=flags);self.discard_prepared()
        warm=self.invoke(flags=flags)
        self.assertGreater(warm['cache']['scalar_reused'],0)
        inactive.write_text('package sample\nUNUSED::1\n')
        changed=self.invoke(flags=flags)
        self.assertEqual(changed['cache']['scalar_reused'],0)
        self.assertEqual(changed['results'][0][1]['value'],'6')

    def test_python_interface_fingerprints_recheck_excluded_file_activation(self):
        from types import SimpleNamespace
        from thorlang.compiler import Source
        from thorlang.scalarcache import headers
        path=self.root/'tagged.odin'
        def fingerprint():
            source=Source(path,self.frontend,thor=False,package=True,cache=False)
            return headers(SimpleNamespace(sources={path:source}))
        path.write_text('#+ignore\npackage sample\nVALUE::1\n')
        before=fingerprint()
        path.write_text('#+ignore\npackage sample\nVALUE::2\n')
        inactive=fingerprint()
        self.assertEqual(inactive,before)
        path.write_text('package sample\nVALUE::2\n')
        active=fingerprint()
        self.assertNotEqual(active,before)


if __name__=='__main__':
    unittest.main()
