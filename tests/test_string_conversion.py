"""Owned basic._string conversions and recoverable allocation errors."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests import test_thor as base

UNCHECKED=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']
IMPORTS='import "thor:basic"\n'


class StringConversionTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def both(self,body,prefix=IMPORTS,**options):
        for flags in [[],UNCHECKED]:
            with self.subTest(flags=flags):self.invoke(self.program(body,prefix),flags=flags,**options)

    def test_native_format_suite_checked_release_and_asan(self):
        with tempfile.TemporaryDirectory(prefix='thor-string-native-') as directory:
            for flags in [[],UNCHECKED,['-debug','-sanitize:address']]:
                with self.subTest(flags=flags):
                    result=subprocess.run([os.environ.get('THOR_ODIN','odin'),'test',str(base.ROOT/'runtime/format'),
                        '-collection:thor='+str(base.ROOT/'runtime'),'-out:'+str(Path(directory)/'string-tests'),
                        '-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true',*flags],capture_output=True,text=True,timeout=60)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_numeric_boolean_and_enum_output_matches_interpolation(self):
        body=[]
        values=['min(int)','max(uint)','max(uintptr)','min(i8)','min(i16)','min(i32)','min(i64)','min(i128)',
                'max(u8)','max(u16)','max(u32)','max(u64)','max(u128)','i32be(-12)','u64le(12)',
                'f16(1.25)','f32(-1.25)','f64(1e300)','f64be(1.25)','Count(-12)','Ratio(1.25)',
                'true','false','b32(true)','Choice.First',"'😀'",'complex64(1+2i)',
                'quaternion256(1-2i+3j-4k)']
        for value in values:
            body.append('''{
value:='''+value+'''
actual,err:=basic._string(value)
expected:=f"{value}"
if err!=nil||actual!=expected {panic("conversion differs from interpolation")}
delete(actual);delete(expected)
}''')
        self.both('\n'.join(body),IMPORTS+'Count::distinct i64\nRatio::distinct f64\nChoice::enum{First=7}\n')

    def test_integer_rational_constants_borrows_and_roundtrip(self):
        self.both('''
VALUE::Integer(12345678901234567890123456789012345678901234567890)
FRACTION::Rational(22,13)
integer:Integer=-123456789012345678901234567890
rational:=Rational(-22,13)
defer delete(integer);defer delete(rational)
for _ in 0..<3 {
 text,err:=basic._string(integer);defer delete(text)
 fraction,ferr:=basic._string(rational);defer delete(fraction)
 if err!=nil||ferr!=nil||text!="-123456789012345678901234567890"||fraction!="-22/13" {panic("logical numeric text")}
 parsed,ok:=Integer(text);defer delete(parsed)
 ratio,rok:=Rational(fraction);defer delete(ratio)
 if !ok||!rok||parsed!=integer||ratio!=rational {panic("roundtrip")}
}
text:=basic._string(VALUE) or_panic;defer delete(text)
fraction:=basic._string(FRACTION) or_panic;defer delete(fraction)
if text!="12345678901234567890123456789012345678901234567890"||fraction!="22/13" {panic("constant values")}
zero:Integer;ratio_zero:Rational
a,b:=basic._string(zero),basic._string(ratio_zero)
defer delete(a);defer delete(b)
if a!="0"||b!="0/1" {panic("zero values")}
''')

    def test_text_containers_unicode_bytes_and_independent_storage(self):
        self.both(r'''
letters:=[]rune{'a','界','😀',0}
text,err:=basic._string(letters);defer delete(text)
if err!=nil||text!="a界😀\x00" {panic("UTF8 slice")}
letters[0]='?'
if text[0]!='a' {panic("owned text")}
ary:=[2]rune{'é','界'}
immutable :> ary
fixed:[dynamic;4]rune
append(&fixed,'é','界')
growing:=make([dynamic]rune);defer delete(growing)
append(&growing,'é','界')
a,b,c,d:=basic._string(ary),basic._string(immutable),basic._string(fixed),basic._string(growing)
defer delete(a);defer delete(b);defer delete(c);defer delete(d)
if a!="é界"||a!=b||a!=c||a!=d {panic("rune sequences")}
bytes:=[4]u8{'a',0,255,'b'}
copy:=basic._string(bytes[:]);defer delete(copy)
if copy!="a\x00\xffb"||raw_data(copy)==raw_data(&bytes) {panic("byte contents")}
bytes[0]='?'
if copy[0]!='a' {panic("copied bytes")}
word:cstring="word"
owned:=basic._string(word);other:=basic._string(owned)
defer delete(owned);defer delete(other)
if owned!="word"||owned!=other||raw_data(owned)==raw_data(other)||raw_data(owned)==rawptr(word) {panic("string copies")}
invalid:=basic._string([rune(-1),rune(0x110000),rune(0xd800)])
defer delete(invalid)
if invalid!="\ufffd\ufffd\ufffd" {panic("invalid Unicode replacement")}
''')

    def test_optional_errors_nil_allocator_empty_inputs_and_return_propagation(self):
        self.both('''
allocator:=mem.nil_allocator()
text,err:=basic._string(123,allocator=allocator)
if err!=.Out_Of_Memory||text!="" {panic("returned allocation error")}
ignored:=basic._string(true,allocator=allocator)
if ignored!="" {panic("ignored error must not expose partial text")}
empty,eerr:=basic._string("",allocator=allocator)
nil_runes:[]rune;nil_bytes:[]u8
runes,rerr:=basic._string(nil_runes,allocator)
bytes,berr:=basic._string(nil_bytes,allocator)
if empty!=""||runes!=""||bytes!=""||eerr!=nil||rerr!=nil||berr!=nil {panic("empty allocation-free result")}
propagated,perr:=convert(allocator)
if propagated!=""||perr!=.Out_Of_Memory {panic("or_return propagation")}
''',IMPORTS+'''import "core:mem"
convert::proc(allocator:mem.Allocator)->(result:string,err:mem.Allocator_Error){
 text:=basic._string(1.25,allocator) or_return
 return text,nil
}
''')

    def test_long_enum_names_and_native_formatter_growth_failures(self):
        name='Long_'+'x'*700
        body='''
text,err:=basic._string(Choice.'''+name+''')
defer delete(text)
if err!=nil||text!="'''+name+'''" {panic("long enum name")}
failed,ferr:=basic._string(Choice.'''+name+''',mem.nil_allocator())
if failed!=""||ferr!=.Out_Of_Memory {basic.print("failed enum:",len(failed),ferr);panic("enum growth failure")}
registry:map[typeid]fmt.User_Formatter
registry[typeid_of(Number)]=custom
fmt.set_user_formatters(&registry)
defer {fmt._user_formatters=nil;delete(registry)}
chunk:=strings.repeat("x",4096);defer delete(chunk)
for budget in 0..<8 {
 state:=Budget{backing=context.allocator,remaining=budget}
 allocator:=mem.Allocator{limited,&state}
 calls=0
 result,rerr:=basic._string(Number(3),allocator)
 if calls!=1 {panic("formatter must execute once")}
 if rerr!=nil {if result!="" {panic("partial text escaped")}}
 else {if len(result)!=12288 {panic("custom format")};delete(result,allocator)}
}
'''
        prefix=IMPORTS+'''import "core:mem"
import "core:fmt"
import "core:strings"
Choice::enum{'''+name+'''}
Number::distinct int
calls:int
custom::proc(fi:^fmt.Info,arg:any,verb:rune)->bool {
 calls+=1
 chunk:[4096]u8
 for &byte in chunk {byte='x'}
 for _ in 0..<int(arg.(Number)) {fmt.fmt_string(fi,string(chunk[:]),'s')}
 return true
}
Budget::struct{backing:mem.Allocator,remaining:int}
limited::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,old:rawptr,old_size:int,loc:=#caller_location)->([]byte,mem.Allocator_Error) {
 state:=cast(^Budget)data
 if size>0&&(mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed) {
  if state.remaining==0 {return nil,.Out_Of_Memory}
  state.remaining-=1
 }
 return state.backing.procedure(state.backing.data,mode,size,alignment,old,old_size,loc)
}
'''
        for flags in [[],UNCHECKED,['-debug']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(self.program(body,prefix),flags=flags)
                self.assertNotIn('Thor allocation tracker:',result.stderr)

    def test_debug_ownership_default_custom_allocators_and_arena(self):
        _,result=self.invoke(self.program('''
tracker:mem.Tracking_Allocator
mem.tracking_allocator_init(&tracker,context.allocator)
defer mem.tracking_allocator_destroy(&tracker)
allocator:=mem.tracking_allocator(&tracker)
{
 context.allocator=mem.nil_allocator()
 context.temp_allocator=mem.nil_allocator()
 text,err:=basic._string(1.25,allocator)
 if err!=nil||text!="1.25"||tracker.current_memory_allocated!=4 {panic("explicit allocator")}
 delete(text,allocator)
}
if tracker.current_memory_allocated!=0 {panic("sized free")}
copied:=basic._string(f"anonymous input {7}") or_panic
if copied!="anonymous input 7" {panic("copy must survive anonymous input cleanup")}
delete(copied)
{
 context.allocator=basic.scoped_arena()
 rune_text:=basic._string(['界','😀']) or_panic
 integer:Integer=123456789012345678901234567890
 numeric:=basic._string(integer) or_panic
 basic.print(f"{rune_text}: {numeric}")
}
''',IMPORTS+'import "core:mem"\n'),flags=['-debug'],expected='界😀: 123456789012345678901234567890')
        self.assertNotIn('Thor allocation tracker:',result.stderr)
        self.assertNotIn('AddressSanitizer:',result.stderr)

    def test_selected_wildcard_imports_and_generic_callers(self):
        for declaration in ['import * "thor:basic"','import _ "thor:basic" / _string']:
            with self.subTest(declaration=declaration):
                self.invoke(self.program('''
text:=_string(['a','b']) or_panic;defer delete(text)
number:=describe(42);defer delete(number)
if text!="ab"||number!="42" {panic("imports/generic")}
''',declaration+'\ndescribe::proc(value:$T)->string{return _string(value) or_panic}\n'))

    def test_unsupported_types_have_clear_mapped_diagnostics(self):
        for expression in ['struct{x:int}{1}','[]int{1,2}','map[string]int{}','(^int)(nil)','proc(){}','boxed','unicode','unicode_c']:
            with self.subTest(expression=expression):
                _,result=self.invoke(self.program('boxed:any=42;unicode:string16;unicode_c:cstring16\n_=basic._string('+expression+')',IMPORTS),command='check',
                    fail='_string supports byte/rune sequences')
                self.assertIn('main.thor(',result.stderr)
                self.assertNotIn('__thor_',result.stderr)


if __name__=='__main__':unittest.main()
