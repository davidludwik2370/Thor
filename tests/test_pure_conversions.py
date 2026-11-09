"""Pure conversions validate captured type parameters without runtime probes."""
import unittest
from tests import test_thor as base


class PureConversionTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def tracked(self, code):
        for flags in (['-debug'], ['-o:speed', '-disable-assert', '-no-bounds-check',
                                  '-no-type-assert', '--track-allocations=basic']):
            with self.subTest(flags=flags):
                _, result = self.invoke(code, flags=flags)
                self.assertEqual(result.stderr, '')

    def test_captured_pointer_conversion_and_cast_are_rejected(self):
        for expression in ('T(uintptr(value))', 'cast(T)uintptr(value)'):
            with self.subTest(expression=expression):
                self.invoke(self.program('local:=1;_=outer(^int,int(uintptr(&local)))', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{pointer:='''+expression+''';pointer^=9;return pointer^}
    return inner(n)
}
'''), command='check', fail='Pure code cannot convert values to pointers')

    def test_captured_distinct_and_aliased_pointer_targets_are_rejected(self):
        for typ, prefix in (('Pointer', 'Pointer::distinct ^int\n'), ('^int', '')):
            with self.subTest(typ=typ):
                self.invoke(self.program('local:=1;_=outer('+typ+',int(uintptr(&local)))', prefix+'''
outer::proc($T:typeid,n:int)->int{
    Alias::T
    inner::fn(value:int)->int{pointer:=Alias(uintptr(value));pointer^=9;return pointer^}
    return inner(n)
}
'''), command='check', fail='Pure code cannot convert values to pointers')

    def test_direct_distinct_pointer_targets_are_rejected(self):
        for expression in ('Pointer(uintptr(value))', 'cast(Pointer)uintptr(value)'):
            with self.subTest(expression=expression):
                self.invoke(self.program('local:=1;_=work(int(uintptr(&local)))', '''
Pointer::distinct ^int
work::fn(value:int)->int{pointer:='''+expression+''';pointer^=9;return pointer^}
'''), command='check', fail='Pure code cannot convert values to pointers')

    def test_captured_record_fields_cannot_hide_pointer_operands(self):
        for expression in ('int(uintptr(record.pointer))', 'record.pointer==record.pointer ? 1 : 0'):
            with self.subTest(expression=expression):
                self.invoke(self.program('n:=3;_=outer(^int,n)', '''
Holder::struct(T:typeid){pointer:T}
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        local:=value
        record:Holder(T)
        record.pointer=&local
        return '''+expression+'''
    }
    return inner(n)
}
'''), command='check', fail='pointer')

    def test_captured_numeric_record_fields_remain_valid(self):
        self.tracked(self.program('n:=3;if outer(f64,n)!=3{panic("numeric record field")}', '''
Holder::struct(T:typeid){value:T}
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        record:=Holder(T){T(value)}
        return record.value==T(value) ? int(record.value) : 0
    }
    return inner(n)
}
'''))

    def test_captured_nil_only_switch_does_not_observe_pointer_identity(self):
        self.tracked(self.program('n:=3;if outer(^int,n)!=3{panic("nil-only switch")}', '''
Holder::struct(T:typeid){pointer:T}
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        record:Holder(T)
        switch record.pointer{case nil:return value;case:return 0}
    }
    return inner(n)
}
'''))

    def test_captured_raw_union_local_storage_and_literals_are_rejected(self):
        for initialization in ('storage:T', 'storage:=T{bits=uintptr(value)}'):
            with self.subTest(initialization=initialization):
                self.invoke(self.program('local:=1;_=outer(Unsafe,int(uintptr(&local)))', '''
Unsafe::struct #raw_union{bits:uintptr,pointer:^int}
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        '''+initialization+'''
        storage.bits=uintptr(value)
        storage.pointer^=9
        return value
    }
    return inner(n)
}
'''), command='check', fail='raw-union')

    def test_distinct_generic_record_instantiations_are_all_checked(self):
        self.invoke(self.program('local:=1;_=outer(Unsafe,int(uintptr(&local)))', '''
Unsafe::struct #raw_union{bits:uintptr,pointer:^int}
Box::struct(T:typeid){value:T}
Mixed::struct(T:typeid){left:Box(int),right:Box(T)}
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        storage:Mixed(T)
        storage.right.value.bits=uintptr(value)
        storage.right.value.pointer^=9
        return value
    }
    return inner(n)
}
'''), command='check', fail='raw-union')

    def test_generic_public_raw_unions_are_rejected_even_with_one_field(self):
        for fields in ('value:int', 'value:int,other:int'):
            with self.subTest(fields=fields):
                self.invoke(self.program('raw:=Raw{value=3};_=identity(raw)', '''
