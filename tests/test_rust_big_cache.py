"""Logical contents packets stay canonical, reusable and independently owned."""
import hashlib
import json
import unittest
from tests import test_rust_cached_execution as cached
from tests import test_rust_compiler as native

class RustBigCacheTests(unittest.TestCase):
    setUpClass=classmethod(cached.RustCachedExecutionTests.setUpClass.__func__)
    setUp=cached.RustCachedExecutionTests.setUp
    invoke=cached.RustCachedExecutionTests.invoke

    def write(self,number='123456789012345678901234567890',extra=''):
        self.source.write_text('''package main
import "core:fmt"
import "core:mem"
import "thor:big"
work::fn()->Integer{return Integer('''+number+''')}
main::proc(){
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 a:=#run work();b:=#run work()
 '''+extra+'''
 a+=1;if a==b{panic("independence")}
 text:=big.to_string(b);fmt.println(text);delete(text);delete(a);delete(b)
 if len(tracker.allocation_map)!=0||len(tracker.bad_free_array)!=0{panic("cached ownership")}
}
''')

    def test_warm_logical_packets_survive_runtime_edits_and_invalidate_for_pure_edits(self):
        for flags in (['-debug'],native.UNCHECKED):
            with self.subTest(flags=flags):
                self.write();cold=self.invoke(flags=flags,output='123456789012345678901234567890\n')
                self.assertGreater(cold['cache']['stats']['writes'],0)
                warm=self.invoke(flags=flags,output='123456789012345678901234567890\n')
                self.assertGreaterEqual(warm['cache']['stats']['hits'],2)
                self.assertEqual(warm['cache']['stats']['writes'],0)
                self.assertGreater(warm['cache']['prepared_hits'],0)
                self.write(extra='extra:=22;_=extra')
                edited=self.invoke(flags=flags,output='123456789012345678901234567890\n')
                self.assertGreaterEqual(edited['cache']['stats']['hits'],2)
                self.assertGreater(edited['cache']['prepared_hits'],0)
                self.write(number='123456789012345678901234567891')
                changed=self.invoke(flags=flags,output='123456789012345678901234567891\n')
                self.assertGreater(changed['cache']['stats']['writes'],0)
                packets=[json.loads(p.read_text())['value'] for p in (self.cache/'eval-v1').glob('*.json')]
                self.assertTrue(packets)
                self.assertTrue(all(v['kind']=='big_integer' and set(v)=={'kind','value'} for v in packets))

    def test_checksummed_noncanonical_numeric_packets_are_recomputed(self):
        self.write();self.invoke(output='123456789012345678901234567890\n')
        paths=list((self.cache/'eval-v1').glob('*.json'));self.assertTrue(paths)
        for path in paths:
            record=json.loads(path.read_text());record['value']={'kind':'big_integer','value':'01'}
            contents=json.dumps(record['value'],sort_keys=True,separators=(',',':')).encode()
            record['checksum']=hashlib.sha256(contents).hexdigest();path.write_text(json.dumps(record))
        recovered=self.invoke(output='123456789012345678901234567890\n')
        self.assertGreater(recovered['cache']['stats']['corrupt'],0)
        self.assertGreater(recovered['cache']['stats']['writes'],0)

if __name__=='__main__':unittest.main()
