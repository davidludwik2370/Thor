"""Native-oracle SDK contracts and Rust-specific allocation-proof regressions."""
import json
from pathlib import Path
import unittest
import sys
import platform
from tests import test_rust_compiler as base

SINK='''sink::proc(data:rawptr,mode:io.Stream_Mode,bytes:[]byte,offset:i64,whence:io.Seek_From)->(i64,io.Error){return i64(len(bytes)),nil}
'''


class NoAllocSDKTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke

    def check(self,body,decl='',imports='',fail=None,flags=()):
        self.source.write_text('package main\n'+imports+'\n'+decl+'\n@(no_alloc)\nmain::proc(){'+body+'}\n')
        result=self.invoke('rust','check',flags=['-thread-count:1',*flags])
        if fail:
            self.assertNotEqual(result.returncode,0,result.stderr)
            self.assertIn(fail,result.stderr)
        else:self.assertEqual(result.returncode,0,result.stderr)
        return result

    def test_sdk_acceptance_matrix(self):
        cases=json.loads((base.ROOT/'tests/fixtures/no_alloc_sdk_cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']):
                if case.get('platforms') and sys.platform not in case['platforms']:continue
                if case.get('architectures') and platform.machine().lower() not in case['architectures']:continue
                expectation=case.get('platform_expect',{}).get(sys.platform,case['expect'])
                imports='\n'.join('import '+json.dumps('core:'+p) for p in case['imports'])
                self.check(case['body'],case.get('declarations',''),imports,
                           fail='no_alloc:' if expectation=='reject' else None,flags=case.get('flags',()))

    def test_crt_condition_selects_only_the_matching_native_implementation(self):
        for flags,inverted in [([],False),(['-no-crt'],True)]:
            safe_when='ODIN_NO_CRT' if inverted else '!ODIN_NO_CRT'
            decl='when '+safe_when+'{selected::proc(){}}else{selected::proc(){_=make([]int,1)}}'
            self.source.write_text('package main\n'+decl+'\n@(no_alloc) main::proc(){selected()}\n')
            required=['-no-thread-local','-default-to-nil-allocator','-build-mode:llvm-ir']
            good=self.invoke('rust','build',flags=[*flags,*required])
            self.assertEqual(good.returncode,0,good.stdout+good.stderr)
            bad=self.invoke('rust','build',flags=[*([] if flags else ['-no-crt']),*required])
            self.assertNotEqual(bad.returncode,0,bad.stderr)
            self.assertIn('no_alloc:',bad.stderr)
        self.check('data:[8]byte;out:=raw_data(data[:]);_=runtime.memset(out[2:],1,3)',
            imports='import "base:runtime"')

    def test_conditional_multipointer_storage_keeps_native_element_types(self):
        declaration='''fill::proc(values:[]$T, element:T, direct:bool){
 temp:[8]T
 out:=raw_data(values) if direct else raw_data(temp[:])
 out[0]=element
 out[1:][0]=element
}
'''
        self.check('values:[4]byte;fill(values[:],byte(3),true)',declaration)
        self.check('values:[4]f64;fill(values[:],f64(3),false)',declaration)
        self.check('values:[4]Integer;fill(values[:],Integer(3),true)',declaration,fail='no_alloc:')

    def test_callback_factories_aliases_forwarding_and_nested_records(self):
        for declaration in [
            'factory::proc()->io.Writer{return io.Writer{procedure=sink}}',
            'factory::proc()->io.Writer{writer:io.Writer;writer.procedure=sink;return writer}',
            'factory::proc()->(writer:io.Writer){writer.procedure=sink;return}',
            'create::proc()->io.Writer{return io.Writer{procedure=sink}}\nalias::create\nfactory::proc()->io.Writer{return alias()}',
            'relay::proc(value:$T)->T{return value}\nfactory::proc()->io.Writer{return relay(io.Writer{procedure=sink})}',
            'Box::struct{writer:io.Writer}\nfactory::proc()->io.Writer{box:Box;box.writer.procedure=sink;return box.writer}',
        ]:
            with self.subTest(declaration=declaration):
                self.check('writer:=factory();fmt.wprintln(writer,7)',SINK+declaration,'import "core:io"\nimport "core:fmt"')

    def test_return_identity_is_not_an_effect_exemption(self):
        self.check('writer:=factory();fmt.wprintln(writer,7)',SINK+
                   'factory::proc()->io.Writer{_=make([]int,1);return io.Writer{procedure=sink}}',
                   'import "core:io"\nimport "core:fmt"',fail="builtin 'make'")
        self.check('factory()(7)',
                   'printer::proc(value:int){}\nfactory::proc()->proc(int){_=make([]int,1);return printer}',
                   fail="builtin 'make'")

    def test_multiple_results_never_become_false_zero_generators(self):
        self.check('_,gen:=factory();_=rand.uint64(gen)',
                   'bad::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){_=make([]int,1)}\n'
                   'factory::proc()->(int,rand.Generator){return 7,rand.Generator{procedure=bad}}',
                   'import "base:runtime"\nimport "core:math/rand"',fail='no_alloc:')

    def test_implicit_boxing_cannot_preserve_mutated_callback_targets(self):
        declarations='safe::proc(){}\nbad::proc(){_=make([]int,1)}\n'
        for body,decl in [
            ('callback:=safe;change(callback);callback()', 'change::proc(value:any){p:=cast(^proc())value.data;p^=bad}'),
            ('box:=Box{safe};change(box);box.callback()', 'Box::struct{callback:proc()}\nchange::proc(value:any){p:=cast(^Box)value.data;p.callback=bad}'),
            ('callback:=safe;boxed:any=callback;change(boxed);callback()', 'change::proc(value:any){p:=cast(^proc())value.data;p^=bad}'),
            ('callback:=safe;boxed:=any(callback);change(boxed);callback()', 'change::proc(value:any){p:=cast(^proc())value.data;p^=bad}'),
            ('callback:=safe;change(callback);callback()', 'change::proc(#by_ptr value:proc()){mutate(value)}\nmutate::proc(value:any){p:=cast(^proc())value.data;p^=bad}'),
            ('callback:=safe;change(callback);callback()', 'change::proc(values:..any){p:=cast(^proc())values[0].data;p^=bad}'),
            ('callback:=safe;boxed:=[]any{callback};change(boxed[0]);callback()', 'change::proc(value:any){p:=cast(^proc())value.data;p^=bad}'),
        ]:
            with self.subTest(body=body):self.check(body,declarations+decl,fail='no_alloc:')

    def test_boxing_computed_copies_does_not_escape_their_inputs(self):
        for body in [
            'callback:=safe;_=any(size_of(callback));touch();callback()',
            'callback:=safe;_= []any{size_of(callback)};touch();callback()',
            'callback:=safe;_=Box{payload=7,callback=callback};touch();callback()',
            'callback:=safe;_=any(identity(callback));touch();callback()',
        ]:
            with self.subTest(body=body):
                self.check(body,'safe::proc(){}\ntouch::proc(){}\nBox::struct{payload:any,callback:proc()}\nidentity::proc(value:proc())->proc(){return value}')

    def test_native_boolean_widths_have_boolean_zero_values(self):
        for width in ['b8','b16','b32','b64']:
            with self.subTest(width=width):
                self.check(f'flag:{width};if flag==false{{_=make([]int,1)}}',fail="builtin 'make'")
                self.check(f'box:Box;if box.flag==false{{_=make([]int,1)}}',f'Box::struct{{flag:{width}}}',fail="builtin 'make'")
                self.check(f'flag:{width};inspect(flag)',f'inspect::proc(flag:{width}){{if flag==false{{_=make([]int,1)}}}}',fail="builtin 'make'")
                self.check(f'flag:{width};if flag{{_=make([]int,1)}}')
                imports='import "base:intrinsics"'
                self.check(f'flag:{width};inspect(flag)',
                    'inspect::proc(value:$T){when intrinsics.type_is_boolean(T){}else{_=make([]int,1)}}',imports)
                self.check(f'flag:{width};inspect(flag)',
                    'inspect::proc(value:$T){when intrinsics.type_is_boolean(T){_=make([]int,1)}}',imports,fail="builtin 'make'")

    def test_any_header_is_not_its_payload(self):
        self.check('pointer:^int;inspect(pointer)',
                   'inspect::proc(value:any){if value!=nil{_=make([]int,1)}}',fail='no_alloc:')
        self.check('inspect(Box{id=nil})',
                   'Box::struct{id:typeid}\ninspect::proc(value:any){if value.id!=nil{_=make([]int,1)}}',fail='no_alloc:')

    def test_implicit_numeric_conversions_cannot_hide_allocations(self):
        self.check('value:=u64(max(u64));inspect(value)',
                   'inspect::proc(#any_int value:int){if value == -1{_=make([]int,1)}}',fail='no_alloc:')
        self.check('inspect(16777217)',
                   'inspect::proc(value:f32){if value==16777216{_=make([]int,1)}}',fail='no_alloc:')
        self.check('value:f32=16777217;if value==16777216{_=make([]int,1)}',fail='no_alloc:')
        self.check('box:=Box{16777217};if 16777216==box.value{_=make([]int,1)}',
                   'Box::struct{value:f32}',fail='no_alloc:')

    def test_type_info_queries_still_evaluate_runtime_producers(self):
        self.check('_=type_info_of(producer())',
                   'producer::proc()->typeid{_=make([]int,1);return typeid_of(int)}',fail='no_alloc:')
        self.check('id:=typeid_of(int);_=type_info_of(id);_=type_info_of(int)')

    def test_instruction_catalog_has_only_reviewed_memory_effects(self):
        catalog=json.loads((base.ROOT/'rust/data/noalloc-instructions.json').read_text())
        self.assertGreater(len(catalog['entries']),100)
        for link,entry in catalog['entries'].items():
            with self.subTest(link=link):
                self.assertNotIn('IntrHasSideEffects',entry['properties'])
                self.assertTrue(set(entry['properties']) & {'IntrNoMem','IntrReadMem','IntrArgMemOnly'})
                self.assertFalse(any(part in link for part in ['threadlocal','coro.','gc.','sme.','stackmap','patchpoint']))
                self.assertTrue(entry['sources'])

    def test_runtime_casts_cannot_hide_an_allocation_branch(self):
        self.check('value:=256;small:=cast(u8)value;if small==0{_=make([]int,1)}',fail="builtin 'make'")

    def test_summary_equality_cannot_hide_an_allocating_branch(self):
        self.check('a:=Box{input};b:=Box{input};if a!=b{_=make([]int,1)}',
                   'Box::struct{value:int}\ninput:int',fail="builtin 'make'")
        self.check('a,b:=1,1;p:=&a;q:=&b;if p!=q{_=make([]int,1)}',fail="builtin 'make'")

    def test_failure_guards_still_evaluate_their_ordinary_arguments(self):
        self.check('runtime.bounds_check_error_contextless(loc(),1,1,0,1)',
                   'loc::proc()->string{_=make([]int,1);return "file"}',
                   'import "base:runtime"',fail="builtin 'make'")

    def test_sdk_summaries_reject_changed_source_overlays(self):
        for relative,body,imports in [
            ('core/slice/sort.odin','data:=[3]int{3,2,1};slice.sort(data[:])','import "core:slice"'),
            ('core/sys/posix/time.odin','_=time.now()','import "core:time"'),
        ]:
            with self.subTest(relative=relative):
                import subprocess
                root=Path(subprocess.run(['odin','root'],capture_output=True,text=True,check=True).stdout.strip())
                original=root/relative; replacement=self.root/'changed.odin'
                replacement.write_bytes(original.read_bytes()+b'\n// edited SDK\n')
                overlay=self.root/'overlay.json';overlay.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
                self.check(body,imports=imports,flags=['--overlay',str(overlay)],fail='effect profile does not match')

    def test_sdk_paths_have_no_runtime_allocations(self):
        code='''package main
import "core:fmt"
import "core:mem"
import "core:strings"
import "core:strconv"
import "core:slice"
import "core:unicode"
import "core:sync"
import "core:time"
import "core:crypto/sha2"
import "core:crypto/aes"
less::proc(a,b:$T)->bool{return a<b}
@(no_alloc)
exercise::proc()->bool{
 if !strings.contains("sample","amp")||strings.compare("abc","abd")>=0{return false}
 if !unicode.is_letter('É')||unicode.to_lower('É')!='é'{return false}
 number,ok:=strconv.parse_f64("1.25");if !ok||number!=1.25{return false}
 values:=[3]int{3,1,2};slice.sort_by(values[:],less)
 if (values!=[3]int{1,2,3}){return false}
 fixed:[dynamic;8]int;n:=append(&fixed,3,1,2);if n!=3{return false};ordered_remove(&fixed,0)
 if pop(&fixed)!=2{return false};clear(&fixed);if len(fixed)!=0{return false}
 value:u64;_=sync.atomic_add(&value,3);if sync.atomic_load(&value)!=3{return false}
 _=time.now();_=time.tick_now();time.sleep(0)
 ctx:sha2.Context_256;sha2.init_256(&ctx);sha2.update(&ctx,[]byte{1,2,3})
 digest:[32]byte;sha2.final(&ctx,digest[:]);if digest[0]!=3{return false}
 cipher:aes.Context_ECB;key:[16]byte;plaintext,ciphertext,zero:[16]byte
 aes.init_ecb(&cipher,key[:]);aes.encrypt_ecb(&cipher,ciphertext[:],plaintext[:])
 expected:=[16]byte{0x66,0xe9,0x4b,0xd4,0xef,0x8a,0x2c,0x3b,0x88,0x4c,0xfa,0x59,0xca,0x34,0x2b,0x2e}
 if ciphertext!=expected{return false}
 aes.decrypt_ecb(&cipher,plaintext[:],ciphertext[:]);if plaintext!=zero{return false}
 return true
}
main::proc(){
 tracker:mem.Tracking_Allocator;mem.tracking_allocator_init(&tracker,context.allocator)
 old:=context.allocator;old_temp:=context.temp_allocator
 context.allocator=mem.tracking_allocator(&tracker);context.temp_allocator=context.allocator
 ok:=exercise();count:=tracker.total_allocation_count
 context.allocator=old;context.temp_allocator=old_temp;mem.tracking_allocator_destroy(&tracker)
 if !ok||count!=0{panic("SDK result or allocation regression")}
 fmt.println("zero SDK allocations")
}
'''
        self.source.write_text(code)
        for flags in [[],base.UNCHECKED]:
            with self.subTest(flags=flags):
                result=self.invoke('rust','run',flags=['-thread-count:1',*flags])
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'zero SDK allocations')

    def test_reinterpreted_record_layout_cannot_reuse_callback_identities(self):
        declarations='A::struct{a,b:proc()}\nB::struct{b,a:proc()}\nsafe::proc(){}\nbad::proc(){_=make([]int,1)}'
        for conversion in ['cast(^B)raw','transmute(^B)raw','(^B)(raw)']:
            with self.subTest(conversion=conversion):
                self.check('value:=A{safe,bad};raw:=rawptr(&value);view:='+conversion+';view.a()',
                           declarations,fail='no_alloc:')
        self.check('value:=A{safe,bad};raw:=rawptr(&value);view:=cast(^A)raw;view.a()',declarations)

    def test_native_deferred_cleanup_paths_are_checked(self):
        for mode,declaration,body in [
            ('deferred_none','cleanup::proc(){}','work()'),
            ('deferred_in','cleanup::proc(value:$T){}','_=work(7)'),
            ('deferred_out','cleanup::proc(value:$T){}','_=work(7)'),
            ('deferred_in_out','cleanup::proc(a,b:$T){}','_=work(7)'),
            ('deferred_in_by_ptr','cleanup::proc(value:^$T){}','_=work(7)'),
            ('deferred_out_by_ptr','cleanup::proc(value:^$T){}','_=work(7)'),
            ('deferred_in_out_by_ptr','cleanup::proc(a,b:^$T){}','_=work(7)'),
        ]:
            with self.subTest(mode=mode):
                work='work::proc(){}' if mode=='deferred_none' else 'work::proc(value:$T)->T{return value}'
                declarations=declaration+'\n@('+mode+'=cleanup)\n'+work
                self.check(body,declarations)
                self.check(body,declarations.replace('cleanup::proc(){}','cleanup::proc(){_=make([]int,1)}').replace('){}','){_=make([]int,1)}'),fail="builtin 'make'")

    def test_fixed_capacity_generics_and_result_shapes(self):
        self.check('values:[dynamic;8]int;push(&values,1);n:=pop(&values);_=n+1',
                   'push::proc(values:^$T/[dynamic;$N]$E,value:E){_=append(values,value)}')
        self.check('grow(&values)', 'values:[dynamic]int\ngrow::proc(values:^[dynamic]int){_=append(values,1)}',fail="builtin 'append'")

    def test_short_sorts_skip_only_the_callback_invocation(self):
        declarations='bad::proc(a,b:int)->bool{_=make([]int,1);return a<b}'
        for prefix,data in [('', '[]int{}'),('values:=[1]int{1};','values[:]'),('values:=[3]int{1,2,3};','values[:1]')]:
            with self.subTest(data=data):self.check(prefix+'slice.sort_by('+data+',bad)',declarations,'import "core:slice"')
        self.check('slice.sort_by([]int{},factory())',declarations+'\nfactory::proc()->proc(int,int)->bool{_=make([]int,1);return bad}',
                   'import "core:slice"',fail="builtin 'make'")
        self.check('values:=[]int{};values=input;slice.sort_by(values,bad)',declarations+'\ninput:[]int','import "core:slice"',fail="builtin 'make'")

    def test_aggregate_arithmetic_result_types_keep_native_dimensions(self):
        declarations='expect::proc(value:matrix[2,4]f32){}\ninspect::proc(value:$T){when T==matrix[2,4]f32{_=make([]int,1)}}'
        self.check('a:matrix[2,3]f32;b:matrix[3,4]f32;result:=a*b;expect(result)',declarations)
        self.check('a:matrix[2,3]f32;b:matrix[3,4]f32;inspect(a*b)',declarations,fail="builtin 'make'")
        self.check('value:#simd[4]f32;inspect(f32(2)*value)',
                   'inspect::proc(value:$T){when T==#simd[4]f32{_=make([]int,1)}}',fail="builtin 'make'")
        self.check('value:#row_major matrix[2,3]f32;inspect(value)',
                   'inspect::proc(value:$M/matrix[$R,$C]$E){check(value)}\ncheck::proc(value:$T){when T==#row_major matrix[2,3]f32{_=make([]int,1)}}',fail="builtin 'make'")

    def test_native_matrix_types_and_storage_permissions(self):
        self.check('m:>matrix[2,2]f32{};m[0,0]=2',fail='immutable storage')
        self.check('m:#row_major matrix[2,2]f32;_=linalg.determinant(m)','', 'import "core:math/linalg"')
        self.check('m:matrix[2,2]f32;set(&m)', 'set::proc(value:^matrix[2,2]f32){value[0,0]=2}')
        self.check('m:#row_major matrix[2,2]f32;_=first(m)',
                   'first::proc(value:#row_major matrix[$R,$C]$E)->E{return value[0,0]}')
        self.check('m:matrix[2,2]f32;_=m[index(),0]',
                   'index::proc()->int{_=make([]int,1);return 0}',fail="builtin 'make'")
        self.check('m:#row_major matrix[2,2]f32;inspect(m)',
                   'inspect::proc(value:$T){when T==matrix[2,2]f32{}else{_=make([]int,1)}}',fail="builtin 'make'")

    def test_generic_when_callbacks_keep_their_selected_mutation_identity(self):
        declarations='safe::proc(){}\nbad::proc(){_=make([]int,1)}\nchange::proc(p:^proc()){p^=bad}\n'
        for body,fail in [
            ('when pick{callback:=safe}else{callback:=safe};callback()',None),
            ('when pick{callback:=safe}else{callback:=bad};callback()',"builtin 'make'"),
            ('when pick{callback:=safe}else{callback:=safe};change(&callback);callback()','no_alloc:'),
        ]:
            with self.subTest(body=body):
                self.check('choose(true);choose(false)',declarations+'choose::proc($pick:bool){'+body+'}',fail=fail)

    def test_generic_when_locals_use_the_selected_annotation(self):
        self.check('inspect(u64(1))',
                   'inspect::proc(value:$T){when T==u32{w:[2]u32}else{w:[2]u64};consume(w[0])}\nconsume::proc(value:u64){}')

    def test_comparators_are_checked_even_through_factories_and_aliases(self):
        imports='import "core:slice"'
        self.check('data:=[3]int{3,2,1};slice.sort_by(data[:],less)',
                   'less::proc(a,b:int)->bool{_=make([]int,1);return a<b}',imports,fail="builtin 'make'")
        self.check('data:=[3]int{3,2,1};alias::slice.sort_by;alias(data[:],less)',
                   'less::proc(a,b:$T)->bool{return a<b}',imports)
        self.check('data:=[3]int{3,2,1};slice.sort_by(data[:],factory())',
                   'less::proc(a,b:int)->bool{_=make([]int,1);return a<b}\nfactory::proc()->proc(int,int)->bool{return less}',
                   imports,fail="builtin 'make'")

    def test_comparator_userdata_is_not_assumed_stable_between_calls(self):
        self.check('data:=[3]int{3,2,1};box:=Box{safe};slice.sort_by_with_data(data[:],less,&box)',
                   'Box::struct{callback:proc()}\nsafe::proc(){}\nbad::proc(){_=make([]int,1)}\n'
                   'less::proc(a,b:int,data:rawptr)->bool{box:=cast(^Box)data;box.callback();box.callback=bad;return a<b}',
                   'import "core:slice"',fail='no_alloc:')

    def test_native_alias_identity_and_opaque_bitset_guards_never_hide_effects(self):
        for actual,expected,equal in [('byte','u8',True),('rune','i32',False),
                                      ('map[byte]int','map[u8]int',True)]:
            for operator in ('==','!='):
                with self.subTest(actual=actual,operator=operator):
                    self.check('item:'+actual+';inspect(item)',
                               'inspect::proc(value:$T){when T'+operator+expected+'{_=make([]int,1)}}',
                               fail="builtin 'make'" if equal==(operator=='==') else None)
        self.check('inspect(bit_set[0..<4]{})',
                   'inspect::proc(value:$T){when T==bit_set[0..<4]{_=make([]int,1)}}',fail="builtin 'make'")

    def test_native_pointer_and_enumerated_array_guards_never_hide_allocations(self):
        for typ,condition in [('rawptr','i.type_is_pointer(T)'),('[^]int','!i.type_is_pointer(T)'),
                              ('[^]int','i.type_is_multi_pointer(T)'),('[^]int','T!=^int'),
                              ('[E]int','!i.type_is_array(T)'),('set[int]','i.type_is_struct(T)')]:
            with self.subTest(typ=typ,condition=condition):
                self.check('v:'+typ+';inspect(v)','E::enum{a,b}\n'
                           'inspect::proc(v:$T){when '+condition+'{_=make([]int,1)}}',
                           'import i "base:intrinsics"',fail="builtin 'make'")

        self.check('when !i.type_is_pointer(#soa^#soa[]Row){_=make([]int,1)}',
                   'Row::struct{x,y:int}', 'import i "base:intrinsics"', fail="builtin 'make'")

    def test_inline_native_type_identity_and_bit_field_reflection(self):
        for typ in ('struct{x:int}', '[]struct{x:int}', 'enum{a,b}', 'union{int,string}'):
            with self.subTest(typ=typ):
                self.check('v:'+typ+';inspect(v)',
                           'inspect::proc(v:$T){when T=='+typ+'{_=make([]int,1)}}',
                           fail="builtin 'make'")
        self.check('v:Bits;inspect(v)', 'Bits::bit_field u8{x:u8|2}\n'
                   'inspect::proc(v:$T){when !i.type_is_struct(T){_=make([]int,1)}}',
                   'import i "base:intrinsics"', fail="builtin 'make'")

    def test_soa_storage_and_ordinary_collections_select_native_branches(self):
        for typ, ordinary in [('#soa[2]Row','[2]Row'),('#soa[dynamic]Row','[dynamic]Row')]:
            for actual, is_soa in [(typ,True),(ordinary,False)]:
                for condition in ('i.type_is_struct(T)', 'T=='+typ, 'T!='+ordinary):
                    with self.subTest(actual=actual,condition=condition):
                        self.check('v:'+actual+';inspect(v)', 'Row::struct{x,y:int}\n'
                                   'inspect::proc(v:$T){when '+condition+'{_=make([]int,1)}}',
                                   'import i "base:intrinsics"', fail="builtin 'make'" if is_soa else None)

    def test_indexed_and_iterated_values_select_the_native_effects(self):
        cases = [
            ('s:Wide;inspect(s[0])', 'u16'),
            ('s:Wide;p:=&s;inspect(p[0])', 'u16'),
            ('s:Wide;inspect(raw_data(s)[0])', 'u16'),
            ('a:[2]int;inspect(raw_data(&a)[0])', 'int'),
            ('a:[2][3]int;inspect(raw_data(&a)[0])', '[3]int'),
            ('m:Mapping;for k in m{inspect(k)}', 'u16'),
            ('m:Mapping;for _,v in m{inspect(v)}', 'string'),
            ('s:Wide;for r in s{inspect(r)}', 'rune'),
            ('a:[E]int;for _,i in a{inspect(i)}', 'E'),
            ('b:bit_set[E];for e in b{inspect(e)}', 'E'),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.check(body, 'Wide::distinct string16\nMapping::distinct map[u16]string\nE::enum{A,B}\n'
                           'inspect::proc(v:$T){when T=='+expected+'{_=make([]int,1)}}', fail='no_alloc:')
        self.check('s:string;inspect(s[0]);inspect(raw_data(s)[0])',
                   'inspect::proc(v:$T){when T==u16{_=make([]int,1)}}')

    def test_native_vector_and_array_type_conditions_remain_distinct(self):
        imports='import "base:intrinsics"'
        self.check('value:#simd[4]f32;inspect(value)',
                   'inspect::proc(value:$T){when intrinsics.type_is_array(T){}else{_=make([]int,1)}}',
                   imports,fail="builtin 'make'")
        self.check('value:#simd[4]f32;inspect(value)',
                   'inspect::proc(value:$T){when intrinsics.type_is_simd_vector(T){}else{_=make([]int,1)}}',imports)

    def test_labelled_native_statements_do_not_become_value_reads(self):
        self.check('outer: if true {break outer};block:{break block};loop:for i in 0..<2{break loop};'
                   'select:switch 1{case 1:break select;case:}',flags=base.UNCHECKED[1:])


if __name__=='__main__':unittest.main()
