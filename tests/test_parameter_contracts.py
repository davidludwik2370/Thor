"""Caller metadata, runtime barriers, and explicit slice/vet contracts together."""
import json
import subprocess
import unittest
from tests import test_rust_compiler as base


class ParameterContractTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    compare=base.RustCompilerTests.compare

    def checked_and_unchecked(self,text,expected):
        for flags in [[],base.UNCHECKED]:
            with self.subTest(unchecked=bool(flags)):
                self.compare(text,flags=['-thread-count:1',*flags],expected=expected)

    def test_automatic_metadata_direct_aliases_and_callbacks(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
Callback::#type proc(value:int, expression:>#caller_expression(value))->string
capture::proc(value:int, expression:>#caller_expression(value))->string{return expression}
invoke::proc(callback:Callback)->string{return callback(2+3)}
generic::proc(callback:$F,value:int)->string{return callback(value)}
record::proc(a,b:int,a_text:>#caller_expression(a),b_text:>#caller_expression(b),call:>#caller_expression,loc:>#caller_location){
    if loc.line<=0 || len(loc.file_path)==0 {panic("missing location")}
    fmt.println(a_text,b_text,call)
}
main::proc(){
    x,y:=2,3
    record(x,y)
    alias::capture
    fmt.println(alias(x+1))
    callback:Callback=capture
    fmt.println(invoke(callback),generic(alias,4))
}
''','x y record(x, y)\nx + 1\n2 + 3 value')

    def test_automatic_metadata_cannot_be_overridden(self):
        for invocation in ['capture(1,"fake")','capture(1,expression="fake")','alias(1,expression="fake")']:
            with self.subTest(invocation=invocation):
                self.compare('''package main
capture::proc(value:int,expression:>#caller_expression(value)){}
main::proc(){alias::capture;'''+invocation+'}\n',command='check',flags=['-thread-count:1'],fail='cannot be supplied explicitly')

    def test_metadata_contract_cannot_be_erased(self):
        self.compare('''package main
capture::proc(value:int,expression:>#caller_expression(value)){}
main::proc(){callback:proc(value:int,expression:string)=capture;callback(1,"fake")}
''',command='check',flags=['-thread-count:1'],fail='Incompatible automatic caller metadata')

    def test_metadata_foreign_and_automatic_entry_points_are_rejected(self):
        for declaration in [
            'capture::proc "c"(value:int,expression:>#caller_expression(value)){}',
            '@(export)\ncapture::proc(value:int,expression:>#caller_expression(value)){}',
            '@(init)\ncapture::proc(expression:>#caller_expression){}',
        ]:
            with self.subTest(declaration=declaration):
                self.compare('package main\n'+declaration+'\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail='Automatic :> caller metadata parameters cannot')

    def test_invalid_metadata_defaults(self):
        for signature,problem in [
            ('value:int,expression:> "fake"','A :> parameter requires'),
            ('expression:>#caller_expression(value),value:int','earlier ordinary'),
            ('value:int,expression:>#caller_expression(value),other:int','must follow ordinary'),
        ]:
            with self.subTest(signature=signature):
                self.compare('package main\ncapture::proc('+signature+'){}\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail=problem)

    def test_runtime_marker_preserves_execution_and_types(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
twice::fn(value:int)->int{return value*2}
main::proc(){
 alias::twice
 value:=#runtime alias(7)
 other:=alias(#runtime 7)
 nested:=#runtime (alias(3)+alias(4))
 if other!=14 || nested!=14 {panic("runtime operands")}
 fmt.println(value)
}
''','14')

    def test_runtime_in_required_constant_is_rejected(self):
        self.compare('''package main
twice::fn(value:int)->int{return value*2}
VALUE::#runtime twice(7)
main::proc(){_=VALUE}
''',command='check',flags=['-thread-count:1'],fail='#runtime cannot be used where a compile-time value is required')

    def test_ro_readers_and_independent_copies(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
Item::struct{value:int}
read::proc(#ro values:[]Item)->int{copy:=values[0];copy.value+=1;return copy.value}
generic::proc(#ro values:[]$T)->int{return len(values)}
main::proc(){values:>[]Item{{7}};fmt.println(read(values),generic(values))}
''','8 1')

    def test_ro_rejects_transitive_mutation_and_escapes(self):
        for body in ['values[0]=9','alias:=values;alias[0]=9','pointer:=&values[0];pointer^=9','write(values)']:
            with self.subTest(body=body):
                self.compare('''package main
write::proc(#mut values:[]int){values[0]=9}
read::proc(#ro values:[]int){'''+body+'''}
main::proc(){values:=[]int{7};read(values)}
''',command='check',flags=['-thread-count:1'],fail='read-only')

    def test_ro_requires_slice_and_conflicts_with_mut(self):
        for signature,problem in [('#ro value:int','#ro requires a slice'),('#mut #ro value:[]int','both #mut and #ro')]:
            with self.subTest(signature=signature):
                self.compare('package main\nread::proc('+signature+'){}\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail=problem)

    def test_runtime_does_not_strip_slice_permissions(self):
        self.compare('''package main
identity::proc(#ro values:[]int)->[]int{return values}
main::proc(){values:=[]int{7};view:=#runtime identity(values);view[0]=9}
''',command='check',flags=['-thread-count:1'],fail='read-only')

    def test_metadata_inside_run_uses_each_original_expression(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
capture::fn(value:int,expression:>#caller_expression(value))->string{return expression}
X::7
Y::7
A::#run capture(X)
B::#run capture(Y)
main::proc(){fmt.println(A,B)}
''','X Y')

    def test_vet_ignore_suppresses_warnings_and_apply_only_on_its_declaration(self):
        text='''package main
import "core:fmt"
@(vet_ignore="immutable")
KEEP_MUTABLE:=7
@(vet_ignore="fn, unused")
helper::proc(value:int)->int{return value*2}
main::proc(){fmt.println(KEEP_MUTABLE)}
'''
        for engine in ['rust','python']:
            with self.subTest(engine=engine):
                self.source.write_text(text)
                result=self.invoke(engine,'vet',['--apply','-thread-count:1'])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                updated=self.source.read_text()
                self.assertIn('KEEP_MUTABLE:=7',updated)
                self.assertIn('helper::proc',updated)
                self.assertNotIn("'helper' declared but not used",result.stdout+result.stderr)
                self.assertIn('@(vet_ignore="fn, unused")',updated)

    def test_vet_ignore_unknown_names_and_compiler_errors(self):
        self.compare('package main\n@(vet_ignore="typo")\nVALUE::7\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail='Unknown vet check')
        self.compare('package main\n@(vet_ignore="unused")\nVALUE::missing\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail='Undeclared name: missing')

    def test_runtime_barrier_in_function_body_and_explicit_run(self):
        text='''package main
import "core:fmt"
twice::fn(value:int)->int{return value*2}
inner::fn(value:int)->int{return #runtime twice(value)}
outer::fn(value:int)->int{return inner(value)}
main::proc(){fmt.println(outer(7))}
'''
        self.checked_and_unchecked(text,'14')
        self.compare(text.replace('fmt.println(outer(7))','value::#run outer(7);fmt.println(value)'),command='check',flags=['-thread-count:1'],fail='#runtime cannot')

    def test_ro_callbacks_accept_readers_and_reject_writers(self):
        text='''package main
import "core:fmt"
Reader::#type proc(#ro values:[]int)->int
read::proc(values:[]int)->int{return values[0]}
write::proc(values:[]int)->int{values[0]=9;return values[0]}
invoke::proc(reader:Reader,#ro values:[]int)->int{return reader(values)}
main::proc(){values:>[]int{7};fmt.println(invoke(read,values))}
'''
        self.checked_and_unchecked(text,'7')
        self.compare(text.replace('invoke(read,values)','invoke(write,values)'),command='check',flags=['-thread-count:1'],fail='#ro callback parameter')

    def test_pmap_metadata_does_not_change_job_shape(self):
        self.checked_and_unchecked('''package main
import "thor:basic"
import "core:fmt"
calculate::fn(value:$T,text:>#caller_expression(value))->T{return value*2}
main::proc(){
    jobs:=[]int{2,3}
    output:=basic.pmap(jobs,calculate,workers=2)
    defer delete(output)
    fmt.println(output)
}
''','[4, 6]')

    def test_contract_erasure_through_containers_casts_and_generic_wrappers(self):
        prefix='''package main
Plain::#type proc(value:int,expression:string)
capture::proc(value:int,expression:>#caller_expression(value)){}
Box::struct{callback:Plain}
'''
        for body,extra in [
            ('callbacks:=[1]Plain{capture};callbacks[0](7,"fake")',''),
            ('box:=Box{callback=capture};box.callback(7,"fake")',''),
            ('callback:=Plain(capture);callback(7,"fake")',''),
            ('invoke(capture)','invoke::proc(callback:$F){callback(7,expression="fake")}\n'),
            ('callback:=erase(capture);callback(7,"fake")','erase::proc(callback:$F)->Plain{return callback}\n'),
        ]:
            with self.subTest(body=body):
                self.compare(prefix+extra+'main::proc(){'+body+'}\n',command='check',flags=['-thread-count:1'],fail='metadata')

    def test_native_defaults_remain_overridable_and_metadata_evaluates_once(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
count:int
next::proc()->int{count+=1;return count}
regular::proc(value:int,text:=#caller_expression(value))->string{return text}
capture::proc(value:=7,text:>#caller_expression(value))->string{return text}
main::proc(){
 text:=capture(next())
 if count!=1 || text!="next()" || capture()!="" {panic("metadata defaults")}
 fmt.println(regular(1,text="custom"),text)
}
''','custom next()')

    def test_invalid_metadata_calls_and_placements(self):
        for declaration,problem in [
            ('helper::proc(x:int)->string{return "bad"}\ncapture::proc(value:int,text:>helper(value)){}','A :> parameter requires'),
            ('capture::proc(value:int,text:>#caller_expression(value+1)){}','A :> parameter requires'),
            ('capture::proc(value:int,text:>#caller_expression(value,value)){}','A :> parameter requires'),
            ('capture::proc(value:int,a,b:>#caller_expression(value)){}','one parameter name'),
            ('Row::struct{#ro values:[]int}','#ro requires a slice parameter'),
            ('capture::proc()->(#ro values:[]int){return nil}','#ro requires a slice parameter'),
        ]:
            with self.subTest(declaration=declaration):
                self.compare('package main\n'+declaration+'\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail=problem)

    def test_metadata_callback_names_can_differ_and_container_contracts_survive(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
Capture::#type proc(x:int,text:>#caller_expression(x))->string
capture::proc(value:int,expr:>#caller_expression(value))->string{return expr}
Box::struct{callback:Capture}
identity::proc(callback:$F)->F{return callback}
main::proc(){
 callbacks:=[1]Capture{capture}
 box:=Box{callback=identity(capture)}
 fmt.println(callbacks[0](2+3),box.callback(7))
}
''','2 + 3 7')

    def test_ro_is_per_name_variadic_generic_and_preserves_return_views(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
read_and_write::proc(#ro source,destination:[]int){destination[0]=source[0]}
sum::proc(#ro values:..int)->int{total:int;for v in values{total+=v};return total}
read_generic::proc(#ro values:$T/[]$E)->int{return len(values)}
main::proc(){
 source:>[]int{7,8};destination:=[]int{0}
 read_and_write(source,destination)
 fmt.println(sum(..source),read_generic(source),destination[0])
}
''','15 2 7')
        self.compare('''package main
read::proc(#ro value:$T)->int{return 7}
main::proc(){_=read(1)}
''',command='check',flags=['-thread-count:1'],fail='#ro requires a slice parameter')
        self.compare('''package main
identity::proc(#ro values:[]int)->[]int{return values}
Reader::#type proc(#ro values:[]int)->[]int
main::proc(){callback:Reader=identity;values:=[]int{7};view:=callback(values);view[0]=9}
''',command='check',flags=['-thread-count:1'],fail='read-only')

    def test_ro_cannot_be_laundered_through_mutable_callbacks_or_containers(self):
        prefix='''package main
Reader::#type proc(#ro values:[]int)->int
Plain::#type proc(values:[]int)->int
read::proc(values:[]int)->int{return values[0]}
write::proc(values:[]int)->int{values[0]=9;return values[0]}
Box::struct{callback:Reader}
'''
        for body in [
            'callback:Plain=read;callback=write;reader:Reader=callback;_=reader',
            'callbacks:=[1]Reader{write};_=callbacks',
            'box:=Box{callback=write};_=box',
            'reader:=Reader(write);_=reader',
            'reader:Reader=read;reader=write;_=reader',
        ]:
            with self.subTest(body=body):
                self.compare(prefix+'main::proc(){'+body+'}\n',command='check',flags=['-thread-count:1'],fail='#ro callback parameter')

    def test_pmap_run_and_runtime_callback_keep_metadata(self):
        self.checked_and_unchecked('''package main
import "thor:basic"
import "core:fmt"
calculate::fn(value:$T,text:>#caller_expression(value))->T{return value*2}
Callback::#type proc(value:int,text:>#caller_expression(value))->int
concrete::proc(value:int,text:>#caller_expression(value))->int{return value*2}
main::proc(){
 callback:Callback=concrete
 TOTAL:=#run basic.pmap([]int{2,3},calculate)
 defer delete(TOTAL)
 output:=basic.pmap([]int{2,3},callback,workers=2)
 defer delete(output)
 fmt.println(TOTAL,output)
}
''','[4, 6] [4, 6]')

    def test_runtime_inactive_when_and_runtime_collection_ownership(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
twice::fn(value:int)->int{
 when false {return #runtime (value*2)}
 return value*2
}
VALUE::#run twice(7)
main::proc(){
 output:=#runtime [x*2 for x in 0..<3]
 defer delete(output)
 fmt.println(VALUE,output)
}
''','14 [0, 2, 4]')

    def test_vet_ignore_import_semicolon_and_declaration_scope(self):
        text='''package main
@(vet_ignore=`unused`)
import "core:thread"
import "core:fmt"
main::proc(){
 @(vet_ignore="immutable, semicolon")
 keep:=7;
 edit:=8;
 fmt.println(keep,edit)
}
'''
        for engine in ['rust','python']:
            with self.subTest(engine=engine):
                self.source.write_text(text)
                result=self.invoke(engine,'vet',['--apply','-thread-count:1'])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                updated=self.source.read_text()
                self.assertIn('import "core:thread"',updated)
                self.assertIn('keep:=7;',updated)
                self.assertIn('edit:>8\n',updated)
        text='''package main
import "core:fmt"
@(vet_ignore="immutable")
main::proc(){value:=7;fmt.println(value)}
'''
        for engine in ['rust','python']:
            self.source.write_text(text)
            result=self.invoke(engine,'vet',['--apply','-thread-count:1'])
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('value:>7',self.source.read_text())

    def test_vet_ignore_invalid_forms(self):
        for attribute in ['vet_ignore','vet_ignore=""','vet_ignore="unused,"','vet_ignore="all"','vet_ignore="unused",vet_ignore="pure"','vet_ignore=7']:
            with self.subTest(attribute=attribute):
                self.compare('package main\n@('+attribute+')\nVALUE::7\nmain::proc(){}\n',command='check',flags=['-thread-count:1'],fail='vet')

    def test_runtime_is_emitted_and_never_weakens_purity(self):
        text='''package main
twice::fn(value:int)->int{return value*2}
main::proc(){alias::twice;result:=#runtime alias(7);if result!=14{panic("result")}}
'''
        for engine in ['rust','python']:
            self.source.write_text(text)
            result=self.invoke(engine,'transpile',['-thread-count:1'])
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            from pathlib import Path
            generated=Path(result.stdout.strip()).read_text()
            self.assertIn('alias(7)',generated)
            self.assertNotIn('__thor_runtime(',generated)
        self.compare('''package main
GLOBAL:=7
read::proc()->int{return GLOBAL}
invalid::fn()->int{return #runtime read()}
main::proc(){_=invalid()}
''',command='check',flags=['-thread-count:1'],fail="runtime storage 'GLOBAL'")

    def test_vet_ignore_unused_local_and_other_native_attributes(self):
        text='''package main
@(vet_ignore="pure",deprecated="example")
keep::proc()->int{return 7}
main::proc(){
 @(vet_ignore="unused, immutable")
 value:=7
 _=keep()
}
'''
        for engine in ['rust','python']:
            self.source.write_text(text)
            result=self.invoke(engine,'vet',['--apply','-thread-count:1'])
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn("'value' declared but not used",result.stdout+result.stderr)
            self.assertIn('value:=7',self.source.read_text())
            self.assertIn('keep::proc',self.source.read_text())

    def test_metadata_zero_input_mapping_and_action_callbacks(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
import "thor:basic"
constant::proc(text:>#caller_expression)->int{return 7}
action::proc(value:int,text:>#caller_expression(value)){if value!=7{panic("value")}}
main::proc(){
 output:=basic.pmap([]struct{}{{},{}},constant,workers=2)
 defer delete(output)
 basic.pmap(output,action,workers=2)
 fmt.println(output)
}
''','[7, 7]')

    def test_runtime_barriers_follow_generic_when_selection(self):
        self.checked_and_unchecked('''package main
import "core:fmt"
select::fn(value:$T)->int{
 when T==int {return int(value)}
 else {return #runtime 0}
}
A::select(7)
B::#run select(8)
main::proc(){fmt.println(A,B,select(f64(1)))}
''','7 8 0')

    def test_metadata_generic_when_checks_only_selected_calls(self):
        text='''package main
capture::proc(value:int,text:>#caller_expression(value)){}
select::proc($T:typeid){
 when T==int {capture(7)} else {capture(7,text="fake")}
}
main::proc(){select(int)}
'''
        self.compare(text,command='check',flags=['-thread-count:1'])
        self.compare(text.replace('select(int)','select(f64)'),command='check',flags=['-thread-count:1'],fail='cannot be supplied explicitly')
        numeric=text.replace('$T:typeid','$N:int').replace('T==int','N==0').replace('select(int)','select(0)')
        self.compare(numeric,command='check',flags=['-thread-count:1'])
        self.compare(numeric.replace('select(0)','select(1)'),command='check',flags=['-thread-count:1'],fail='cannot be supplied explicitly')
