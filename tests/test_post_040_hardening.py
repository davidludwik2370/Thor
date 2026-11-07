"""Cross-feature regressions collected after the general purity hardening pass."""
import unittest

from tests import test_thor as base


class RecentFeatureHardeningTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_mutation_checks_visit_all_executable_expression_positions(self):
        prefix='change::proc(values:[]int)->int{values[0]+=1;return values[0]}\n'
        bodies=[
            'answer:=switch change(values){case 2:7;case:8};_=answer',
            'answer:=switch 2{case change(values):7;case:8};_=answer',
            'if n:=change(values);n>0{_=n}',
            'answer:=map[int]int{change(values)=x for x in 0..<1};delete(answer)',
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.invoke(self.program('values:=[]int{1};'+body,prefix))
                self.invoke(self.program('values :> []int{1};'+body,prefix),command='check',fail='read-only slice')

    def test_comprehension_keys_cannot_erase_nested_reference_permissions(self):
        for body in ['table:=map[^[]int]int{header=x for x in 0..<1};delete(table)',
                     'table:=set[^[]int]{header for x in 0..<1};delete(table)']:
            with self.subTest(body=body):
                self.invoke(self.program('values :> []int{1};header:=new_clone(values);defer free(header);'+body),
                            command='check',fail='read-only slice')

    def test_parenthesized_multi_results_preserve_each_result_permission(self):
        prefix='pair::proc(values:[]int)->(int,[]int){return 7,values}\n'
        for body in ['_,alias :> (pair(values));alias[0]=9',
                     '_,alias := (pair(values));_=alias']:
            with self.subTest(body=body):self.invoke(self.program('values :> []int{1};'+body,prefix),
                                                    command='check',fail='read-only slice')

    def test_forwarded_multi_result_returns_preserve_views(self):
        prefix='''pair::proc(values:[]int)->(int,[]int){return 7,values}
forward::proc(values:[]int)->(int,[]int){return pair(values)}
'''
        self.invoke(self.program('values :> []int{1};_,alias :> forward(values);alias[0]=9',prefix),
                    command='check',fail='read-only slice')

    def test_optional_multi_success_results_preserve_views(self):
        prefix='pair::proc(values:[]int)->(int,[]int,bool){return 7,values,true}\n'
        for body in ['_,alias :> pair(values) or_panic;alias[0]=9',
                     '_,alias := pair(values) or_panic;_=alias']:
            with self.subTest(body=body):self.invoke(self.program('values :> []int{1};'+body,prefix),
                                                    command='check',fail='read-only slice')

    def test_optional_return_and_loop_branches_preserve_later_slice_results(self):
        prefix='''pair::proc(values:[]int)->(int,[]int,bool){return 7,values,true}
forward::proc(values:[]int)->([]int,bool){_,view :> (pair(values) or_return);return view,true}
'''
        for body in ['_,view :> pair(values) or_continue;view[0]=9',
                     '_,view :> pair(values) or_break;view[0]=9']:
            with self.subTest(body=body):
                self.invoke(self.program('values :> []int{1};for _ in 0..<1{'+body+'}',prefix),
                            command='check',fail='read-only slice')
        self.invoke(self.program('values :> []int{1};view :> forward(values) or_panic;view[0]=9',prefix),
                    command='check',fail='read-only slice')

    def test_allocator_enum_aliases_and_unrelated_names_keep_their_real_shapes(self):
        for imports,annotation in [('import rt "base:runtime"','rt.Allocator_Error'),
                                   ('import _ "base:runtime" / Allocator_Error','Allocator_Error')]:
            with self.subTest(imports=imports):
                self.invoke(self.program('values,err :> allocate();copy:=err;_=copy;defer delete(values)',
                    imports+'\nError::'+annotation+'\nallocate::proc()->([]int,Error){return make([]int,1)}\n'))
        self.invoke(self.program('values :> []int{1};record :> fake.Allocator_Error{values};copy:=record;copy.items[0]=9',
            'import fake "statuses"\n'),files={'statuses/main.odin':'package statuses\nAllocator_Error::struct{items:[]int}\n'},
            command='check',fail='read-only slice')

    def test_multi_result_arguments_keep_readonly_values(self):
        prefix='''pair::proc(values:[]int)->(int,[]int){return 7,values}
write::proc(n:int,values:[]int){values[0]=n}
'''
        self.invoke(self.program('values:=[]int{1};write(pair(values));if values[0]!=7{panic("tuple arguments")}',prefix))
        self.invoke(self.program('values :> []int{1};write(pair(values))',prefix),command='check',fail='read-only slice')

    def test_optional_status_copies_are_independent_scalar_values(self):
        prefix='Choice::union{int,[]int}\n'
        for body in ['choice :> Choice([]int{1});view,ok :> choice.([]int);flag:=ok;flag=false;_=view;_=flag',
                     'values :> []int{1};table:=map[int][]int{1=values};defer delete(table);view,ok :> table[1];flag:=ok;flag=false;_=view;_=flag']:
            with self.subTest(body=body):self.invoke('#+feature dynamic-literals\n'+self.program(body,prefix))

    def test_allocator_status_is_not_a_slice_alias(self):
        self.invoke(self.program('''values,err :> make([]int,1)
if err!=nil{panic("allocation")};defer delete(values)
error_copy:=err;_=error_copy
'''))

    def test_reviewed_library_statuses_are_independent_of_their_owned_results(self):
        self.invoke(self.program('''text,err :> basic._string(12);if err!=nil{panic("string")};defer delete(text)
error_copy:=err;_=error_copy
code,hash_error :> basic.try_hash([]int{1});hash_copy:=hash_error;_=code;_=hash_copy
number:=7;root,clone_error :> basic.deepclone(&number)
if clone_error!=nil{panic("clone")};defer basic.deepfree(root)
clone_copy:=clone_error;_=clone_copy
''','import "thor:basic"\n'),flags=['-debug'])

    def test_forwarded_allocator_statuses_keep_enum_identity(self):
        prefix='''import "base:runtime"
import "base:intrinsics"
allocate::proc()->([]int,runtime.Allocator_Error){return make([]int,1)}
check::proc(err:$E)->bool{when intrinsics.type_is_enum(E){return err==nil};return false}
'''
        self.invoke(self.program('values,err :> allocate();if !check(err){panic("allocator status")};defer delete(values);copy:=err;_=copy',prefix))

    def test_optional_reader_calls_and_forwarding_keep_native_arity(self):
        prefix='''maybe::proc(values:[]int)->([]int,bool) #optional_ok{return values,true}
forward::proc(values:[]int)->([]int,bool) #optional_ok{return maybe(values)}
read::proc(values:[]int)->int{return values[0]}
'''
        self.invoke(self.program('values :> []int{7};a:=read(maybe(values));b:=read(forward(values));if a+b!=14{panic("optional arity")}',prefix))
        self.invoke(self.program('values :> []int{7};view :> forward(values) or_else []int{};if read(view)!=7{panic("fallback")}',prefix))
        self.invoke(self.program('values :> []int{7};view :> forward(values) or_else []int{};view[0]=9',prefix),
                    command='check',fail='read-only slice')

    def test_opaque_callback_secondary_slices_remain_readonly(self):
        prefix='''Callback::proc()->(int,[]int)
pair::proc()->(int,[]int){return 7,[]int{1}}
work::proc(callback:Callback){_,view :> (callback());view[0]=9}
'''
        self.invoke(self.program('work(pair)',prefix),command='check',fail='read-only slice')

    def test_compiletime_unselected_views_do_not_taint_the_selected_value(self):
        for expression in ['mutable when true else readonly',
                           '#switch{case false:readonly;case true:mutable;case:readonly}',
                           '#switch 2{case 1:readonly;case 2:mutable;case:readonly}']:
            with self.subTest(expression=expression):
                self.invoke(self.program('readonly :> []int{1};mutable:=[]int{2};selected:='+expression+';selected[0]=7;if mutable[0]!=7{panic("selected branch")}'))

    def test_recent_features_together_in_checked_and_unchecked_debug_builds(self):
        prefix='''import "thor:basic"
import "core:mem"
JOINED::[1,2] ++ [3,4]
EVENS::[x*x for x in JOINED if x%2==0]
bump::proc(#mut values:[]int){values[0]+=1}
owned::fn()->[dynamic]int{
    values:[dynamic]int
    append(&values,3)
    bump(values[:])
    return values
}
maybe::proc(x:int)->(int,bool){return x*2,x>1}
'''
        body='''parent:=context.allocator
{
    context.allocator=basic.scoped_arena()
    first:=#run owned()
    second:=owned()
    first[0]=9
    if second[0]!=4 || EVENS!=[4,16] {panic("independent results/constants")}
    values :> [maybe(x) or_else 7 for x in JOINED]
    low,ok :> min(..values)
    found:=ok
    if !found || low!=4 {panic("optional minimum")}
    text,error :> basic._string([]rune{'a','界','😀'})
    status:=error
    if status!=nil || text!="a界😀" {panic("conversion")}
    code,hash_error :> basic.try_hash(values)
    hash_status:=hash_error
    if hash_status!=nil || code!=basic.hash(values) {panic("hash")}
    number:Integer=123456789012345678901234567890
    fraction:=Rational(1,3)
    width:=5
    basic.print(f"{text} {number:,} {fraction:.2f} {values[0]:{width}d}")
}
if context.allocator!=parent {panic("allocator restoration")}
'''
        for flags in [['-debug'],['-debug','-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(self.program(body,prefix),flags=flags,
                    expected='a界😀 123,456,789,012,345,678,901,234,567,890 0.33     7')
                self.assertNotIn('Thor allocation tracker:',result.stderr)
                self.assertNotIn('AddressSanitizer:',result.stderr)


if __name__=='__main__':unittest.main()
