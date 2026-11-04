"""Locally owned mutation through inspectable ordinary procedures."""
import json
import unittest

from tests import test_thor as base


class PureMutationTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_scalar_pointer_runtime_automatic_and_explicit(self):
        source=self.program('n:=3;if work(n)!=10||AUTO!=11||EXPLICIT!=12{panic("result")}', '''
set_y::proc(y:^int){y^=7}
work::fn(x:int)->int{y:int;set_y(&y);return x+y}
AUTO::work(4)
EXPLICIT::#run work(5)
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_local_slice_and_array_elements_allow_mutation(self):
        self.invoke(self.program('n:=3;if work(n)!=10||VALUE!=10{panic("local buffers")}', '''
fill::proc(data:[]int){for i in 0..<len(data){data[i]=i+4}}
add::proc(value:^int){value^+=1}
work::fn(n:int)->int{data:=make([]int,n);fill(data);add(&data[0]);return data[0]+data[1]}
VALUE::#run work(3)
'''),flags=['-debug'])

    def test_borrowed_slice_mutation_is_still_rejected(self):
        text=self.program('data:=[1,2];_=work(data[:])', '''
fill::proc(data:[]int){data[0]=9}
work::fn(input:[]int)->int{fill(input);return 0}
''')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='borrowed from an input')
        errors=json.loads(result.stderr)['errors']
        self.assertTrue(any(e['pos'] and e['pos']['offset']==text.index('fill(input)') for e in errors),errors)
        self.assertTrue(any(note['pos']['offset']==text.index('data[0]=9')
                            for error in errors for note in error.get('related',[])),errors)

    def test_recursive_named_and_forwarded_pointer_calls(self):
        self.invoke(self.program('n:=3;if work(n)!=6||VALUE!=8{panic("forwarding")}', '''
leaf::proc(out:^int,n:int){out^+=n}
alias::leaf
middle::proc(n:int,out:^int){alias(n=n,out=out)}
recursive::proc(out:^int,n:int){if n==0{return};middle(out=out,n=1);recursive(out,n-1)}
work::fn(n:int)->int{local:=n;recursive(&local,n);return local}
VALUE::#run work(4)
'''),flags=['-debug'])

    def test_local_fields_and_shallow_borrowed_members_are_distinguished(self):
        prefix='''Box::struct{count:int,data:[]int}
count::proc(box:^Box){box.count+=1}
change::proc(box:^Box){box.data[0]+=1}
'''
        self.invoke(self.program('data:=[1,2];if work(data[:])!=2||data[0]!=1{panic("field storage")}',prefix+'''
work::fn(input:[]int)->int{local:=Box{1,input};count(&local);return local.count}
'''),flags=['-debug'])
        self.invoke(self.program('data:=[1,2];_=work(data[:])',prefix+'''
work::fn(input:[]int)->int{local:=Box{1,input};change(&local);return 0}
'''),command='check',fail='borrowed from an input')

    def test_out_parameter_buffers_survive_until_the_enclosing_fn_finishes(self):
        source=self.program('n:=3;if work(n)!=9||VALUE!=9{panic("output storage")}', '''
allocate::proc(out:^[]int,n:int){out^=make([]int,n);out^[0]=9}
work::fn(n:int)->int{data:[]int;allocate(&data,n);return data[0]}
VALUE::#run work(3)
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_reference_stores_keep_borrowed_storage_protected_after_the_call(self):
        self.invoke(self.program('data:=[1,2];_=work(data[:])', '''
redirect::proc(out:^[]int,input:[]int){out^=input}
change::proc(data:[]int){data[0]=99}
work::fn(input:[]int)->int{local:=make([]int,2);redirect(&local,input);change(local);return 0}
'''),command='check',flags=['-debug'],fail='borrowed from an input')

    def test_copying_reference_headers_through_a_helper_propagates_borrowing(self):
        prefix='replace::proc(dst,src:[][]int){copy(dst,src)}\n'
        self.invoke(self.program('data:=[1,2];_=work(data[:])',prefix+'''
work::fn(input:[]int)->int{
    local:=[9,10];rows:=[local[:]];source:=[input]
    replace(rows[:],source[:])
    rows[0][0]=7
    return rows[0][0]
}
'''),command='check',fail='borrowed from an input')
        self.invoke(self.program('n:=3;if work(n)!=7||VALUE!=7{panic("owned copied headers")}',prefix+'''
work::fn(n:int)->int{
    first:=[n,n];second:=[n+1,n+1];rows:=[first[:]];source:=[second[:]]
    replace(rows[:],source[:]);rows[0][0]=7
    return second[0]
}
VALUE::#run work(3)
'''),flags=['-debug'])

    def test_aliases_cannot_hide_a_borrowed_target(self):
        self.invoke(self.program('data:=[1,2];_=work(data[:],true)', '''
set::proc(value:^int){value^=99}
work::fn(input:[]int,flag:bool)->int{
    local:=7;pointer:=&local
    if flag{pointer=&input[0]}
    set(pointer)
    return local
}
'''),command='check',flags=['-debug'],fail='borrowed from an input')

    def test_value_generic_and_pointer_generic_helpers(self):
        self.invoke(self.program('n:=7;if work(n)!=10||VALUE!=10{panic("generic writes")}', '''
set::proc(out:^$T,value:T){out^=value}
forward::proc(out:$P,value:int){set(out,value)}
work::fn(n:int)->int{local:=n;alias:=&local;forward(alias,10);return local}
VALUE::#run work(7)
'''),flags=['-debug'])

    def test_compile_time_branches_keep_readonly_calls_lazy(self):
        self.invoke(self.program('data:=[1,2];if work(data[:])!=10||data[0]!=1{panic("lazy mutation")}', '''
maybe::proc(value:$T,data:[]int){when T==int{data[0]=value}}
work::fn(input:[]int)->int{maybe("skip",input);local:=[1,2];maybe(10,local[:]);return local[0]}
'''),flags=['-debug'])

    def test_native_odin_helpers(self):
        self.invoke(self.program('n:=3;if work(n)!=9||VALUE!=9{panic("native helpers")}', '''import "library"
work::fn(n:int)->int{local:=n;library.set(&local,9);return local}
VALUE::#run work(3)
'''),files={'library/main.odin':'package library\nset::proc(out:^int,value:int){out^=value}\n'},flags=['-debug'])

    def test_variadic_spreads_keep_borrowed_nested_elements_protected(self):
        prefix='''change::proc(rows:..[]int){rows[0][0]=9}
'''
        for call in ['change(..input)','change(**input)']:
            annotation='[1][]int' if '**' in call else '[][]int'
            text=self.program('data:=[1,2];rows:=[data[:]];_=work('+('rows' if '**' in call else 'rows[:]')+')',prefix+
                'work::fn(input:'+annotation+')->int{'+call+';return 0}\n')
            with self.subTest(call=call):self.invoke(text,command='check',flags=['-debug'],fail='borrowed from an input')
        self.invoke(self.program('n:=3;if work(n)!=9{panic("variadic local")}',prefix+'''
work::fn(n:int)->int{data:=[n,n];change(data[:]);return data[0]}
'''),flags=['-debug'])

    def test_stack_references_cannot_escape_through_out_parameters(self):
        for declaration,body in [
            ('escape::proc(out:^^int){local:=7;out^=&local}','pointer:^int;escape(&pointer);return 0'),
            ('escape::proc(out:^[]int){local:=[7,8];out^=local[:]}','data:[]int;escape(&data);return 0'),
            ('escape::proc(out:^[]int){out^=[]int{7,8}}','data:[]int;escape(&data);return 0'),
            ('escape::proc(out:^[]int){out^={7,8}}','data:[]int;escape(&data);return 0'),
            ('escape::proc(out:^[]int){out^=([7,8])[:]}','data:[]int;escape(&data);return 0'),
            ('Box::struct{data:[]int}\nescape::proc(out:^Box){out^=Box{{7,8}}}','data:Box;escape(&data);return 0'),
            ('escape::proc(out:[][]int){copy(out,[][]int{[]int{7,8}})}','rows:=[[]int{}];escape(rows[:]);return 0'),
            ('put::proc(out:^[]int,value:[]int){out^=value}\nescape::proc(out:^[]int){put(out,({7,8}))}','data:[]int;escape(&data);return 0'),
            ('put::proc(out:^[]int,value:[]int){out^=value}\nescape::proc(out:^[]int){put(value={7,8},out=out)}','data:[]int;escape(&data);return 0'),
            ('put::proc(out:^^int,value:^int){out^=value}\nescape::proc(out:^^int){local:=7;put(out,&local)}','pointer:^int;escape(&pointer);return 0'),
        ]:
            with self.subTest(declaration=declaration):
                self.invoke(self.program('_=work()',declaration+'\nwork::fn()->int{'+body+'}\n'),command='check',flags=['-debug'],fail='local stack storage')

    def test_pointer_identity_and_numeric_casts_remain_ineligible(self):
        for expression in ['&a==&b','uintptr(&a)==0','transmute(uintptr)&a==0']:
            with self.subTest(expression=expression):
                self.invoke(self.program('n:=3;_=work(n)','work::fn(n:int)->bool{a:=n;b:=n;return '+expression+'}\n'),command='check',fail='Pure')

    def test_pointer_identity_cannot_hide_in_membership_aggregates_or_switches(self):
        for expression in ['p in [q]','[p]==[q]','Record{p}==Record{q}']:
            text=self.program('data:=[1,2];rows:=[data[:],data[:]];_=work(rows[:])','''Record::struct{pointer:^int}
work::fn(input:[][]int)->bool{p:=&input[0][0];q:=&input[1][0];return '''+expression+'}\n')
            with self.subTest(expression=expression):self.invoke(text,command='check',fail='pointer identities')
        self.invoke(self.program('data:=[1,2];rows:=[data[:],data[:]];_=work(rows[:])','''
work::fn(input:[][]int)->bool{p:=&input[0][0];q:=&input[1][0];switch p{case q:return true;case:return false}}
'''),command='check',fail='pointer identities')

    def test_pointer_null_checks_and_type_reflection_keep_value_semantics(self):
        self.invoke(self.program('n:=3;if work(n)!=4||VALUE!=4{panic("pointer metadata")}', '''import "base:intrinsics"
change::proc(out:^int){out^+=1}
work::fn(n:int)->int{
    local:=n;p:=&local;if p==nil{panic("unexpected nil")}
    count::intrinsics.type_proc_parameter_count(type_of(change))
    if count!=1{panic("hidden parameters")}
    change(p);return local
}
VALUE::#run work(3)
'''),flags=['-debug'])

    def test_mutating_helpers_still_reject_transitive_external_effects(self):
        for prefix,statement,message in [
            ('state:int','out^=state','outside the function'),
            ('import "core:fmt"','fmt.println(out^)','performs I/O'),
            ('','_=context','ambient context'),
        ]:
            for usage in ['n:=3;_=work(n)','_=VALUE']:
                with self.subTest(statement=statement,usage=usage):
                    self.invoke(self.program(usage,prefix+'\nchange::proc(out:^int){'+statement+'}\nwork::fn(n:int)->int{local:=n;change(&local);return local}\n'+
                        ('VALUE::#run work(3)\n' if usage=='_=VALUE' else '')),command='check',flags=['-debug'],fail=message)

    def test_helper_cleanup_and_big_updates_use_the_enclosing_allocator(self):
        text=self.program('''n:=3
number:=work(n);defer delete(number)
if number!=4||consume(n)!=7{panic("mutator allocator")}
''','''drop::proc(data:[]int){delete(data)}
increase::proc(out:^Integer){out^+=1}
work::fn(n:int)->Integer{value:=Integer(n);increase(&value);return value}
consume::fn(n:int)->int{data:=make([]int,n);drop(data);return 7}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_compound_updates_cannot_mutate_borrowed_storage(self):
        for statement in ['data[0]+=1','data[0]-=1']:
            with self.subTest(statement=statement):
                self.invoke(self.program('data:=[1,2];_=work(data[:])','change::proc(data:[]int){'+statement+'}\nwork::fn(input:[]int)->int{change(input);return 0}\n'),command='check',fail='borrowed from an input')

    def test_reference_loops_and_union_views_of_local_storage(self):
        self.invoke(self.program('n:=3;if work(n)!=8||VALUE!=8{panic("local references")}', '''
Choice::union{int,bool}
increase::proc(data:[]int){for &item in data{item+=1}}
work::fn(n:int)->int{
    data:=[n,n];increase(data[:])
    choice:Choice=n
    switch &value in choice{case int:value+=1;case bool:_=value}
    selected:=choice.(int)
    return data[0]+selected
}
VALUE::#run work(3)
'''),flags=['-debug'])

    def test_output_pointer_redirection_preserves_caller_aliases(self):
        text=self.program('n:=3;if work(n)!=11||VALUE!=11{panic("redirect")}', '''
put::proc(out:^^int,value:^int){out^=value}
set::proc(out:^int){out^+=1}
work::fn(n:int)->int{local:=n;p:^int;put(&p,&local);set(p);return local+7}
VALUE::#run work(3)
''')
        self.invoke(text,flags=['-debug'])
        self.invoke(self.program('data:=[1,2];_=work(data[:])','''
put::proc(out:^^int,value:^int){out^=value}
set::proc(out:^int){out^+=1}
work::fn(input:[]int)->int{p:^int;put(&p,&input[0]);set(p);return 0}
'''),command='check',fail='borrowed from an input')

    def test_generic_pointer_provenance_survives_big_numeric_copy_dispatch(self):
        prefix='''USE_BIG::Integer(0)
set::proc(out:^$T){out^+=1}
forward::proc(out:$P){alias:=out;set(alias)}
'''
        self.invoke(self.program('n:=3;if work(n)!=4{panic("generic alias")}',prefix+'''
work::fn(n:int)->int{local:=n;forward(&local);return local}
'''),flags=['-debug'])
        self.invoke(self.program('data:=[1,2];_=work(data[:])',prefix+'''
work::fn(input:[]int)->int{forward(&input[0]);return input[0]}
'''),command='check',fail='borrowed from an input')

    def test_overloads_in_generic_mutating_calls_keep_each_binding(self):
        self.invoke(self.program('n:=3;f:=3.0;if work(n)!=4||work(f)!=5||VALUE!=4{panic("overloads")}', '''
set_int::proc(out:^int){out^+=1}
set_float::proc(out:^f64){out^+=2}
set::proc{set_int,set_float}
work::fn(n:$T)->T{local:=n;set(&local);return local}
VALUE::#run work(3)
'''),flags=['-debug'])

    def test_logical_big_storage_cannot_be_read_through_a_helper_pointer(self):
        for annotation in ['^Integer','^$T']:
            with self.subTest(annotation=annotation):
                self.invoke(self.program('n:=3;_=work(n)',
                    'peek::proc(value:'+annotation+'){_=value._value}\n'+
                    'work::fn(n:int)->int{local:=Integer(n);peek(&local);return n}\n'),
                    command='check',fail='implementation storage')
        text=self.program('n:=3;_=work(n)','''USE_BIG::Integer(0)
Box::struct{_value:int}
read::proc(a:^$A,b:^$B)->int{_=a._value;return b._value}
work::fn(n:int)->int{number:=Integer(n);ordinary:=Box{n};return read(&number,&ordinary)}
''')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='implementation storage')
        errors=json.loads(result.stderr)['errors']
        relevant=[error for error in errors if 'implementation storage' in ' '.join(error['msgs'])]
        self.assertEqual(relevant[0]['pos']['offset'],text.index('read(&number,&ordinary)'),errors)
        self.assertTrue(any(note['pos']['offset']==text.index('a._value')
                            for note in relevant[0]['related']),errors)

    def test_invalid_pointer_call_keeps_native_argument_errors(self):
        prefix='set::proc(out:^int,value:int){out^=value}\n'
        for call,message in [('set(&local)',"Parameter 'value'"),('set(&local,"bad")','Cannot convert')]:
            with self.subTest(call=call):
                _,result=self.invoke(self.program('n:=3;_=work(n)',prefix+
                    'work::fn(n:int)->int{local:=n;'+call+';return local}\n'),
                    command='check',fail=message)
                self.assertNotIn('checked_call',result.stderr)

    def test_invalid_overload_signatures_omit_permission_metadata(self):
        _,result=self.invoke(self.program('n:=3;_=work(n)','''
set_int::proc(out:^int){out^=1}
set_float::proc(out:^f64){out^=1}
set::proc{set_int,set_float}
work::fn(n:int)->int{local:="bad";set(&local);return n}
'''),command='check',fail='No procedures or ambiguous call')
        self.assertIn('set_int',result.stderr)
        self.assertIn('set_float',result.stderr)
        self.assertIn('^string',result.stderr)
        for hidden in ['Thor expression','checked_call','[0]u8','ownership']:
            self.assertNotIn(hidden,result.stderr)

    def test_explicit_run_ordinary_entry_has_the_same_storage_permissions(self):
        self.invoke(self.program('if VALUE!=8{panic("ordinary entry")}', '''
set::proc(out:^int){out^+=1}
entry::proc(n:int)->int{local:=n;set(&local);return local}
VALUE::#run entry(7)
'''))
        self.invoke(self.program('_=VALUE','''
set::proc(out:^int){out^+=1}
entry::proc(input:[]int)->int{set(&input[0]);return input[0]}
VALUE::#run entry([]int{7})
'''),command='check',fail='borrowed from an input')

    def test_mutator_output_result_is_promoted_and_scratch_is_reclaimed(self):
        text=self.program('''n:=3
SAVED:=#run work(3)
defer delete(SAVED)
for _ in 0..<4 {
    result:=work(n)
    if len(result)!=n||result[0]!=9{panic("promoted output")}
    delete(result)
}
if SAVED[0]!=9{panic("materialized output")}
''','''allocate::proc(out:^[]int,n:int){out^=make([]int,n);out^[0]=9}
work::fn(n:int)->[]int{local:[]int;allocate(&local,n);return local}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertEqual(result.stderr,'')


if __name__=='__main__':unittest.main()
