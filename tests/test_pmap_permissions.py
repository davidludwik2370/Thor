"""Pure mapping must apply complete, instantiated callback permission checks."""
import json
import unittest
from tests import test_pmap as base


class ParallelMapPermissionTests(unittest.TestCase):
    invoke=base.ParallelMapTests.invoke
    program=base.ParallelMapTests.program

    def verify(self,source,**kwargs):
        for flags in (['-debug'],base.UNCHECKED):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags,**kwargs)
                self.assertNotIn('still allocated',result.stderr)

    def reject(self,source,**kwargs):
        for flags in ([],base.UNCHECKED):
            with self.subTest(flags=flags):
                self.invoke(source,command='build' if flags else 'check',fail='borrowed',flags=flags,**kwargs)

    def test_generic_fresh_buffers_support_indexed_tuple_alias_record_and_dynamic_writes(self):
        prefix='''import "thor:basic"
indexed::proc(x:$T)->[]T{v:=make([]T,2);v[0]=x;v[1]=x*2;return v}
tupled::proc(x:$T)->[]T{v:=make([]T,2);v[0],v[1]=x,x*2;return v}
aliased::proc(x:$T)->[]T{v:=make([]T,2);p:=&v[0];p^=x;v[1]=x*2;return v}
recorded::proc(x:$T)->[]T{Box::struct{data:[]T};v:=Box{make([]T,2)};v.data[0],v.data[1]=x,x*2;return v.data}
growing::proc(x:$T)->[]T{v:=make([dynamic]T,2);v[0],v[1]=x,x*2;return v[:]}
compute::fn(x:i16)->i16{
    jobs:=[]i16{x,x+1}
    a:=basic.pmap(jobs,indexed,workers=2)
    b:=basic.pmap(jobs,tupled,workers=2)
    c:=basic.pmap(jobs,aliased,workers=2)
    d:=basic.pmap(jobs,recorded,workers=2)
    e:=basic.pmap(jobs,growing,workers=2)
    return a[0][1]+a[1][0]+b[0][1]+b[1][0]+c[0][1]+c[1][0]+d[0][1]+d[1][0]+e[0][1]+e[1][0]
}
VALUE::#run compute(i16(4))
'''
        self.verify(self.program('x:i16=3;if compute(x)!=50||VALUE!=65{panic("fresh buffers")}',prefix))

    def test_direct_run_supports_named_and_inline_indexed_allocators(self):
        self.verify(self.program('''
a:=#run basic.pmap([]i16{2,3},allocate)
b:=#run basic.pmap([]i16{4,5},(proc(x:$T)->[]T{v:=make([]T,1);v[0]=x*2;return v}))
if a[1][1]!=6||b[1][0]!=10{panic("direct compile-time allocation")}
for row in a{delete(row)}
for row in b{delete(row)}
delete(a);delete(b)
''','''import "thor:basic"
allocate::proc(x:$T)->[]T{v:=make([]T,2);v[0],v[1]=x,x*2;return v}
'''))

    def test_generic_array_copy_can_be_mutated_but_slice_alias_cannot(self):
        prefix='''import "thor:basic"
rewrite::proc(x:$T)->T{local:=x;local[0]+=1;return local}
compute::fn(input:[2]int)->int{rows:=basic.pmap([][2]int{input},rewrite);return rows[0][0]}
VALUE::#run compute([2]int{5,8})
'''
        self.verify(self.program('input:=[3,7];if compute(input)!=4||input[0]!=3||VALUE!=6{panic("array copy")}',prefix))
        bad=prefix[:prefix.index('compute::')]+'''compute::fn(input:[]int)->int{rows:=basic.pmap([][]int{input},rewrite);return rows[0][0]}
'''
        self.reject(self.program('input:=[3,7];_=compute(input[:])',bad))

    def test_inactive_generic_mutation_branch_is_not_an_unconditional_requirement(self):
        prefix='''import "thor:basic"
calculate::proc(x:$T)->int{when T==i16{return int(x)}else{x[0]=9;return x[0]}}
compute::fn(x:i16)->int{rows:=basic.pmap([]i16{x},calculate);return rows[0]}
VALUE::#run compute(i16(4))
'''
        self.verify(self.program('x:i16=3;if compute(x)!=3||VALUE!=4{panic("inactive branch")}',prefix))
        bad=prefix[:prefix.index('compute::')]+'''compute::fn(input:[]int)->int{rows:=basic.pmap([][]int{input},calculate);return rows[0]}
'''
        root,result=self.invoke(self.program('input:=[3,7];_=compute(input[:])',bad),command='check',fail='borrowed',flags=['--diagnostic-format=json'])
        errors=json.loads(result.stderr)['errors']
        self.assertTrue(any('borrowed' in ' '.join(error['msgs']) and error.get('pos',{}).get('file')==str((root/'input/main.thor').resolve()) for error in errors),errors)
        self.assertNotIn('__thor_',result.stderr)
        self.invoke(self.program('input:=[3,7];_=compute(input[:])',bad),command='build',fail='borrowed',flags=base.UNCHECKED)

    def test_generic_numeric_copies_are_owned_for_direct_and_parallel_helpers(self):
        for typ,value,expected,constant in [('Integer','Integer(3)','Integer(7)','Integer(9)'),
                                            ('Rational','Rational(3,2)','Rational(4)','Rational(6)')]:
            with self.subTest(typ=typ):
                source=self.program('''
x:TYPE=INPUT;defer delete(x)
result:=compute(x);defer delete(result)
constant:=#run compute(INPUT+1);defer delete(constant)
if result!=EXPECTED||constant!=CONSTANT||x!=INPUT{panic("numeric copy ownership")}
''','''import "thor:basic"
allocate::proc(x:$T)->[]T{v:=make([]T,2);v[0],v[1]=x,x;return v}
compute::fn(x:TYPE)->TYPE{
    direct:=allocate(x)
    rows:=basic.pmap([]TYPE{x},allocate)
    direct[0]+=1
    return direct[0]+rows[0][1]
}
''').replace('TYPE',typ).replace('INPUT',value).replace('EXPECTED',expected).replace('CONSTANT',constant)
                self.verify(source)

    def test_shallow_reference_members_remain_borrowed(self):
        for typ,field,write,value in [('slice','data:[]int','local.data[0]=9','[]int{3}'),
                                      ('Integer','number:Integer','local.number+=1','Integer(3)'),
                                      ('Rational','number:Rational','local.number+=1','Rational(3,2)')]:
            with self.subTest(typ=typ):
                prefix='''import "thor:basic"
Box::struct{FIELD}
mutate::proc(x:$T){local:=x;WRITE}
compute::fn(input:[]Box)->int{basic.pmap(input,mutate);return 0}
'''.replace('FIELD',field).replace('WRITE',write)
                self.reject(self.program('_=compute([]Box{{'+value+'}})',prefix))

    def test_permissions_flow_through_imported_generic_helpers(self):
        files={'library/main.thor':'''package library
fill::proc(v:[]$T,x:T){v[0],v[1]=x,x*2}
allocate::proc(x:$T)->[]T{v:=make([]T,2);fill(v,x);return v}
'''}
        prefix='''import "thor:basic"
import "library"
alias::library.allocate
compute::fn(x:i16)->i16{rows:=basic.pmap([]i16{x},alias);return rows[0][1]}
VALUE::#run compute(i16(4))
'''
        self.verify(self.program('x:i16=3;if compute(x)!=6||VALUE!=8{panic("transitive permissions")}',prefix),files=files)
        bad='''import "thor:basic"
import "library"
write::proc(x:[]int)->int{library.fill(x,9);return x[0]}
compute::fn(input:[]int)->int{rows:=basic.pmap([][]int{input},write);return rows[0]}
'''
        self.reject(self.program('input:=[3,7];_=compute(input[:])',bad),files=files)

    def test_imported_helpers_preserve_numeric_clone_ownership(self):
        files={'library/main.thor':'''package library
fill::proc(v:[]$T,x:T){v[0],v[1]=x,x*2}
allocate::proc(x:$T)->[]T{v:=make([]T,2);fill(v,x);return v}
'''}
        for typ,value,expected,constant in [('Integer','Integer(3)','Integer(6)','Integer(8)'),
                                           ('Rational','Rational(3,2)','Rational(3)','Rational(5)')]:
            with self.subTest(typ=typ):
                source=self.program('''
x:=INPUT;defer delete(x)
y:=compute(x);defer delete(y)
z:=#run compute(INPUT+1);defer delete(z)
if x!=INPUT||y!=EXPECTED||z!=CONSTANT{panic("imported numeric ownership")}
''','''import "thor:basic"
import "library"
compute::fn(x:TYPE)->TYPE{rows:=basic.pmap([]TYPE{x},library.allocate);return rows[0][1]}
''').replace('TYPE',typ).replace('INPUT',value).replace('EXPECTED',expected).replace('CONSTANT',constant)
                self.verify(source,files=files)

    def test_bulk_shallow_copies_do_not_grant_numeric_write_access(self):
        for typ,value in [('Integer','Integer(3)'),('Rational','Rational(3,2)')]:
            with self.subTest(typ=typ):
                source=self.program('input:=[]TYPE{INPUT};_=compute(input)','''import "thor:basic"
mutate::proc(input:[]TYPE)->TYPE{local:=make([]TYPE,len(input));copy(local,input);local[0]+=1;return local[0]}
compute::fn(input:[]TYPE)->TYPE{rows:=basic.pmap([][]TYPE{input},mutate);return rows[0]}
''').replace('TYPE',typ).replace('INPUT',value)
                self.reject(source)

    def test_variadic_slice_aliases_converge_and_preserve_permissions(self):
        prefix='''import "thor:basic"
write::proc(values:[]int,n:int){values[0]=n}
forward::proc(n:int,values:..[]int){write(n=n,values=values[0])}
allocate::proc(n:int)->int{fresh:=make([]int,1);forward(n,fresh);return fresh[0]}
compute::fn(n:int)->int{rows:=basic.pmap([]int{n},allocate);return rows[0]}
VALUE::#run compute(7)
'''
        self.verify(self.program('n:=3;if compute(n)!=3||VALUE!=7{panic("variadic alias")}',prefix))
        bad=prefix[:prefix.index('allocate::')]+'''compute::fn(input:[]int)->int{forward(2,input);return input[0]}
'''
        self.reject(self.program('input:=[3,7];_=compute(input[:])',bad))
