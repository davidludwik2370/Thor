"""Transitive ordinary calls keep the fn contract without changing ordinary calls."""
import json
from pathlib import Path
import unittest

from tests import test_thor as base
from tests import test_t18_t20 as pure


class PureCallTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program
    memory=pure.PureTests.memory

    def test_runtime_automatic_and_explicit_transitive_calls(self):
        self.invoke(self.program('value:=4;assert(calculate(value)==17&&AUTO==10&&EXPLICIT==26)', '''
square::proc(x:int)->int{return x*x}
adjusted::proc(x:int)->int{return square(x)+1}
calculate::fn(x:int)->int{return adjusted(x)}
AUTO::calculate(3)
EXPLICIT::#run calculate(5)
'''))
        self.invoke(self.program('_=VALUE','ordinary::proc()->int{return 7}\nwork::fn()->int{return ordinary()}\nVALUE::ordinary()\n'),
                    command='check',fail='global scope')

    def test_recursive_and_mutually_recursive_procedures(self):
        self.invoke(self.program('n:=7;assert(work(n)==13&&EVEN&&TOTAL==8)', '''
fib::proc(n:int)->int{if n<2{return n};return fib(n-1)+fib(n-2)}
even::proc(n:int)->bool{return n==0 ? true : odd(n-1)}
odd::proc(n:int)->bool{return n==0 ? false : even(n-1)}
work::fn(n:int)->int{return fib(n)}
is_even::fn(n:int)->bool{return even(n)}
EVEN::is_even(10)
TOTAL::work(6)
'''))

    def test_single_statement_returns_promote_owned_values(self):
        self.memory('''
for flag in [false,true] {
    value:=work(flag)
    testing.expect(t,value==(flag ? "yes" : "no"))
    delete(value)
}
''','''
leaf::proc(flag:bool)->string{if flag do return f"yes";return "no"}
work::fn(flag:bool)->string{return leaf(flag)}
''')
        self.invoke(self.program('assert(VALUE=="yes")','''
leaf::fn(flag:bool)->string{if flag do return f"yes";return "no"}
VALUE::leaf(true)
'''))

    def test_owned_variadic_results_forward_named_only_parameters(self):
        self.memory('''
n:=3
result:=work(n)
testing.expect(t,result[0]==9 && result[1]==12)
delete(result)
''','''
leaf::proc(values:..int, scale:int=2)->[]int {
    ret:=make([]int,len(values))
    for v,i in values {ret[i]=v*scale}
    return ret
}
work::fn(n:int)->[]int{return leaf(n,n+1,scale=3)}
''')

    def test_generics_overloads_aliases_and_named_defaults(self):
        self.invoke(self.program('x:=3;y:=f64(4);assert(work(x)==9&&work(y)==16&&DEFAULT==25)', '''
integer::proc(value:int)->int{return value*value}
floating::proc(value:f64)->f64{return value*value}
square::proc{integer,floating}
alias::square
middle::proc(value:$T,extra:T=0)->T where T in [int,f64]{return alias(value)+extra}
work::fn(value:$T)->T{return middle(extra=0,value=value)}
DEFAULT::work(5)
'''))

    def test_imported_odin_and_thor_bodies_preserve_private_types(self):
        self.invoke(self.program('n:=7;assert(work(n)==15&&VALUE==11)', '''
import "library" / entry
work::fn(n:int)->int{return entry(n)}
VALUE::work(5)
'''), files={
            'library/native.odin':'''package library
@private
Record::struct{value:int}
inner::proc(n:int)->Record{return {n*2}}
entry::proc(n:int)->int{return middle(n)+1}
''',
            'library/middle.thor':'package library\nmiddle::proc(n:int)->int{return inner(n).value}\n',
        })

    def test_borrowed_views_are_owned_only_on_the_checked_path(self):
        self.memory('''
input:=[1,2,3]
result:=work(input[:])
testing.expect(t,result[0]==9&&input[1]==2)
delete(result)
ordinary:=view(input[:]);ordinary[0]=7
testing.expect(t,input[1]==7)
''','''
view::proc(input:[]int)->[]int{return input[1:]}
middle::proc(input:[]int)->[]int{return view(input)}
work::fn(input:[]int)->[]int{result:=middle(input);result[0]=9;return result}
''')

    def test_allocations_nested_results_and_early_exits_do_not_leak(self):
        self.memory('''
for i in 0..<5{
    testing.expect(t,count("abc",i)==i+3)
    result:=report("hello")
    testing.expect(t,result.text=="value hello"&&result.rows[0]=="hello")
    pure.destroy(result)
}
''','''
import "thor:pure"
Report::struct{text:string,rows:[]string}
leaf::proc(text:string)->Report{
    rows:=make([]string,2);rows[0]=text;rows[1]=text
    return {f"value {text}",rows}
}
report::fn(text:string)->Report{return leaf(text)}
scratch::proc(text:string,n:int)->int{
    data:=make([]int,3);data[0]=n
    unused:=f"{n} {text}"
    if n==0{return len(text)}
    return data[0]+len(text)
}
count::fn(text:string,n:int)->int{return scratch(text,n)}
''')

    def test_impurity_at_transitive_leaves_is_rejected_at_runtime(self):
        cases=[
            ('state:int\nleaf::proc(n:int)->int{return state+n}', 'outside the function'),
            ('leaf::proc(n:int)->int{_=context;return n}', 'ambient context'),
            ('leaf::proc(n:int)->int{x:=n;y:=n;return (&x==&y)?1:0}', 'pointer identities'),
            ('foreign libc { external::proc(n:int)->int--- }\nleaf::proc(n:int)->int{return external(n)}', 'resolved direct call'),
            ('target::proc(n:int)->int{return n}\nleaf::proc(n:int)->int{callback:=target;return callback(n)}', 'indirect'),
        ]
        for prefix,message in cases:
            with self.subTest(message=message):
                _,result=self.invoke(self.program('n:=3;_=work(n)',prefix+'\nmiddle::proc(n:int)->int{return leaf(n)}\nwork::fn(n:int)->int{return middle(n)}\n'),
                                    command='check',fail=message)
                self.assertNotIn('__thor_',result.stderr)

    def test_mutating_or_deleting_borrowed_inputs_is_rejected(self):
        for statement in ['input[0]=7','copy(input,input)','delete(input)']:
            with self.subTest(statement=statement):
                self.invoke(self.program('data:=[1,2];_=work(data[:])',
                    'leaf::proc(input:[]int)->int{'+statement+';return 0}\nmiddle::proc(input:[]int)->int{return leaf(input)}\nwork::fn(input:[]int)->int{return middle(input)}\n'),
                    command='check',fail='borrowed from an input')
        self.invoke(self.program('data:=[1,2];mutate(data[:]);assert(data[0]==7)',
                                'mutate::proc(input:[]int){input[0]=7}\n'))

    def test_native_when_selects_purity_checks_per_instantiation(self):
        prefix='''
state:int
leaf::proc(value:$T)->int{when T==int{return value}else{return state}}
work::fn(value:$T)->int{return leaf(value)}
'''
        self.invoke(self.program('n:=3;assert(work(n)==3)',prefix))
        self.invoke(self.program('_=work("text")',prefix),command='check',fail='outside the function')
        self.invoke(self.program('assert(leaf("text")==0)',prefix.replace('work::fn(value:$T)->int{return leaf(value)}','')))

    def test_type_queries_do_not_execute_or_check_ordinary_effects(self):
        self.invoke(self.program('n:=3;assert(work(n)==size_of(int))','''
state:int
effect::proc()->int{state+=1;return state}
work::fn(n:int)->int{return size_of(type_of(effect()))}
'''))

    def test_inactive_callers_do_not_check_ordinary_bodies(self):
        self.invoke(self.program('assert(leaf()==0)','''
state:int
leaf::proc()->int{return state}
when false{unused::fn()->int{return leaf()}}
'''))
        self.invoke(self.program('x:=3;assert(work(x)==3)','''
state:int
leaf::proc()->int{return state}
work::fn(value:$T)->int{when T==int{return value}else{return leaf()}}
'''))

    def test_conditional_global_implementations_follow_native_selection(self):
        prefix='''
MODE::#config(MODE,1)
when MODE==1{leaf::proc(value:int)->int{return value+1}}
else{leaf::proc(value:int)->int{return value+2}}
work::fn(value:int)->int{return leaf(value)}
'''
        for mode in [1,2]:
            self.invoke(self.program(f'x:=3;assert(work(x)=={3+mode})',prefix),flags=[f'-define:MODE={mode}'])

    def test_standard_library_math_at_runtime_and_compile_time(self):
        self.invoke(self.program('x:=9.0;assert(work(x)==3&&VALUE==4&&plain(x)==3)', '''
import "core:math"
plain::proc(x:f64)->f64{return math.sqrt(x)}
work::fn(x:f64)->f64{return plain(x)}
VALUE::work(16.0)
'''))

    def test_standard_library_nominal_types_keep_one_identity(self):
        self.invoke(self.program('d:=time.Duration(1500000000);assert(work(d)==1.5&&plain(d)==1.5&&VALUE==2.5)', '''
import "core:time"
plain::proc(d:time.Duration)->f64{return time.duration_seconds(d)}
work::fn(d:time.Duration)->f64{return plain(d)}
VALUE::work(time.Duration(2500000000))
'''))

    def test_standard_library_calls_work_under_test_mode(self):
        self.memory('d:=time.Duration(1500000000);testing.expect(t,work(d)==1.5)', '''
import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_seconds(d)}
''')

    def test_private_procedures_cannot_be_exposed_through_checked_versions(self):
        self.invoke(self.program('n:=3;_=work(n)','import "library"\nwork::fn(n:int)->int{return library.hidden(n)}\n'),
                    files={'library/main.odin':'package library\n@private\nhidden::proc(n:int)->int{return n}\n'},
                    command='check',fail='private to its package')
        self.invoke(self.program('n:=3;_=work(n)','work::fn(n:int)->int{return hidden(n)}\n'),
                    files={'hidden.odin':'package main\n@(private="file")\nhidden::proc(n:int)->int{return n}\n'},
                    command='check',directory=True,fail='private to its file')
        self.invoke(self.program('n:=3;_=work(n)','work::fn(n:int)->int{return hidden(n)}\n'),
                    files={'hidden.odin':'#+private file\npackage main\nhidden::proc(n:int)->int{return n}\n'},
                    command='check',directory=True,fail='private to its file')

    def test_same_named_file_private_helpers_keep_their_own_bodies(self):
        self.invoke(self.program('n:=3;assert(first(n)==4&&second(n)==5)', '''
@(private="file")
helper::proc(n:int)->int{return n+1}
first::fn(n:int)->int{return helper(n)}
'''), files={'other.thor':'''package main
@(private="file")
helper::proc(n:int)->int{return n+2}
second::fn(n:int)->int{return helper(n)}
'''},directory=True)

    def test_procedure_names_and_result_requirements_are_preserved(self):
        self.memory('result:=work();testing.expect(t,result=="name");delete(result)', '''
name::proc()->string{return #procedure}
work::fn()->string{return name()}
''')
        self.invoke(self.program('_=VALUE','name::proc()->string{return #procedure}\nwork::fn()->string{return name()}\nVALUE::work()\n#assert(VALUE=="name")\n'))
        self.invoke(self.program('n:=3;work(n)','''
@(require_results)
leaf::proc(n:int)->int{return n}
work::fn(n:int){leaf(n)}
'''),command='check',fail='results')

    def test_void_multiple_results_and_variadic_helpers(self):
        self.memory('result:=work("hello");testing.expect(t,result=="hello");delete(result)', '''
leaf::proc(input:string)->(string,bool){return input,true}
check::proc(ok:bool){assert(ok)}
work::fn(input:string)->string{value,ok:=leaf(input);check(ok);return value}
''')
        self.invoke(self.program('n:=3;assert(work(n,n+1)==7)','''
sum::proc(values:..int)->int{result:=0;for value in values{result+=value};return result}
work::fn(values:..int)->int{return sum(..values)}
'''))

    def test_inspectable_contextless_and_c_helpers(self):
        for convention in ['contextless','c']:
            self.invoke(self.program('n:=3;assert(work(n)==9)',
                'leaf::proc "'+convention+'"(n:int)->int{return n*n}\nwork::fn(n:int)->int{return leaf(n)}\n'))

    def test_transitive_error_locations_are_original_unicode_source(self):
        source='''package main
state:int
leaf::proc(n:int)->int{/* 😀 */return state+n}
middle::proc(n:int)->int{return leaf(n)}
work::fn(n:int)->int{return middle(n)}
main::proc(){n:=3;_=work(n)}
'''
        root,result=self.invoke(source,command='check',flags=['-json-errors'],fail='outside the function')
        errors=json.loads(result.stderr)['errors']
        error=next(e for e in errors if 'outside the function' in ' '.join(e['msgs']))
        self.assertEqual(Path(error['pos']['file']),root.resolve()/'input/main.thor')
        self.assertEqual(error['pos']['offset'],source.encode().index(b'middle(n)',source.encode().index(b'work::fn')))
        self.assertTrue(any(note['pos']['offset']==source.encode().index(b'state+n')
                            for note in error['related']))
        self.assertNotIn('__thor_',result.stderr)

    def test_return_mismatches_show_original_calls_without_checked_arguments(self):
        for declarations,call in [
            ('import "core:math"\nwork::fn(x:f64)->int{return math.floor(x)}','math.floor(x)'),
            ('leaf::proc(x:f64)->f64{return x}\nwork::fn(x:f64)->int{return leaf(x)}','leaf(x)'),
        ]:
            with self.subTest(call=call):
                source=self.program('x:=3.5;_=work(x)',declarations)
                _,result=self.invoke(source,command='check',flags=['-json-errors'],fail="of type 'f64' to 'int'")
                errors=json.loads(result.stderr)['errors']
                message='\n'.join(m for e in errors for m in e['msgs'])
                self.assertIn("'"+call+"'",message)
                self.assertNotIn('Thor expression',message)
                self.assertNotIn('__thor_',message)


if __name__=='__main__':unittest.main()
