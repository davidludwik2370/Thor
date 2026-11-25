"""Native positive oracles keep their setup and verdicts with assertions off."""
import json
import unittest
from tests import test_scoped_owners as base


class LifetimeContractsRuntimeTests(unittest.TestCase):
    setUpClass=classmethod(base.ScopedOwnerTests.setUpClass.__func__)
    setUp=base.ScopedOwnerTests.setUp
    invoke=base.ScopedOwnerTests.invoke
    compare=base.ScopedOwnerTests.compare
    checked=base.ScopedOwnerTests.checked

    def test_repeated_exclusive_replacements_release_every_generation(self):
        self.checked('''package main
import "core:mem"
fresh::proc()->[dynamic]int{v:[dynamic]int;append(&v,7);return v}
exercise::proc(){
 values:=fresh()
 defer delete(values)
 total:=0
 for _ in 0..<1000 {
  for &n in values {n+=1}
  total+=values[0]
  delete(values)
  values=fresh()
 }
 if total!=8000 {panic("replacement result")}
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 exercise()
 if tracker.current_memory_allocated!=0 || len(tracker.bad_free_array)!=0 {panic("replacement cleanup")}
}
''')

    def test_recursive_reader_and_inline_writer_keep_native_behavior(self):
        self.checked('''package main
Node::struct{value:int,left,right:^Node}
sum::proc(p:^Node)->int{if p==nil{return 0};return p.value+sum(p.left)+sum(p.right)}
increment::proc(p:^Node){if p==nil{return};p.value+=1;increment(p.left);increment(p.right)}
main::proc(){a:=Node{value=1};b:=Node{value=2};root:=Node{value=3,left=&a,right=&b}
 for _ in 0..<100{increment(&root)}
 result:=sum(&root)
 if result!=306 || a.value!=101 || b.value!=102 || root.value!=103{panic("recursive effects")}
}
''')

    def test_native_file_acquisition_io_and_close(self):
        path=json.dumps(str(self.app/'contract-runtime.txt'))
        self.checked('''package main
import "core:os"
main::proc(){
 name::'''+path+'''
 writer:=os.create(name) or_panic
 bytes:=[5]u8{'h','e','l','l','o'}
 count,err:=os.write(writer,bytes[:])
 if err!=nil || count!=5{panic("write")}
 close_error:=os.close(writer)
 if close_error!=nil{panic("close writer")}
 reader:=os.open(name) or_panic
 buffer:[16]u8
 read_count,read_error:=os.read(reader,buffer[:])
 if read_count!=5 || (read_error!=nil && read_error!=.EOF){panic("read")}
 for byte,i in bytes{if buffer[i]!=byte{panic("contents")}}
 close_error=os.close(reader)
 if close_error!=nil{panic("close reader")}
 remove_error:=os.remove(name)
 if remove_error!=nil{panic("remove")}
}
''')


if __name__=='__main__':unittest.main()
