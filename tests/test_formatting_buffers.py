"""Allocation, lifetime and fallback contracts for streaming interpolation."""
import unittest
from tests import test_thor as base


STRICT=r'''
import "core:mem"
import "core:strings"
State::struct{backing:mem.Allocator,sizes:map[rawptr]int,no_resize:bool}
strict::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old_memory:rawptr,old_size:int,
             loc:=#caller_location)->([]byte,mem.Allocator_Error){
 s:=cast(^State)data
 if old_memory!=nil && (mode==.Free||mode==.Resize||mode==.Resize_Non_Zeroed) {
  previous,found:=s.sizes[old_memory]
  assert(found && previous==old_size,"allocator received the wrong allocation size")
 }
 if s.no_resize && (mode==.Resize||mode==.Resize_Non_Zeroed){return nil,.Mode_Not_Implemented}
 result,err:=s.backing.procedure(s.backing.data,mode,size,alignment,old_memory,old_size,loc)
 if err!=nil{return result,err}
 #partial switch mode {
 case .Alloc,.Alloc_Non_Zeroed:if raw_data(result)!=nil{s.sizes[raw_data(result)]=size}
 case .Resize,.Resize_Non_Zeroed:
  if old_memory!=nil{delete_key(&s.sizes,old_memory)}
  if raw_data(result)!=nil{s.sizes[raw_data(result)]=size}
 case .Free:if old_memory!=nil{delete_key(&s.sizes,old_memory)}
 case:
 }
 return result,err
}
'''


class FormattingBufferTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_formatting_with_assertions_disabled_and_unchecked_release_flags(self):
        source=self.program('''
backing:=context.allocator
tracker:mem.Tracking_Allocator;mem.tracking_allocator_init(&tracker,backing)
context.allocator=mem.tracking_allocator(&tracker)
width:=8;number:=42
text:=f"value {number:0{width}d}|{1.25:+.2f}|{0.125:.2%}|{Choice.A:.4d}|{42:.8d}"
fmt.println(text);delete(text)
text=f"outer {f"inner {number}"} end"
fmt.println(text);delete(text)
text=f"{Integer(12345678901234567890):,}|{Rational(1,3):.2f}"
fmt.println(text);delete(text)
input:=strings.repeat("x",4096)
text=f"prefix {input} suffix"
if len(text)!=4110 || text[:7]!="prefix " || text[-7:]!=" suffix" {panic("large output differs")}
delete(text);delete(input)
if tracker.total_allocation_count==0 || tracker.current_memory_allocated!=0 {panic("allocation accounting differs")}
context.allocator=backing
mem.tracking_allocator_destroy(&tracker)
fmt.println("release formatting passed")
''','import "core:fmt"\nimport "core:mem"\nimport "core:strings"\nChoice::enum{A=7}\n')
        expected="value 00000042|+1.25|12.50%|0007|00000042\nouter inner 42 end\n12,345,678,901,234,567,890|0.33\nrelease formatting passed"
        for flags in [
            ['-disable-assert'],
            ['-o:speed','-disable-assert'],
            ['-o:speed','-microarch:native','-no-bounds-check','-no-type-assert','-disable-assert'],
        ]:
            with self.subTest(flags=flags):self.invoke(source,flags=flags,expected=expected)

    def test_common_native_fields_allocate_only_the_owned_result(self):
        self.invoke(self.program('''
backing:=context.allocator
tracker:mem.Tracking_Allocator;mem.tracking_allocator_init(&tracker,backing)
defer mem.tracking_allocator_destroy(&tracker)
context.allocator=mem.tracking_allocator(&tracker)
width:=8;number:=42;value:=1.25
text:=f"{number:0{width}d}|{value:.2f}|{number+1}|{true}|{\"text\"}"
assert(text=="00000042|1.25|43|true|text")
assert(tracker.total_allocation_count==1)
assert(tracker.current_memory_allocated==i64(len(text)))
delete(text)
assert(tracker.current_memory_allocated==0)
context.allocator=backing
''','import "core:mem"\n'),flags=['-o:speed'])

    def test_custom_allocator_exact_sized_frees_and_unsupported_resize_fallback(self):
        self.invoke(self.program('''
backing:=context.allocator
for no_resize in [false,true] {
 state:=State{backing=backing,sizes=make(map[rawptr]int,allocator=backing),no_resize=no_resize}
 context.allocator=mem.Allocator{procedure=strict,data=&state}
 for count in [0,1,127,255,511,512,513,1024,4096] {
  input:=make([]u8,count)
  for &byte in input {byte='x'}
  text:=f"<{string(input)}>|{string(input):q}|{\"é😀\":'界'^{count+2}.1s}|{1.25:.1000f}|{42:.1000d}"
  assert(text[0]=='<' && len(text)>2000)
  delete(text);delete(input)
  assert(len(state.sizes)==0)
 }
 context.allocator=backing
 delete(state.sizes)
}
''',STRICT),flags=['-debug','--track-allocations=off'])

    def test_results_escape_stack_storage_and_remain_independent(self):
        self.invoke(self.program('''
a:=describe(41);b:=describe(42)
defer delete(a);defer delete(b)
assert(a=="value 41" && b=="value 42" && raw_data(a)!=raw_data(b))
for i in 0..<100 {text:=describe(i);assert(len(text)>0);delete(text)}
assert(a=="value 41" && b=="value 42")
''','describe::proc(n:int)->string{return f"value {n}"}\n'),flags=['-debug'])

    def test_streaming_api_preserves_input_views_during_destination_growth(self):
        self.invoke(self.program('''
input:=strings.repeat("ab",1024);defer delete(input)
for width in [0,4096] {
 buffer:format.Buffer;format.buffer_init(&buffer)
 format.buffer_write_string(&buffer,input)
 view:=strings.to_string(buffer.builder)
 format.write_spec(&buffer.builder,view,grouping=false,width=width,align=.Right,
                   verb='s',precision=-1,plus=false,space=false,zero=false,alternate=false)
 text:=format.buffer_owned_text(&buffer)
 format.buffer_destroy(&buffer)
 assert(len(text)==2048+max(2048,width) && text[:2048]==input && text[-2048:]==input)
 delete(text)
}
''','import "core:strings"\nimport "thor:format"\n'),flags=['-debug'])

    def test_custom_formatter_executes_once_across_stack_and_heap_growth(self):
        self.invoke(self.program('''
registry:map[typeid]fmt.User_Formatter
registry[typeid_of(Item)]=custom;fmt.set_user_formatters(&registry)
defer {fmt._user_formatters=nil;delete(registry)}
for count in [0,1,511,512,513,4096] {
 calls=0
 text:=f"begin {Item{count}:'_'^{count+2}v} end"
 assert(calls==1 && len(text)==count+12 && text[:7]=="begin _" && text[-5:]=="_ end")
 delete(text)
}
''','''import "core:fmt"
calls:int
Item::struct{count:int}
custom::proc(fi:^fmt.Info,arg:any,verb:rune)->bool{
 calls+=1
 item:=arg.(Item)
 for _ in 0..<item.count {fmt.fmt_string(fi,"x",'s')}
 return true
}
'''),flags=['-debug'])

    def test_native_formats_match_odin_for_many_runtime_values(self):
        self.invoke(self.program('''
for i in -100..<100 {
 value:=f64(i)/7
 a:=f"{i:+08d}|{i:#x}|{value:.3f}|{value:.4e}|{\"hi\":-5s}"
 b:=fmt.aprintf("%+08d|%#x|%.3f|%.4e|%-5s",i,i,value,value,"hi")
 assert(a==b);delete(a);delete(b)
}
''','import "core:fmt"\n'),flags=['--track-allocations=basic'])

    def test_public_formatter_uses_explicit_allocator_for_owned_result(self):
        self.invoke(self.program('''
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
allocator:=mem.tracking_allocator(&tracker)
text:=format.format(1234,grouping=true,allocator=allocator)
assert(text=="1,234" && tracker.total_allocation_count==1 && tracker.current_memory_allocated==5)
delete(text,allocator=allocator)
assert(tracker.current_memory_allocated==0)
''','import "core:mem"\nimport "thor:format"\n'))

    def test_final_result_allocation_failure_is_reported(self):
        source=self.program('context.allocator=mem.Allocator{procedure=deny};_=f"short {7}"','''import "core:mem"
deny::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old_memory:rawptr,old_size:int,
           loc:=#caller_location)->([]byte,mem.Allocator_Error){return nil,.Out_Of_Memory}
''')
        for flags in [[],['-o:speed','-disable-assert']]:
            with self.subTest(flags=flags):self.invoke(source,flags=flags,fail='format allocation failed')

    def test_failed_scratch_growth_does_not_silently_truncate_custom_output(self):
        source=self.program('''
registry:map[typeid]fmt.User_Formatter
registry[typeid_of(Item)]=custom;fmt.set_user_formatters(&registry)
context.allocator=mem.Allocator{procedure=deny}
_=f"{Item{}}"
''','''import "core:fmt"
import "core:mem"
Item::struct{}
custom::proc(fi:^fmt.Info,arg:any,verb:rune)->bool{
 for _ in 0..<4096 {fmt.fmt_string(fi,"x",'s')}
 return true
}
deny::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old_memory:rawptr,old_size:int,
           loc:=#caller_location)->([]byte,mem.Allocator_Error){return nil,.Out_Of_Memory}
''')
        for flags in [[],['-o:speed','-disable-assert']]:
            with self.subTest(flags=flags):self.invoke(source,flags=flags,fail='format allocation failed')


if __name__=='__main__':unittest.main()
