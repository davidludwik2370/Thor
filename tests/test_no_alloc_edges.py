"""Nonallocating native paths retain types; unknown/allocating effects stay errors."""
import unittest
from tests import test_rust_compiler as base


class NoAllocEdgeTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare

    def check(self,body,decl='',fail=None):
        return self.compare('package main\n'+decl+'\n@(no_alloc)\nwork::proc(){'+body+'}\nmain::proc(){work()}\n',
                            command='check',flags=['-thread-count:1'],fail=fail)

    def compact_lowering(self,source):
        self.source.write_text(source)
        for engine in ['python','rust']:
            lowered=self.invoke(engine,command='transpile',flags=['-thread-count:1'])
            self.assertEqual(lowered.returncode,0,lowered.stdout+lowered.stderr)
            files=list((self.root/engine).rglob('main.odin'))
            self.assertEqual(len(files),1)
            size=files[0].stat().st_size
            self.assertLess(size,100_000,'Retained type facts must not expand scalar arithmetic into large fallback expressions')

    def test_generic_expression_shapes(self):
        for decl,body in [
            ('neg::proc(x:$T)->T{return -x}','_=neg(2)'),
            ('invert::proc(x:$T)->T{return ~x}','_=invert(u32(2))'),
            ('choose::proc(flag:bool,a,b:$T)->T{return flag?a:b}','_=choose(true,2,3)'),
            ('bound::proc(a,b:$T)->T{return min(a,b)}','_=bound(2,3)'),
            ('bound::proc(a,b:$T)->T{return max(a,b)}','_=bound(2,3)'),
            ('bound::proc(a,b,c:$T)->T{return clamp(a,b,c)}','_=bound(2,1,3)'),
            ('magnitude::proc(x:$T)->T{return abs(x)}','_=magnitude(-2)'),
        ]:
            with self.subTest(decl=decl):self.check(body,decl)

    def test_slicing_and_indexing_preserve_specializations(self):
        for decl,body in [
            ('@(no_alloc)\ntail::proc(s:[]$T)->[]T{return s[1:]}','values:=[1,2,3];s:=tail(values[:]);_=len(s)'),
            ('@(no_alloc)\nview::proc(s:^[$N]$T)->[]T{return s[:]}','values:=[1,2,3];s:=view(&values);_=len(s)'),
            ('read::proc(s:[]$T)->T{tail:=s[1:];return tail[0]}','a:=[1,2,3];_=read(a[:])'),
            ('read::proc(p:^[$N]$T)->T{s:=p[:];return s[0]}','a:=[1,2,3];_=read(&a)'),
            ('','a:=[1,2,3];p:=&a;s:=p[:];s[0]=2;_=p[0]'),
            ('','a:=[1,2,3];s:=a[:];p:=&s;t:=p[:];t[0]=2'),
            ('','text:="abc";_=text[0];_=text[1:]'),
            ('','a:=[1,2];p:=raw_data(a[:]);_=p[0];_=p[:2]'),
            ('','a:=[2][2]int{{1,2},{3,4}};p:=raw_data(a[:]);s:=p[:2];v:=s[1];_=v[0]'),
        ]:
            with self.subTest(body=body):self.check(body,decl)

    def test_type_switch_and_assertion_bindings(self):
        for decl,body in [
            ('Value::union{int,f64}\nread::proc(x:Value)->int{switch v in x {case int:return v+1;case f64:return int(v);case:return 0}}','_=read(int(2))'),
            ('read::proc(x:any)->int{switch v in x {case int:return v+1;case f64:return int(v);case:return 0}}','_=read(2)'),
            ('read::proc(x:any,$T:typeid)->T{v,ok:=x.(T);_=ok;return v}','_=read(2,int)'),
            ('Pair::struct{x:int}\nValue::union{Pair,int}\nread::proc(input:^Value)->int{switch &v in input {case Pair:using v;return x;case int:return v;case:return 0}}','value:=Value(Pair{2});_=read(&value)'),
        ]:
            with self.subTest(decl=decl):self.check(body,decl)

    def test_multi_result_builtin_shapes(self):
        for decl,body in [
            ('Pair::struct{a,b:int}','a,b:=expand_values(Pair{1,2});_=a;_=b'),
            ('','a,b:=expand_values([1,2]);_=a;_=b'),
            ('','v:=compress_values(2,3);_=v[0]'),
            ('','v:=compress_values(2,true);_=v.v0;_=v.v1'),
            ('pair::proc()->(int,bool){return 2,true}','v:=compress_values(pair());_=v.v0;_=v.v1'),
            ('','a:=[1,2];b:=[3,4];z:=soa_zip(a=a[:],b=b[:]);s,t:=soa_unzip(z);_=s[0];_=t[0]'),
        ]:
            with self.subTest(body=body):self.check(body,decl)

    def test_numeric_and_swizzle_builtin_aliases(self):
        for decl,body in [
            ('','z:=complex(f64(1),f64(2));_=real(z);_=imag(z);_=conj(z)'),
            ('','q:=quaternion(w=f64(1),x=f64(2),y=f64(3),z=f64(4));_=real(q);_=imag(q);_=jmag(q);_=kmag(q);_=conj(q)'),
            ('read::proc(v:[$N]$T)->[2]T{return swizzle(v,1,0)}','_=read([1,2,3])'),
            ('','shuffle::swizzle;v:=[1,2,3];s:=shuffle(v,2,0);_=s[0]'),
            ('','low::min;mag::abs;_=low(2,3);_=mag(-2)'),
            ('import "core:math"','_=math.abs(-3);_=math.sqrt(f64(4))'),
            ('good::proc(x:u64)->int{return 1}\nbad::proc(x:int)->int{_=make([]int,1);return 0}\nchoose::proc{good,bad}',
             '_=choose(min(1,u64(2)));_=choose(clamp(1,u64(0),u64(3)))'),
        ]:
            with self.subTest(body=body):self.check(body,decl)

    def test_large_homogeneous_value_expansion_keeps_compact_type_facts(self):
        self.compact_lowering('package main\n@(no_alloc)\nmain::proc(){values:[100000]int;packed:=compress_values(expand_values(values));_=len(packed)}')

    def test_inferred_default_parameter_types(self):
        for decl,body in [
            ('read::proc(value:=3)->int{return value+1}','_=read();_=read(7)'),
            ('read::proc(value:=false)->bool{return !value}','_=read();_=read(true)'),
            ('read::proc(value:=f32(3))->f32{return -value}','_=read();_=read(7)'),
        ]:
            with self.subTest(decl=decl):self.check(body,decl)

    def test_native_branch_statements(self):
        self.check('for i in 0..<3 {if i==1 {continue};if i==2 {break}}')
        self.check('outer: for i in 0..<3 {for j in 0..<3 {if i==j {break outer};continue}}')
        self.check('value:=1;switch value {case 1:fallthrough;case: value=2}')

    def test_proven_expression_paths_skip_unexecuted_calls(self):
        declarations='allocate::proc()->bool{_=make([]int,1);return true}'
        for body in ['_=false&&allocate()','_=true||allocate()','_=true?false:allocate()',
                     '_=false?allocate():true','for false {_=allocate()}',
                     'value:=false;_=value&&allocate()']:
            with self.subTest(body=body):self.check(body,declarations)
        for body in ['_=true&&allocate()','_=false||allocate()','_=true?allocate():false']:
            with self.subTest(body=body):self.check(body,declarations,fail="builtin 'make'")
        self.check('_=allocate()&&false',declarations,fail="builtin 'make'")
        self.check('_=read(false)',declarations+'\nread::proc(flag:bool)->bool{return flag&&allocate()}')

    def test_unknown_paths_and_condition_side_effects_remain_checked(self):
        for decl,body in [
            ('allocate::proc()->bool{_=make([]int,1);return true}', '_=allocate()?true:false'),
            ('allocate::proc()->bool{_=make([]int,1);return true}', 'for values:=make([]int,1);false; {_=values}'),
            ('allocate::proc()->bool{_=make([]int,1);return true}\nread::proc(flag:bool){_=flag&&allocate()}', 'read(true)'),
        ]:
            with self.subTest(body=body):self.check(body,decl,fail='no_alloc:')

    def test_contextual_literals_through_wrappers(self):
        declarations='Pair::struct{x,y:int}\nread::proc(p:Pair)->int{return p.x}\nitems::proc(p:..Pair){}'
        for body in ['_=read(({1,2}))','_=read(true?{1,2}:{3,4})','items(({1,2}),({3,4}))','items(p=[]Pair{{1,2},{3,4}})']:
            with self.subTest(body=body):self.check(body,declarations)

    def test_sdk_borrowed_string_and_parsing_helpers(self):
        self.check('_=strings.trim_space(" abc ");_=strings.trim_space(" É 😀 ");_=strings.has_prefix("abc","a")','import "core:strings"')
        self.check('_,_=strconv.parse_int("123");_,_=strconv.parse_int("bad");_,_=strconv.parse_bool("true")','import "core:strconv"')
        self.check('context.user_index=2;_=context.user_index')

    def test_memory_primitives_and_aliases(self):
        self.check('a,b:[4]byte;mem.copy(mem.raw_data(a[:]),mem.raw_data(b[:]),4);mem.zero_slice(a[:])','import "core:mem"')
        self.check('a,b:[4]byte;copy_bytes::intrinsics.mem_copy;copy_bytes(raw_data(a[:]),raw_data(b[:]),4);intrinsics.mem_zero(raw_data(a[:]),4)','import "base:intrinsics"')

    def test_builtin_names_and_memory_aliases_do_not_grant_effects(self):
        self.check('_=abs(2)','abs::proc(x:int)->int{_=make([]int,1);return x}',fail="builtin 'make'")
        self.check('alias(nil,nil,0)','mem_copy::proc(dst,src:rawptr,n:int){_=make([]int,1)}\nalias::mem_copy',fail="builtin 'make'")

    def test_owned_data_remains_rejected_after_type_refinements(self):
        for decl,body in [
            ('import "thor:basic" / Integer\nread::proc(values:[]$T){tail:=values[1:];_=tail[0]}','values:[]Integer;read(values)'),
            ('import "thor:basic" / Integer\nread::proc(p:^[1]Integer){_=p[0]}','read(nil)'),
            ('import "thor:basic" / Integer\nread::proc(p:[^][1]Integer){_=p[0]}','read(nil)'),
            ('import "thor:basic" / Integer\nneg::proc(x:$T)->T{return -x}','value:Integer;_=neg(value)'),
            ('import "thor:basic" / Integer\nPair::struct{x:Integer}\nread::proc(p:Pair){}','read(({2}))'),
        ]:
            with self.subTest(decl=decl):self.check(body,decl,fail='no_alloc:')

    def test_unknown_callbacks_and_growth_stay_conservative(self):
        for decl,body in [
            ('plus::proc(x:int)->int{return x+1}','callbacks:=[plus];_=callbacks[0](2)'),
            ('','values:[dynamic]int;append(&values,1)'),
        ]:
            with self.subTest(body=body):self.check(body,decl,fail='no_alloc:')

    def test_fixed_buffer_integer_formatting_only_reports_fatal_failures(self):
        self.check('buffer:[32]byte;_=strconv.itoa(buffer[:],3);_=strconv.write_int(buffer[:],i64(3),10)',
                   'import "core:strconv"')

    def test_specializations_and_branch_joins_cannot_hide_allocations(self):
        declarations='allocate::proc()->bool{_=make([]int,1);return true}\nread::proc(flag:bool)->bool{return flag?allocate():false}'
        self.check('_=read(false);_=read(true)',declarations,fail="builtin 'make'")
        self.check('flag:=false;set(&flag);_=flag&&allocate()',declarations+'\nset::proc(x:^bool){x^=true}',fail="builtin 'make'")
        self.check('flag=false;set();_=flag&&allocate()',declarations+'\nflag:bool\nset::proc(){flag=true}',fail="builtin 'make'")
        self.check('switch 1 {case 1:fallthrough;case 2:_=allocate()}',declarations,fail="builtin 'make'")

    def test_runtime_edge_paths_make_zero_allocations(self):
        code='''package main
import "core:mem"
import "core:strings"
import "core:strconv"
import "core:fmt"
Pair::struct{a,b:int}
Value::union{int,f64}
neg::proc(x:$T)->T{return -x}
read::proc(x:Value)->int{switch v in x{case int:return v;case f64:return int(v);case:return 0}}
@(no_alloc)
calculate::proc()->int{
 values:=[1,2,3];pointer:=&values;tail:=pointer[:][1:]
 bytes:=[4]byte{1,2,3,4};copy:[4]byte
 mem.copy(raw_data(copy[:]),raw_data(bytes[:]),4)
 text:=strings.trim_space(" 123 ");n,ok:=strconv.parse_int(text)
 if !ok {return -1}
 a,b:=expand_values(Pair{2,3});packed:=compress_values(a,b)
 z:=complex(f64(1),f64(2));components:=swizzle(values,2,0)
 total:=n+tail[0]+int(copy[2])+packed[0]+packed[1]+read(int(4))+int(real(z))+components[0]+neg(-5)
 for i in 0..<3 {if i==1 {continue};total+=i}
 return total
}
main::proc(){
 backing:=context.allocator;temp:=context.temp_allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 context.allocator=mem.tracking_allocator(&tracker);context.temp_allocator=context.allocator
 value:=calculate();count:=tracker.total_allocation_count
 context.allocator=backing;context.temp_allocator=temp
 mem.tracking_allocator_destroy(&tracker)
 if value!=148 || count!=0 {panic("no_alloc edge regression")}
 fmt.println("zero allocations")
}
'''
        self.compact_lowering(code)
        for flags in [[],base.UNCHECKED,['-debug']]:
            with self.subTest(flags=flags):self.compare(code,flags=['-thread-count:1',*flags],expected='zero allocations')


if __name__=='__main__':unittest.main()