Raw::struct #raw_union{'''+fields+'''}
identity::fn(value:$T)->T{return value}
'''), command='check', fail='Raw-union storage')

    def test_captured_local_pointers_remain_owned_and_writable(self):
        self.tracked(self.program('n:=3;if outer(^int,n)!=4{panic("captured local pointer")}', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{local:=value;pointer:T=&local;pointer^+=1;return local}
    return inner(n)
}
'''))

    def test_ordinary_record_is_not_confused_with_an_inaccessible_raw_union(self):
        self.tracked(self.program('n:=3;if outer(Safe,n)!=6{panic("record layout identity")}', '''
Safe::struct{bits:uintptr,pointer:^int}
outer::proc($T:typeid,n:int)->int{
    Hidden::struct #raw_union{bits:uintptr,pointer:^int}
    inner::fn(value:int)->int{
        local:=value
        storage:T
        storage.bits=uintptr(value)
        storage.pointer=&local
        return int(storage.bits)+storage.pointer^
    }
    return inner(n)
}
'''))

    def test_captured_numeric_conversion_keeps_constants_and_runs_operands_once(self):
        self.tracked(self.program('''n:=3
if outer(f64,n)!=5||outer(i16,n)!=5{panic("captured numeric conversion")}
''', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        FIRST::T(2)
        #assert(FIRST==2)
        local:=value
        result:=T(step(&local))
        if local!=value+1{panic("conversion operand count")}
        return int(result)+int(FIRST)-1
    }
    return inner(n)
}
step::proc(out:^int)->int{out^+=1;return out^}
'''))

    def test_captured_cast_keeps_constants(self):
        self.tracked(self.program('n:=3;if outer(f64,n)!=5{panic("captured cast")}', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        FIRST::cast(T)2
        #assert(FIRST==2)
        return int(cast(T)value)+int(FIRST)
    }
    return inner(n)
}
'''))

    def test_captured_string_and_slice_conversions_preserve_owned_results(self):
        self.tracked(self.program('''n:=3
text:=convert_text(string,n);defer delete(text)
input :> []int{n};output:=convert_values([]int,input);defer delete(output)
output[0]=9
if text!="number 3"||input[0]!=3||output[0]!=9{panic("captured owned conversions")}
''', '''
convert_text::proc($T:typeid,n:int)->string{
    inner::fn(value:int)->string{return T(f"number {value}")}
    return inner(n)
}
convert_values::proc($T:typeid,values:[]int)->[]int{
    inner::fn(input:[]int)->[]int{return T(input)}
    return inner(values)
}
'''))

    def test_type_queries_may_inspect_pointer_conversions(self):
        self.tracked(self.program('''n:=3
if outer(^int,n)!=size_of(^int)+n{panic("unevaluated pointer conversion")}
''', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{_=type_of(T(uintptr(value)));return size_of(T)+value}
    return inner(n)
}
'''))

    def test_supplied_arguments_do_not_check_unused_captured_defaults(self):
        for argument in ('value', 'value=value'):
            with self.subTest(argument=argument):
                self.tracked(self.program('n:=3;if outer(^int,n)!=3{panic("unused default")}', '''
outer::proc($T:typeid,n:int)->int{
    helper::proc(value:int=int(uintptr(T(uintptr(1)))))->int{return value}
    inner::fn(value:int)->int{return helper('''+argument+''')}
    return inner(n)
}
'''))

    def test_omitted_captured_defaults_are_checked(self):
        self.invoke(self.program('n:=3;_=outer(^int,n)', '''
outer::proc($T:typeid,n:int)->int{
    helper::proc(value:int=int(uintptr(T(uintptr(1)))))->int{return value}
    inner::fn(value:int)->int{_=value;return helper()}
    return inner(n)
}
'''), command='check', fail='pointer')

    def test_declared_fn_defaults_remain_part_of_the_contract(self):
        self.invoke(self.program('n:=3;_=outer(^int,n)', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int=int(uintptr(T(uintptr(1)))))->int{return value}
    return inner(n)
}
'''), command='check', fail='pointer')

    def test_compile_time_override_does_not_check_unused_captured_default(self):
        self.invoke(self.program('if AUTO!=3||EXPLICIT!=5{panic("compile-time default override")}', '''
outer::proc($T:typeid,n:int)->int{
    helper::proc(value:int=int(uintptr(T(uintptr(1)))))->int{return value}
    inner::fn(value:int)->int{return helper(value)}
    return inner(n)
}
work::fn(n:int)->int{return outer(^int,n)}
AUTO::work(3)
EXPLICIT::#run work(5)
'''))

    def test_captured_conversions_in_explicit_and_automatic_compile_time_calls(self):
        self.invoke(self.program('if AUTO!=5||EXPLICIT!=7{panic("compile-time conversion")}', '''
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{
        FIRST::T(2)
        when FIRST>0{return int(T(value))+int(FIRST)}else{return 0}
    }
    return inner(n)
}
work::fn(n:int)->int{return outer(f64,n)}
AUTO::work(3)
EXPLICIT::#run work(5)
'''))


if __name__ == '__main__':
    unittest.main()
