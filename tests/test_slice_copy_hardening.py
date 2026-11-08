"""Copy-versus-alias acceptance matrices for read-only slice permissions."""
import unittest

from tests import test_thor as base


class SliceCopyHardeningTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def check(self,body,prefix='',*,writable=True,flags=()):
        return self.invoke(self.program(body,prefix),command='check',flags=flags,
                           fail=None if writable else 'read-only slice')

    def test_scalar_and_inline_copy_matrix(self):
        payloads=[('int','7','value=9',''),
                  ('[2]int','{1,2}','value[0]=9',''),
                  ('Row','{7}','value.x=9','Row::struct{x:int}\n'),
                  ('Outer','{{7}}','value.inner.x=9','Row::struct{x:int}\nOuter::struct{inner:Row}\n')]
        for typ,literal,mutation,prefix in payloads:
            bodies=[f'value:=source[0];{mutation}',
                    f'for input in source{{value:=input;{mutation}}}',
                    f'copied:=[input for input in source];value:=copied[0];{mutation};delete(copied)',
                    f'copied:=slice.clone(source);value:=copied[0];{mutation};delete(copied)',
                    f'copied:=make([]{typ},1);copy(copied,source);value:=copied[0];{mutation};delete(copied)',
                    f'copied:[dynamic]{typ};append(&copied,..source);value:=copied[0];{mutation};delete(copied)']
            for index,body in enumerate(bodies):
                with self.subTest(type=typ,copy=index):
                    self.check(f'source :> []{typ}{{{literal}}};'+body,'import "core:slice"\n'+prefix)

    def test_nested_borrowed_fields_survive_every_shallow_copy(self):
        prefix='import "core:slice"\nRow::struct{count:int,items:[]int}\n'
        copies=['value:=source[0]',
                'values:=slice.clone(source);value:=values[0]',
                'values:=slice.clone_to_dynamic(source);value:=values[0]',
                'values:=[input for input in source];value:=values[0]',
                'values:=make([]Row,1);copy(values,source);value:=values[0]',
                'values:[dynamic]Row;append(&values,..source);value:=values[0]']
        for copy in copies:
            with self.subTest(copy=copy):
                self.check('source :> []Row{{7,[]int{1}}};'+copy+';value.count=9;value.items[0]=9',
                           prefix,writable=False)

    def test_reference_free_conditional_numeric_aliases(self):
        prefix='import "core:c"\n'
        for typ in ['c.long','c.ulong','c.wchar_t','c.int_fast32_t','c.uint_fast16_t']:
            with self.subTest(type=typ):self.check(f'original :> {typ}(7);copy:=original;_=int(copy)',prefix)
        prefix='Number::i8 when #config(SMALL,false) else i64\n'
        for flag in ['true','false']:
            with self.subTest(small=flag):
                self.check('original :> Number(7);copy:=original;_=copy',prefix,flags=['-define:SMALL='+flag])

    def test_conditionally_selected_reference_types_do_not_erase_views(self):
        prefix='Value::[]int when #config(REFERENCE,true) else int\n'
        self.check('value :> Value{1};copy:=value;_=copy',prefix,writable=False,flags=['-define:REFERENCE=true'])
        self.check('value :> Value(7);copy:=value;_=copy',prefix,flags=['-define:REFERENCE=false'])

    def test_union_scalar_narrowing_discards_other_variants_reference_facts(self):
        prefix='Choice::union{int,[]int}\n'
        for declaration,expression in [('choice :> Choice(int(7))','choice'),
                                       ('values :> []Choice{int(7)}','values[0]')]:
            with self.subTest(expression=expression):
                self.check(declaration+';switch value in '+expression+'{case int:_=i64(value);case []int:}',prefix)
        prefix+='read::proc(choice:Choice)->int{switch value in choice{case int:return value;case []int:return 0};return 0}\n'
        self.check('choice :> Choice(int(7));_=i64(read(choice))',prefix)

    def test_union_inline_copies_and_reference_variants(self):
        prefix='Row::struct{x:int}\nChoice::union{[2]int,Row,[]int}\n'
        for expr,mutation in [('Choice([2]int{1,2})','value,ok:=choice.([2]int);_=ok;value[0]=9'),
                              ('Choice(Row{7})','value,ok:=choice.(Row);_=ok;value.x=9')]:
            with self.subTest(expr=expr):self.check('choice :> '+expr+';'+mutation,prefix)
        self.check('choice :> Choice([]int{7});value,ok :> choice.([]int);_=ok;value[0]=9',prefix,writable=False)

    def test_default_parameters_are_checked_instead_of_skipped(self):
        for annotation,value,read in [('','7','copy:=view;_=copy'),
                                      ('','"hello"','copy:=view;_=copy'),
                                      ('','[1,2]','copy:=view;copy[0]=9')]:
            with self.subTest(default=value):
                self.check('use()','use::proc(value'+annotation+':='+value+'){view :> value;'+read+'}\n')
        for operation in ['view[0]=9','copy:=view;_=copy','p:=&view[0];_=p']:
            with self.subTest(operation=operation):
                self.check('use()','use::proc(value:=[]int{1}){view :> value;'+operation+'}\n',writable=False)

    def test_generic_specializations_enforce_views_without_guessing_element_shape(self):
        for operation in ['view[0]=9','copy:=view;_=copy']:
            with self.subTest(operation=operation):
                self.check('use(7)','use::proc(value:$T){view :> []int{1};'+operation+';_=value}\n',writable=False)
        self.check('use(7)','use::proc(value:$T){view :> value;copy:=view;_=copy}\n')
        self.check('value:=1;use(&value)','use::proc(value:$T){view :> value;view^=7}\n')
        prefix='Row::struct{items:[]int}\nuse::proc($T:typeid){rows :> []T{};_=raw_data(rows)}\n'
        self.check('use(Row)',prefix,writable=False)

    def test_new_clone_copies_inline_storage_without_losing_nested_views(self):
        prefix='Row::struct{x:int}\nclone_alias::new_clone\n'
        for call in ['new_clone(original)','clone_alias(original)',
                     'new_clone(allocator=context.allocator,data=original)']:
            with self.subTest(call=call):
                self.check('original :> Row{7};copy:='+call+';copy.x=9;free(copy)',prefix)
        prefix='Row::struct{items:[]int}\nclone_alias::new_clone\n'
        for call in ['new_clone(original)','clone_alias(original)',
                     'new_clone(allocator=context.allocator,data=original)']:
            for selector in ['copy.items[0]','copy^.items[0]']:
                with self.subTest(call=call,selector=selector):
                    self.check('original :> Row{[]int{1}};copy:='+call+';'+selector+'=9',prefix,writable=False)

    def test_swizzle_and_expand_values_preserve_per_result_permissions(self):
        self.check('original :> [1,2];copy:=swizzle(original,1,0,1);copy[0]=9')
        self.check('original :> [2][]int{{1},{2}};copy:=swizzle(original,1,0);copy[0][0]=9',writable=False)
        for prefix,source in [('', '[1,2]'),('Row::struct{x,y:int}\n','Row{1,2}')]:
            with self.subTest(source=source):
                self.check('original :> '+source+';first,second :> expand_values(original);copy:=second;_=first;_=copy',prefix)
        self.check('original :> Row{7,[]int{1}};n,view :> expand_values(original);copy:=n;_=copy;view[0]=9',
                   'Row::struct{number:int,items:[]int}\n',writable=False)

    def test_array_and_slice_overloads_keep_their_own_contracts(self):
        prefix='''choose::proc{from_slice,from_array}
from_slice::proc(values:[]int)->int{return values[0]}
from_array::proc(values:[$N]int)->int{copy:=values;copy[0]=9;return copy[0]}
'''
        self.check('view :> []int{7};array :> [7];_=choose(view);_=choose(array)',prefix)
        self.check('view :> []int{7};choose(view)',prefix.replace('return values[0]','values[0]=9;return values[0]'),writable=False)

    def test_normal_builds_do_not_analyze_test_only_slice_examples(self):
        files={'invalid_test.thor':'package main\nimport "core:testing"\n@(test)\ninvalid::proc(t:^testing.T){values :> []int{1};values[0]=9;_=t}\n'}
        self.invoke(self.program(''),files=files,directory=True,command='check')
        self.invoke(self.program(''),files=files,directory=True,command='test',fail='read-only slice')

    def test_allocating_set_conversion_reads_scalars_and_copies_keys(self):
        for source in ['[]int{1,2}','[1,2]']:
            with self.subTest(source=source):
                self.check('values :> '+source+';unique:=basic.to_set(values);copy:=basic.to_slice(unique);copy[0]=9;delete(copy);delete(unique)',
                           'import "thor:basic"\n')

    def test_basic_clone_remains_a_shallow_value_copy(self):
        prefix='import "thor:basic"\n'
        self.check('value :> [1,2];copy:=basic.clone(value);copy[0]=9',prefix)
        self.check('value :> []int{1,2};copy:=basic.clone(value);_=copy',prefix,writable=False)
        self.check('value :> Row{[]int{1}};copy:=basic.clone(value);copy.items[0]=9',
                   prefix+'Row::struct{items:[]int}\n',writable=False)

    def test_replacing_copied_headers_restores_only_the_replacement(self):
        prefix='Row::struct{items:[]int}\n'
        self.check('original :> Row{[]int{1}};copy:=original;copy.items=make([]int,1);copy.items[0]=9',prefix)
        self.check('original :> Row{[]int{1}};copy:=original;prior:=copy;copy.items=make([]int,1);prior.items[0]=9',prefix,writable=False)
        factory='factory::proc(original:Row)->Row{copy:=original;copy.items=make([]int,1);return copy}\n'
        self.check('original :> Row{[]int{1}};copy:=factory(original);copy.items[0]=9',prefix+factory)
        named='factory::proc(original:Row)->(copy:Row){copy=original;copy.items=make([]int,1);return}\n'
        self.check('original :> Row{[]int{1}};copy:=factory(original);copy.items[0]=9',prefix+named)

    def test_definite_replacement_at_both_branch_ends(self):
        prefix='Row::struct{items:[]int}\n'
        self.check('flag:=true;original :> Row{[]int{1}};copy:=original;'
                   'if flag{copy.items=make([]int,1)}else{copy.items=make([]int,2)};copy.items[0]=9',prefix)
        self.check('flag:=true;original :> Row{[]int{1}};copy:=original;'
                   'if flag{copy.items=make([]int,1)};copy.items[0]=9',prefix,writable=False)
        self.check('original :> Row{[]int{1}};copy:=original;when true{copy.items=make([]int,1)};copy.items[0]=9',prefix)

    def test_returned_alias_graph_is_preserved_across_multiple_results(self):
        prefix='''Row::struct{items:[]int}
factory::proc()->([]Row,[]Row){values:=make([]Row,1);return values,values}
'''
        self.check('a,b:=factory();readonly :> []int{1};a[0]=Row{readonly};b[0].items[0]=9',prefix,writable=False)

    def test_mixed_scalar_reference_results_keep_separate_facts(self):
        prefix='pair::proc(values:[]int)->(int,[]int,int,bool){return 7,values,9,true}\n'
        self.check('source :> []int{1};first,view,last :> pair(source) or_panic;'
                   'a,b:=first,last;_=a;_=b;_=view[0]',prefix)
        self.check('source :> []int{1};first,view,last := pair(source) or_panic;_=first;_=view;_=last',prefix,writable=False)

    def test_loop_and_deferred_paths_cannot_clear_a_borrowed_reference(self):
        prefix='Row::struct{items:[]int}\n'
        for control in ['for _ in 0..<1{copy.items=make([]int,1)}',
                        'defer copy.items=make([]int,1)']:
            with self.subTest(control=control):
                self.check('source :> Row{[]int{1}};copy:=source;'+control+';copy.items[0]=9',prefix,writable=False)
        self.check('source :> []int{1};if false{source[0]=9}',writable=False)

    def test_new_local_copies_inside_control_flow_can_replace_their_headers(self):
        prefix='import "core:slice"\nRow::struct{items:[]int}\n'
        body='copy:=source;copy.items=slice.clone(source.items);copy.items[0]=9;delete(copy.items)'
        for control in ['for _ in 0..<2{'+body+'}', 'if true{'+body+'}',
                        'switch 1{case 1:'+body+'}', 'defer{'+body+'}']:
            with self.subTest(control=control):self.check('source :> Row{[]int{1}};'+control,prefix)

    def test_copied_pointer_fields_keep_existing_pointee_access(self):
        prefix='Node::struct{items:[]int}\n'
        self.check('node:=Node{[]int{1}};values :> []^Node{&node};copy:=values[0];copy.items[0]=9',prefix)
        self.check('node:=Node{[]int{1}};values :> []^Node{&node};values[0]=nil',prefix,writable=False)

    def test_specializations_include_record_arguments_and_array_lengths(self):
        prefix='''Box::struct($T:typeid){value:T}
read::proc(values:[]$T){copy:=values[0].value;_=copy}
'''
        self.check('a :> []Box(int){{7}};b :> []Box([]int){{[]int{1}}};read(a);read(b)',prefix,writable=False)
        prefix='''use::proc(values:[]$T){when len(T{})==1{copy:=values[0];copy[0]=9}else{_=values[0]}}
'''
        self.check('a :> [][1]int{{7}};b :> [][2]int{{1,2}};use(a);use(b)',prefix)

    def test_structure_of_arrays_copies_and_borrowed_columns(self):
        self.check('a :> []int{1};b:=[]int{2};values :> soa_zip(a=a,b=b);row:=values[0];row.a=9;copy:=values.a[0];_=copy')
        for write in ['values.a[0]=9','for &row in values{row.a=9}',
                      'column :> soa_unzip(values);column[0]=9']:
            source='values :> soa_zip(a=a)' if 'soa_unzip' in write else 'values :> soa_zip(a=a,b=b)'
            with self.subTest(write=write):self.check('a :> []int{1};b:=[]int{2};'+source+';'+write,writable=False)
        self.check('a :> [][]int{{1}};values :> soa_zip(a=a);row:=values[0];row.a[0]=9',writable=False)
        self.check('a :> []int{1};values :> soa_zip(a=a);part :> values[:];part.a[0]=9',writable=False)

    def test_structure_of_arrays_contracts_are_column_specific(self):
        prefix='read::proc(values:#soa[]$T)->int{row:=values[0];copy:=row.a;return copy}\n'
        self.check('a :> []int{1};values :> soa_zip(a=a);_=read(values)',prefix)
        prefix='write::proc(values:#soa[]$T){values.b[0]=9}\n'
        self.check('a :> []int{1};b:=[]int{2};write(soa_zip(a=a,b=b))',prefix)
        self.check('a :> []int{1};b:=[]int{2};write(soa_zip(a=b,b=a))',prefix,writable=False)

    def test_copies_and_clones_are_independent_at_runtime_with_assertions_disabled(self):
        prefix='''import "core:slice"
import "thor:basic"
Row::struct{count:int,items:[]int}
factory::proc(source:Row)->Row{copy:=source;copy.items=make([]int,1);copy.items[0]=8;return copy}
'''
        body='''source := []Row{{7,[]int{1}}}
readonly :> source
row:=readonly[0];row.count=9
copied:=slice.clone(readonly);defer delete(copied);copied[0].count=10
fresh:=factory(row);defer delete(fresh.items);fresh.items[0]=11
if source[0].count!=7||source[0].items[0]!=1||copied[0].count!=10||fresh.items[0]!=11{panic("copy boundaries")}
array :> [1,2];swizzled:=swizzle(array,1,0);swizzled[0]=9
if array!=[1,2]||swizzled!=[9,1]{panic("swizzle copy")}
keys :> []int{1,2,2};unique:=basic.to_set(keys);defer delete(unique)
values:=basic.to_slice(unique);defer delete(values);values[0]=99
if len(unique)!=2{panic("set ownership")}
left :> []int{1};right:=[]int{2};zipped :> soa_zip(left=left,right=right)
ziprow:=zipped[0];ziprow.left=9
if left[0]!=1||right[0]!=2{panic("soa row copy")}
'''
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(self.program(body,prefix),flags=flags)
                self.assertNotIn('Thor allocation tracker:',result.stderr)


if __name__=='__main__':unittest.main()
