"""Slice write permissions, inferred effects and source-level diagnostics."""
import json
from pathlib import Path
import tempfile
import unittest

from tests import test_thor as base


class SliceAccessTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_readonly_view_is_not_a_frozen_allocation(self):
        source=self.program('''a:=make([]int,3);defer delete(a)
a[0]=1;b :> a;d:=a;d[0]=9
if b[0]!=9{panic("view must share storage")}
count:=len(b);doubled:=b[0]*2;count+=1;doubled+=1
if count!=4||doubled!=19{panic("scalar copies")}
pointer:=&a;pointer^[1]=2
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_direct_and_compound_element_writes_fail(self):
        for operation in ['s[0]=7','s[0]+=7','s[0],s[1]=7,8']:
            with self.subTest(operation=operation):
                self.invoke(self.program('s :> []int{1,2};'+operation),command='check',fail='read-only slice')

    def test_writable_aliases_and_addresses_fail(self):
        for operation in ['alias:=s;_=alias','alias:[]int;alias=s','p:=&s[0];_=p',
                          'p:=raw_data(s);_=p','for &v in s{v=7}']:
            with self.subTest(operation=operation):
                self.invoke(self.program('s :> []int{1,2};'+operation),command='check',fail='read-only slice')
        self.invoke(self.program('s :> []int{1,2};p:=&s;_=p'),command='check',fail='immutable storage')

    def test_slice_bounds_and_immutable_aliases_preserve_permission(self):
        self.invoke(self.program('s :> []int{1,2,3};v :> (s[1:]);if v[-1]!=3{panic("slice")};for n in v{_=n}'))
        self.invoke(self.program('s :> []int{1,2,3};v :> s[1:];v[-1]=7'),command='check',fail='read-only slice')

    def test_fixed_array_copies_and_struct_element_copies_are_independent(self):
        self.invoke(self.program('''a :> [1,2];b:=a;b[0]=9
rows :> []Row{{1},{2}};row:=rows[0];row.count=9
if a[0]!=1||rows[0].count!=1{panic("value copy")}
''','Row::struct{count:int}\n'))
        self.invoke(self.program('rows :> []Row{{1}};rows[0].count=9','Row::struct{count:int}\n'),command='check',fail='read-only slice')

    def test_numeric_array_components_and_swizzles_are_value_copies(self):
        prefix='''Vector::[4]f32
position::proc()->Vector{return {1,2,3,4}}
read::proc(values:[]Vector)->int{return int(values[0].x)+int(values[0].a)}
read_generic::proc(value:$T)->int{return int(value.y)+int(value.b)}
'''
        text=self.program('''m :> position()
x,y,z,w:=int(m.x),int(m.y),int(m.z),int(m.w)
r,g,b,a:=int(m.r),int(m.g),int(m.b),int(m.a)
if x+y+z+w!=10||r+g+b+a!=10{panic("component copies")}
pair:=m.yx;pair.x=20
if pair!=[f32(20),1]||m.y!=2{panic("swizzle copy")}
rows :> []Vector{{1,2,3,4}}
if read(rows)!=5||read_generic(m)!=5{panic("reader contracts")}
for row in rows{value:=int(row.x);if value!=1{panic("loop component")}}
''',prefix)
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):self.invoke(text,flags=flags)

    def test_foreign_numeric_array_results_have_typed_components(self):
        for call in ['rl.GetMousePosition()','GetMousePosition()']:
            with self.subTest(call=call):
                self.invoke(self.program('''using rl
position :> '''+call+'''
point:=[int(position.x),int(position.y)]
swizzled:=position.yx
_=point;_=swizzled
''','import rl "vendor:raylib"\n'),command='check')

    def test_using_namespace_preserves_reader_writer_and_returned_view_contracts(self):
        files={'library/main.odin':'''package library
Vector::[2]f32
position::proc()->Vector{return {1,2}}
read::proc(values:[]int)->int{return values[0]}
write::proc(values:[]int){values[0]=9}
view::proc(values:[]int)->[]int{return values}
'''}
        prefix='import "library"\n'
        self.invoke(self.program('''using library
values :> []int{7}
point :> position()
if int(point.x)+int(point.y)!=3||read(values)!=7{panic("namespace reader")}
''',prefix),files=files)
        for operation in ['write(values)','alias:=view(values);_=alias']:
            with self.subTest(operation=operation):
                self.invoke(self.program('using library;values :> []int{7};'+operation,prefix),
                            files=files,command='check',fail='read-only slice')

    def test_using_namespace_keeps_reviewed_reader_identity_and_local_shadowing(self):
        self.invoke(self.program('''using fmt
values :> []int{7}
println(values)
{println::proc(items:[]int){items[0]=9};writable:=[]int{1};println(writable)}
''','import "core:fmt"\n'),expected='[7]')
        self.invoke(self.program('''using fmt
values :> []int{7}
{println::proc(items:[]int){items[0]=9};println(values)}
''','import "core:fmt"\n'),command='check',fail='read-only slice')

    def test_array_component_and_swizzle_writes_require_writable_slice_storage(self):
        for operation in ['values[0].x=9','values[0].rg=[9,8]',
                          'pointer:=&values[0].x;pointer^=9']:
            with self.subTest(operation=operation):
                prefix='change::proc(values:[][2]int){'+operation+'}\n'
                text=self.program('values :> [][2]int{{1,2}};change(values)',prefix)
                self.invoke(text,command='check',fail='read-only slice')
                self.invoke(text.replace('values :>','values :='))

    def test_array_components_and_swizzles_preserve_nested_slice_permissions(self):
        for operation in ['alias:=rows.x;_=alias','rows.y[0]=9',
                          'pair:=rows.yx;pair[0][0]=9',
                          'write(rows.x)', 'write(rows.yx[0])']:
            with self.subTest(operation=operation):
                prefix='write::proc(values:[]int){values[0]=9}\n'
                self.invoke(self.program('rows :> [2][]int{{1},{2}};'+operation,prefix),
                            command='check',fail='read-only slice')
        self.invoke(self.program('rows :> [2][]int{{1},{2}};view :> rows.x;if view[0]!=1{panic("view") }'))

    def test_nested_slices_and_copied_record_members_stay_readonly(self):
        prefix='Row::struct{count:int,items:[]int}\n'
        for body in ['rows :> [][]int{{1,2}};rows[0][0]=9',
                     'rows :> []Row{{1,[]int{2}}};row:=rows[0];row.items[0]=9']:
            with self.subTest(body=body):self.invoke(self.program(body,prefix),command='check',fail='read-only slice')

    def test_pointer_fields_keep_existing_pointee_permissions(self):
        self.invoke(self.program('''node:=Node{1};nodes :> []^Node{&node};nodes[0]^.count=9
if node.count!=9{panic("pointer target")}
''','Node::struct{count:int}\n'))

    def test_reader_side_effects_do_not_impose_purity(self):
        self.invoke(self.program('s :> []int{7};if read(s)!=7||count!=1{panic("reader")}',
            'count:int\nread::proc(data:[]int)->int{count+=1;return data[0]}\n'))

    def test_reference_free_multiple_results_keep_each_scalar_type(self):
        prefix='''pair::proc()->(int,f32){return 2,3}
optional::proc()->(int,int,bool){return 2,3,true}
'''
        for expression in ['pair()','optional() or_else panic("status")','(optional() or_panic)']:
            with self.subTest(expression=expression):
                self.invoke(self.program('a,b :> '+expression+';copy:=b;if a!=2||copy!=3{panic("scalar copies")}',prefix))

    def test_buffer_formatters_write_only_the_buffer_and_read_other_slices(self):
        prefix='''import "core:fmt"
format::proc(buffer:[]byte,values:[]int)->string{return fmt.bprintf(buffer,"%v",values)}
'''
        text=self.program('''buffer:[64]byte
values :> []int{1,2}
if format(buffer[:],values)!="[1, 2]"{panic("buffer reader")}
if fmt.bprint(buffer[:],values)!="[1, 2]"{panic("bprint")}
if fmt.bprintln(buffer[:],values)!="[1, 2]\\n"{panic("bprintln")}
if fmt.bprintfln(buffer[:],"%v",values)!="[1, 2]\\n"{panic("bprintfln")}
''',prefix)
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):self.invoke(text,flags=flags)

    def test_buffer_formatter_contracts_reject_readonly_destinations(self):
        prefix='''import "core:fmt"
format::proc(buffer:[]byte)->string{return fmt.bprintf(buffer,"ok")}
'''
        for call in ['fmt.bprint(buffer,"ok")','fmt.bprintln(buffer,"ok")',
                     'fmt.bprintf(buf=buffer,fmt="ok")','fmt.bprintfln(buffer,"ok")',
                     'format(buffer)','fmt.bprintf(**struct{buf:[]byte,fmt:string}{buffer,"ok"})']:
            with self.subTest(call=call):
                self.invoke(self.program('buffer :> []byte{0,0,0,0};_='+call,prefix),
                            command='check',fail='read-only slice')

    def test_qualified_builtin_and_c_aliases_remain_scalar_values(self):
        prefix='''import b "base:builtin"
import "core:c"
import rl "vendor:raylib"
ScalarRecord::struct{x:c.int,y:c.uint,z:b.f32}
number::proc()->b.i32{return 7}
'''
        self.invoke(self.program('''value :> number();copy:=value
record :> ScalarRecord{1,2,3}
x,y,z:=int(record.x),int(record.y),int(record.z)
if int(copy)+x+y+z!=13{panic("qualified scalar types")}
''',prefix))
        self.invoke(self.program('''using rl
width :> GetScreenWidth()
height :> GetScreenHeight()
canvas :> LoadRenderTexture(width,height)
UnloadRenderTexture(canvas)
x,y:=int(width),int(height);_=x;_=y
''',prefix),command='check')
        self.invoke(self.program('values :> b.i32{1};copy:=values;_=copy','import b "local"\n'),
                    files={'local/types.thor':'package local\ni32::[]int\n'},command='check',fail='read-only slice')

    def test_writing_helpers_and_transitive_calls_fail_at_argument(self):
        text=self.program('s :> []int{7};middle(s)', '''
leaf::proc(data:[]int){data[0]=9}
middle::proc(data:[]int){leaf(data)}
''')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='read-only slice')
        errors=json.loads(result.stderr)['errors']
        relevant=[e for e in errors if 'read-only slice' in '\n'.join(e['msgs'])]
        self.assertEqual(relevant[0]['pos']['offset'],text.index('s)',text.index('main::')) if 'main::' in text else text.rindex('s)'))
        self.assertTrue(any(note['pos']['offset']==text.index('data[0]=9') for e in relevant for note in e.get('related',[])),errors)

    def test_per_name_mut_annotations(self):
        prefix='first::proc(#mut a,b:[]int){_=a;_=b}\nsecond::proc(a,#mut b:[]int){_=a;_=b}\nboth::proc(#mut a,#mut b:[]int){_=a;_=b}\n'
        self.invoke(self.program('a:=[]int{1};b :> a;first(a,b);second(b,a);both(a,a)',prefix))
        for call in ['first(b,a)','second(a,b)','both(a,b)','both(b,a)']:
            with self.subTest(call=call):self.invoke(self.program('a:=[]int{1};b :> a;'+call,prefix),command='check',fail='#mut')

    def test_explicit_mut_does_not_make_parameter_binding_writable(self):
        self.invoke(self.program('s:=[]int{1};change(s)','change::proc(#mut a:[]int){a=nil}\n'),command='check',fail='assign')

    def test_mut_annotation_validation(self):
        for declaration in ['f::proc(#mut n:int){}','f::proc(#mut p:^int){}','R::struct{#mut data:[]int}']:
            with self.subTest(declaration=declaration):self.invoke(self.program('',declaration),command='check',fail='#mut')

    def test_returned_views_retain_permissions(self):
        prefix='view::proc(data:[]int)->[]int{return data[1:]}\n'
        self.invoke(self.program('s :> []int{1,2};part :> view(s);if part[0]!=2{panic("returned view")}',prefix))
        for body in ['part:=view(s);_=part','part :> view(s);part[0]=9']:
            with self.subTest(body=body):self.invoke(self.program('s :> []int{1,2};'+body,prefix),command='check',fail='read-only slice')
        self.invoke(self.program('s:=[]int{1,2};part:=view(s);part[0]=9;if s[1]!=9{panic("writable view")}',prefix))

    def test_writable_global_escapes_fail(self):
        prefix='saved:[]int\nremember::proc(data:[]int){saved=data}\n'
        self.invoke(self.program('s :> []int{1,2};remember(s)',prefix),command='check',fail='read-only slice')

    def test_named_arguments_bind_effects_by_name(self):
        prefix='copy_values::proc(source,destination:[]int){copy(destination,source)}\n'
        self.invoke(self.program('a:=[]int{1};b :> a;c:=[]int{0};copy_values(destination=c,source=b);if c[0]!=1{panic("copy")}',prefix))
        self.invoke(self.program('a:=[]int{1};b :> a;copy_values(source=a,destination=b)',prefix),command='check',fail='read-only slice')

    def test_native_helpers_are_inspected(self):
        files={'library/main.odin':'package library\nread::proc(data:[]int)->int{return data[0]}\nwrite::proc(data:[]int){data[0]=9}\n'}
        self.invoke(self.program('s :> []int{7};if library.read(s)!=7{panic("read")}','import "library"\n'),files=files)
        self.invoke(self.program('s :> []int{7};library.write(s)','import "library"\n'),files=files,command='check',fail='read-only slice')

    def test_clone_is_writable_but_nested_clone_remains_shallow(self):
        self.invoke(self.program('s :> []int{1,2};c:=slice.clone(s);defer delete(c);c[0]=9;if s[0]!=1{panic("clone")}','import "core:slice"\n'))
        self.invoke(self.program('s :> [][]int{{1,2}};c:=slice.clone(s);defer delete(c);c[0][0]=9','import "core:slice"\n'),command='check',fail='read-only slice')

    def test_inactive_when_does_not_report_a_local_write(self):
        self.invoke(self.program('s :> []int{1};when false{s[0]=9}'))

    def test_references_in_comprehensions_preserve_permissions(self):
        self.invoke(self.program('s :> []int{1,2};c:=[x*2 for x in s];defer delete(c);c[0]=9;if s[0]!=1{panic("scalar comprehension")}'))
        self.invoke(self.program('s :> [][]int{{1,2}};c:=[x for x in s];defer delete(c);c[0][0]=9'),command='check',fail='read-only slice')

    def test_immutable_comprehension_is_readonly(self):
        self.invoke(self.program('s :> [x*2 for x in 0..<3];defer delete(s);s[0]=9'),command='check',fail='read-only slice')

    def test_readonly_views_of_immutable_fixed_arrays(self):
        self.invoke(self.program('a :> [1,2,3];s :> a[:];if s[1]!=2{panic("array view")}'))
        self.invoke(self.program('a :> [1,2,3];s:=a[:];_=s'),command='check',fail='read-only slice')

    def test_generic_readers_writers_and_mut_annotations(self):
        prefix='read::proc(data:[]$T)->T{return data[0]}\nwrite::proc(data:[]$T,value:T){data[0]=value}\nforced::proc(#mut data:$S){_=data}\n'
        self.invoke(self.program('s :> []i16{1,2};if read(s)!=1{panic("generic read")}',prefix))
        for call in ['write(s,i16(9))','forced(s)']:
            with self.subTest(call=call):self.invoke(self.program('s :> []i16{1,2};'+call,prefix),command='check',fail='read-only slice')

    def test_generic_value_copies_are_not_reference_escapes(self):
        self.invoke(self.program('s :> []int{1,2};if read(s)!=2{panic("value generic")}',
            'read::proc(data:$S/[]$T)->T{n:=data[0];return n+1}\n'))

    def test_recursive_and_mutually_recursive_effects(self):
        prefix='''leaf::proc(data:[]int,n:int){if n>0{middle(data,n-1)};data[0]=9}
middle::proc(data:[]int,n:int){if n>0{leaf(data,n-1)}}
'''
        self.invoke(self.program('s :> []int{1};middle(s,2)',prefix),command='check',fail='read-only slice')

    def test_variadic_readers_and_writers(self):
        prefix='read::proc(data:..int)->int{return data[0]}\nwrite::proc(data:..int){data[0]=9}\n'
        self.invoke(self.program('s :> []int{1,2};if read(..s)!=1{panic("varargs")}',prefix))
        self.invoke(self.program('s :> []int{1,2};write(..s)',prefix),command='check',fail='read-only slice')

    def test_returned_views_through_multiple_results(self):
        prefix='view::proc(data:[]int)->([]int,bool){return data[:],true}\n'
        self.invoke(self.program('s :> []int{1,2};part,ok :> view(s);if !ok||part[0]!=1{panic("multiple results")}',prefix))
        self.invoke(self.program('s :> []int{1,2};part,ok:=view(s);_=ok;_=part',prefix),command='check',fail='read-only slice')

    def test_pointer_output_parameter_cannot_expose_readonly_slice(self):
        self.invoke(self.program('s :> []int{1,2};out:[]int;redirect(&out,s)',
            'redirect::proc(out:^[]int,data:[]int){out^=data}\n'),command='check',fail='read-only slice')

    def test_map_and_any_storage_cannot_erase_permission(self):
        for body in ['m:=map[int][]int{1=s};m[1][0]=9;delete(m)',
                     'boxed:any=s;_=boxed']:
            with self.subTest(body=body):self.invoke('#+feature dynamic-literals\n'+self.program('s :> []int{1,2};'+body),command='check',fail='read-only slice')

    def test_slice_permission_survives_record_return(self):
        prefix='Box::struct{data:[]int}\nwrap::proc(data:[]int)->Box{return Box{data}}\nchange::proc(box:Box){box.data[0]=9}\n'
        self.invoke(self.program('s :> []int{1,2};box:=wrap(s);change(box)',prefix),command='check',fail='read-only slice')

    def test_native_slice_reverse_and_reader(self):
        self.invoke(self.program('s :> []int{1,2};_=slice.contains(s,1)','import "core:slice"\n'))
        self.invoke(self.program('s :> []int{1,2};slice.reverse(s)','import "core:slice"\n'),command='check',fail='read-only slice')

    def test_conditional_returned_aliases_are_conservative(self):
        prefix='choose::proc(a,b:[]int,which:bool)->[]int{return which?a:b}\n'
        self.invoke(self.program('a:=[]int{1};b :> []int{2};s:=choose(a,b,true);_=s',prefix),command='check',fail='read-only slice')

    def test_indirect_callbacks_require_writable_access(self):
        prefix='read::proc(data:[]int){_=len(data)}\napply::proc(callback:proc([]int),data:[]int){callback(data)}\n'
        self.invoke(self.program('s :> []int{1,2};apply(read,s)',prefix),command='check',fail='read-only slice')

    def test_deferred_mutation_and_mutation_inside_interpolation(self):
        prefix='change::proc(data:[]int)->int{data[0]=9;return 1}\n'
        for body in ['defer {s[0]=9}','text:=f"{change(s)}";delete(text)']:
            with self.subTest(body=body):self.invoke(self.program('s :> []int{1,2};'+body,prefix),command='check',fail='read-only slice')

    def test_shadowing_does_not_poison_independent_storage(self):
        self.invoke(self.program('s :> []int{1,2};{s:=[]int{3,4};s[0]=9};if s[0]!=1{panic("shadow")}'))

    def test_returned_view_does_not_restrict_the_original_writable_alias(self):
        self.invoke(self.program('a:=[]int{1,2};b :> a;part :> view(b);a[0]=9;if part[0]!=9{panic("alias permissions")}',
            'view::proc(data:[]int)->[]int{return data}\n'))

    def test_named_results_preserve_returned_view_permission(self):
        prefix='view::proc(data:[]int)->(out:[]int){out=data;return}\n'
        self.invoke(self.program('a :> []int{1,2};b :> view(a);if b[0]!=1{panic("named result")}',prefix))
        self.invoke(self.program('a :> []int{1,2};b:=view(a);_=b',prefix),command='check',fail='read-only slice')

    def test_implicit_any_parameters_cannot_erase_permissions(self):
        prefix='saved:any\nremember::proc(value:any){saved=value}\n'
        self.invoke(self.program('a :> []int{1,2};remember(a)',prefix),command='check',fail='read-only slice')
        self.invoke(self.program('a :> []int{1,2};boxed:=Box{a};_=boxed','Box::struct{value:any}\n'),
            command='check',fail='read-only slice')

    def test_builtin_aliases_and_shadowed_names(self):
        self.invoke(self.program('a :> []int{1,2};copy(a)','copy::proc(values:[]int){_=len(values)}\n'))
        self.invoke(self.program('a :> []int{1,2};c(a,[]int{9,9})','c::copy\n'),command='check',fail='read-only slice')

    def test_array_and_record_argument_spreads_preserve_permissions(self):
        prefix='read::proc(data:[]int)->int{return data[0]}\nwrite::proc(data:[]int){data[0]=9}\n'
        for container in ['[a]','struct{data:[]int}{a}']:
            with self.subTest(container=container):
                self.invoke(self.program('a :> []int{1,2};args:='+container+';if read(**args)!=1{panic("spread read")}',prefix))
                self.invoke(self.program('a :> []int{1,2};args:='+container+';write(**args)',prefix),command='check',fail='read-only slice')

    def test_generic_when_branches_use_actual_element_types(self):
        prefix='maybe::proc(data:[]$T){when T==int{data[0]=T(9)}}\n'
        self.invoke(self.program('a :> []f64{1,2};maybe(a)',prefix))
        self.invoke(self.program('a :> []int{1,2};maybe(a)',prefix),command='check',fail='read-only slice')

    def test_typed_slice_when_comparisons_do_not_confuse_element_types(self):
        prefix='maybe::proc(data:$T){when T==[]int{data[0]=9}}\n'
        self.invoke(self.program('a :> []f64{1,2};maybe(a)',prefix))
        self.invoke(self.program('a :> []int{1,2};maybe(a)',prefix),command='check',fail='read-only slice')

    def test_generic_mut_requires_a_slice_specialization(self):
        self.invoke(self.program('force(7)','force::proc(#mut data:$T){_=data}\n'),command='check',fail='#mut requires a slice')

    def test_union_assertions_and_switches_preserve_nested_slice_access(self):
        prefix='Cell::union{int,[]int}\n'
        for operation in ['value:=a[1].([]int) or_panic;value[0]=9',
                          'switch value in a[1]{case []int:value[0]=9;case int:}']:
            with self.subTest(operation=operation):
                self.invoke(self.program('a :> []Cell{int(7),[]int{1,2}};'+operation,prefix),command='check',fail='read-only slice')
        self.invoke(self.program('a :> []Cell{int(7),[]int{1,2}};switch value in a[1]{case []int:n:=value[0];n+=1;if n!=2{panic("union read")};case int:}',prefix))

    def test_compile_time_readers_accept_readonly_locals(self):
        prefix='''read::proc(data:[]int)->int{return data[0]}
work::fn()->int{values :> []int{7,8};return read(values)}
VALUE::#run work()
'''
        self.invoke(self.program('if VALUE!=7||work()!=7{panic("compile time readonly")}',prefix))

    def test_compile_time_execution_does_not_bypass_readonly_locals(self):
        prefix='''change::proc(data:[]int)->int{data[0]=9;return data[0]}
work::fn()->int{values :> []int{7,8};return change(values)}
VALUE::#run work()
'''
        self.invoke(self.program('_=VALUE',prefix),command='check',fail='read-only slice')

    def test_formatting_extrema_and_basic_readers(self):
        prefix='import * "thor:basic"\n'
        self.invoke(self.program('''a :> []rune{'a','b'}
text:=_string(a);defer delete(text)
_=hash(a)
if text!="ab"{panic("string reader")}
n,ok:=min(..a);if !ok||n!='a'{panic("extrema reader")}
''',prefix))

    def test_loop_carried_aliases_cannot_hide_a_readonly_reference(self):
        prefix='change::proc(data:[]int){data[0]=9}\n'
        self.invoke(self.program('a :> []int{1};box:=struct{data:[]int}{};for i in 0..<2{if i==0{box.data=a}else{change(box.data)}}',prefix),
            command='check',fail='read-only slice')

    def test_independent_result_from_reader_is_writable(self):
        self.invoke(self.program('a :> []int{7};b:=fresh(a);defer delete(b);b[0]=9;if a[0]!=7{panic("fresh result")}',
            'fresh::proc(data:[]int)->[]int{result:=make([]int,len(data));copy(result,data);return result}\n'))

    def test_named_clone_arguments_preserve_nested_readonly_elements(self):
        self.invoke(self.program('a :> [][]int{{1,2}};b:=slice.clone(allocator=context.allocator,a=a);defer delete(b);b[0][0]=9',
            'import "core:slice"\n'),command='check',fail='read-only slice')

    def test_deepclone_creates_an_independent_writable_nested_graph(self):
        self.invoke(self.program('''a :> [][]int{{1,2}}
source:=struct{rows:[][]int}{a}
cloned,err:=basic.deepclone(&source)
if err!=.None{panic("deepclone")}
defer basic.deepfree(cloned)
cloned.rows[0][0]=9
if a[0][0]!=1{panic("deepclone alias")}
''','import "thor:basic"\n'))

    def test_immutable_second_result_cannot_be_written(self):
        self.invoke(self.program('a:=[]int{1};number,view :> pair(a);_=number;view[0]=9',
            'pair::proc(data:[]int)->(int,[]int){return 1,data}\n'),command='check',fail='read-only slice')

    def test_conditional_immutable_bindings_follow_native_when_selection(self):
        prefix='''when #config(READONLY,false){data :> []int{1}}else{data:=[]int{1}}
change::proc(values:[]int){values[0]=9}
'''
        self.invoke(self.program('change(data);if data[0]!=9{panic("mutable branch")}',prefix))
        self.invoke(self.program('change(data)',prefix),command='check',flags=['-define:READONLY=true'],fail='read-only slice')

    def test_conditional_binding_keeps_permissions_through_local_aliases(self):
        prefix='when #config(READONLY,false){data :> []int{1}}else{data:=[]int{1}}\n'
        self.invoke(self.program('local:=data;local[0]=9',prefix))
        self.invoke(self.program('local:=data;local[0]=9',prefix),command='check',flags=['-define:READONLY=true'],fail='read-only slice')

    def test_compile_time_configuration_controls_inferred_mutation(self):
        prefix='change::proc(values:[]int){when #config(WRITE,false){values[0]=9}}\n'
        self.invoke(self.program('values :> []int{1};change(values)',prefix))
        self.invoke(self.program('values :> []int{1};change(values)',prefix),command='check',flags=['-define:WRITE=true'],fail='read-only slice')

    def test_native_type_traits_control_generic_mutation(self):
        prefix='import "base:intrinsics"\nchange::proc(values:[]$T){when intrinsics.type_is_integer(T){values[0]=T(9)}}\n'
        self.invoke(self.program('values :> []f64{1};change(values)',prefix))
        self.invoke(self.program('values :> []int{1};change(values)',prefix),command='check',fail='read-only slice')

    def test_string_slices_keep_existing_string_copy_semantics(self):
        self.invoke(self.program('text :> "hello";tail :> text[1:];copy:=tail;copy="other";if tail!="ello"||copy!="other"{panic("string slice")}'))

    def test_empty_nil_views_and_empty_clones_keep_their_permissions(self):
        prefix='import "core:slice"\nrequire_writable::proc(#mut values:[]int){_=len(values)}\n'
        self.invoke(self.program('values:[]int > nil;copy:=slice.clone(values);defer delete(copy);require_writable(copy)',prefix))
        self.invoke(self.program('values:[]int > nil;require_writable(values)',prefix),command='check',fail='#mut')

    def test_mut_is_not_a_reserved_identifier(self):
        self.invoke(self.program('mut:=7;if mut!=7{panic("identifier")}'))

    def test_conditional_view_in_do_body_preserves_diagnostics(self):
        prefix='when #config(READONLY,false){data :> []int{1}}else{data:=[]int{1}}\nchange::proc(values:[]int){values[0]=9}\n'
        self.invoke(self.program('if true do change(data)',prefix))
        self.invoke(self.program('if true do change(data)',prefix),command='check',flags=['-define:READONLY=true'],fail='read-only slice')

    def test_imported_conditional_view_tracks_native_selected_declaration(self):
        files={'library/main.thor':'package library\nwhen #config(READONLY,false){data :> []int{1}}else{data:=[]int{1}}\n'}
        prefix='import "library"\nchange::proc(values:[]int){values[0]=9}\n'
        self.invoke(self.program('change(library.data)',prefix),files=files)
        self.invoke(self.program('change(library.data)',prefix),files=files,command='check',flags=['-define:READONLY=true'],fail='read-only slice')

    def test_imported_views_and_native_consumers_cannot_drop_permission(self):
        files={'data/main.thor':'package data\nvalues :> []int{1,2}\n',
               'library/main.odin':'package library\nimport "../data"\nchange::proc(){data.values[0]=9}\n'}
        self.invoke(self.program('data.values[0]=9','import "data"\n'),files=files,command='check',fail='read-only slice')
        self.invoke(self.program('library.change()','import "library"\n'),files=files,command='check',fail='read-only slice')

    def test_malformed_mut_annotations_report_syntax_errors(self):
        for signature in ['proc(#mut)','proc(a,#mut)','proc(#mut #mut a:[]int)',
                          'proc(a,#mut #no_alias b:[]int)']:
            with self.subTest(signature=signature):
                _,result=self.invoke(self.program('', 'work::'+signature+'{}\n'),command='check',fail='#mut')
                self.assertNotIn('Traceback',result.stderr)

    def test_map_iteration_preserves_readonly_slice_values(self):
        self.invoke('#+feature dynamic-literals\n'+self.program('values :> []int{1,2};m:=map[int][]int{1=values};defer delete(m);for _,view in m{view[0]=9}'),
            command='check',fail='read-only slice')
        self.invoke('#+feature dynamic-literals\n'+self.program('values :> []int{1,2};m:=map[int][]int{1=values};defer delete(m);for _,view in m{if view[0]!=1{panic("map read")}}'))

    def test_dynamic_array_insertions_and_pop_preserve_readonly_elements(self):
        for operation in ['rows[0][0]=9','view :> pop(&rows);view[0]=9']:
            with self.subTest(operation=operation):
                self.invoke(self.program('values :> []int{1,2};rows:=make([dynamic][]int);defer delete(rows);append(&rows,values);'+operation),
                    command='check',fail='read-only slice')

    def test_typed_loop_indices_keep_scalar_generic_instantiations(self):
        prefix='read::proc(index:$I,values:[]int)->int{when I==i8{return values[index]};return 0}\n'
        self.invoke(self.program('values :> []int{7};sum:=0;for _,(i:i8) in values{sum+=read(i,values)};if sum!=7{panic("typed index")} ',prefix))

    def test_variadic_scalar_copies_do_not_require_writable_input(self):
        self.invoke(self.program('values :> []int{7};if read(..values)!=8{panic("variadic scalar copy")}',
            'read::proc(values:..int)->int{value:=values[0];return value+1}\n'))

    def test_variadic_any_cannot_hide_a_writable_escape(self):
        prefix='saved:any\nremember::proc(values:..any){saved=values[0]}\n'
        self.invoke(self.program('values :> []int{1};remember(values)',prefix),command='check',fail='read-only slice')

    def test_formatting_readonly_slices_borrows_without_exposing_writable_access(self):
        self.invoke(self.program('values :> []int{1,2};text:=f"{values}";defer delete(text);if len(text)==0{panic("format view")}'))

    def test_conditional_procedure_implementations_check_the_selected_effects(self):
        prefix='''when #config(WRITE,false){work::proc(values:[]int){values[0]=9}}
else{work::proc(values:[]int){_=values[0]}}
'''
        self.invoke(self.program('values :> []int{1};work(values)',prefix))
        self.invoke(self.program('values :> []int{1};work(values)',prefix),command='check',
                    flags=['-define:WRITE=true'],fail='read-only slice')
        self.invoke(self.program('values :> []int{1};alias(values)',prefix+'alias::work\n'),command='check',
                    flags=['-define:WRITE=true'],fail='read-only slice')

    def test_cloned_headers_and_pointer_builtin_aliases_keep_permissions(self):
        self.invoke(self.program('values :> []int{1};header:=new_clone(values);defer free(header);header^[0]=9'),command='check',fail='read-only slice')
        self.invoke(self.program('values :> []int{1};header:=new_clone(values);defer free(header);if header^[0]!=1{panic("cloned header")}'))
        self.invoke(self.program('values :> []int{1};data::raw_data;_=data(values)'),command='check',fail='read-only slice')

    def test_scalar_builtin_and_union_results_are_not_slice_views(self):
        prefix='Choice::union{int,[]int}\napply::proc(callback:proc(int)->int,n:int)->int{return callback(n)}\nidentity::proc(n:int)->int{return n}\n'
        self.invoke(self.program('''values :> []int{1,2};count :> len(values);size :> size_of(int)
small :> min(count,3);large :> max(count,3);distance :> abs(-count);bound :> clamp(count,1,3)
for n in [count,size,small,large,distance,bound]{if apply(identity,n)!=n{panic("scalar builtin")}}
choice :> Choice(7);number,ok :> choice.(int);if !ok||apply(identity,number)!=7{panic("scalar union")}
''',prefix))

    def test_shape_constrained_mut_parameter(self):
        prefix='change::proc(#mut values:$S/[]$T){values[0]=T(9)}\n'
        self.invoke(self.program('values:=[]i16{1};change(values);if values[0]!=9{panic("shape mut")}',prefix))
        self.invoke(self.program('values :> []i16{1};change(values)',prefix),command='check',fail='#mut')

    def test_returned_containers_and_headers_keep_nested_local_views(self):
        prefix='''make_map::proc()->map[int][]int{values :> []int{1};return map[int][]int{1=values}}
make_header::proc()->^[]int{values :> []int{1};return new_clone(values)}
'''
        for body in ['m:=make_map();m[1][0]=9','p:=make_header();p^[0]=9']:
            with self.subTest(body=body):self.invoke('#+feature dynamic-literals\n'+self.program(body,prefix),command='check',fail='read-only slice')

    def test_pure_result_cloning_restores_independent_writable_access(self):
        prefix='copy_values::fn(values:[]int)->[]int{return values}\n'
        self.invoke(self.program('''storage:=make([]int,1);defer delete(storage);storage[0]=1
values :> storage;result:=copy_values(values);defer delete(result);result[0]=9
if values[0]!=1||result[0]!=9{panic("independent fn result")}
''',prefix),flags=['-debug'])

    def test_when_compares_generic_record_arguments_not_only_the_record_name(self):
        prefix='''Box::struct($T:typeid){items:[]T}
work::proc(values:[]$T){when T==Box(int){_=values[0]}else{values[0].items[0]=9}}
'''
        self.invoke(self.program('values :> []Box(int){{[]int{1}}};work(values)',prefix))
        self.invoke(self.program('values :> []Box(f64){{[]f64{1}}};work(values)',prefix),command='check',fail='read-only slice')

    def test_previously_evaluated_dimensions_select_readonly_generic_paths(self):
        prefix='''size::proc()->int{return 2}
COUNT::#run size()
read::proc(values:[]$T){when T==[COUNT]int{_=values[0][0]}else{values[0][0]=9}}
'''
        self.invoke(self.program('values :> [][COUNT]int{{1,2}};read(values)',prefix))

    def test_editable_basic_library_bodies_are_checked_even_when_unused(self):
        library=base.ROOT/'runtime/basic/basic.thor'
        original=library.read_text()
        with tempfile.TemporaryDirectory(prefix='thor-slice-overlay-') as directory:
            root=Path(directory)
            replacement=root/'basic.thor'
            replacement.write_text(original+'\nbad_view::proc(){values :> []int{1};values[0]=9}\n')
            overlay=root/'overlay.json'
            overlay.write_text(json.dumps({'Replace':{str(library):str(replacement)}}))
            self.invoke(self.program('', 'import "thor:basic"\n'),command='check',flags=['--overlay',str(overlay)],fail='read-only slice')
        self.assertEqual(library.read_text(),original)

    def test_map_literal_key_calls_cannot_mutate_readonly_inputs(self):
        prefix='change::proc(values:[]int)->int{values[0]=9;return 1}\n'
        self.invoke('#+feature dynamic-literals\n'+self.program('values :> []int{1};table:=map[int]int{change(values)=1};delete(table)',prefix),
                    command='check',fail='read-only slice')

    def test_readonly_references_cannot_escape_in_map_or_set_keys(self):
        prefix='import "thor:sets"\n'
        for body in ['table:=map[^[]int]int{header=1};delete(table)',
                     'table:=make(map[^[]int]int);table[header]=1;delete(table)',
                     'table:=set[^[]int]{header};delete(table)',
                     'table:set[^[]int];sets.insert(&table,header);delete(table)',
                     'table:set[^[]int];table+=header;delete(table)']:
            with self.subTest(body=body):self.invoke('#+feature dynamic-literals\n'+self.program(
                'values :> []int{1};header:=new_clone(values);defer free(header);'+body,prefix),command='check',fail='read-only slice')

    def test_helpers_cannot_hide_nested_readonly_references_in_keys_or_globals(self):
        prefix='''saved:^[]int
remember::proc(header:^[]int){saved=header}
as_map::proc(header:^[]int)->map[^[]int]int{return map[^[]int]int{header=1}}
'''
        for body in ['remember(header)','table:=as_map(header);delete(table)']:
            with self.subTest(body=body):self.invoke('#+feature dynamic-literals\n'+self.program(
                'values :> []int{1};header:=new_clone(values);defer free(header);'+body,prefix),command='check',fail='read-only slice')

    def test_mutable_callbacks_and_overridden_defaults_cannot_borrow_an_initializer_contract(self):
        prefix='''reader::proc(values:[]int){_=values[0]}
writer::proc(values:[]int){values[0]=9}
apply::proc(values:[]int,callback:=proc(items:[]int){_=items[0]}){callback(values)}
'''
        for body in ['callback:=reader;callback=writer;callback(values)',
                     'apply(values,writer)']:
            with self.subTest(body=body):self.invoke(self.program('values :> []int{1};'+body,prefix),command='check',fail='read-only slice')
        self.invoke(self.program('values :> []int{1};callback :> reader;callback(values)',prefix))

    def test_opaque_returned_slices_cannot_erase_a_global_view(self):
        prefix='values :> []int{1}\nreader::proc()->[]int{return values}\n'
        self.invoke(self.program('callback:=reader;result:=callback();result[0]=9',prefix),command='check',fail='read-only slice')
        self.invoke(self.program('callback:=reader;result :> callback();if result[0]!=1{panic("callback view")}',prefix))
        self.invoke(self.program('callback:=reader;result :> callback();copy:=slice.clone(result);defer delete(copy);copy[0]=9',
                    'import "core:slice"\n'+prefix))


if __name__=='__main__':unittest.main()
