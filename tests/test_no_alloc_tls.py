"""Live editor contracts: annotations, original ranges, paths, and unsaved edits."""
import json
import unittest
from tests import test_tls as support


class NoAllocTLSTests(unittest.TestCase):
    setUpClass=classmethod(support.TLSTests.setUpClass.__func__)
    open=support.TLSTests.open
    hover=support.TLSTests.hover
    position=staticmethod(support.TLSTests.position)

    def test_annotation_hover_completion_and_unsaved_violation(self):
        text='''// 😀 original source
package main
@(no_alloc)
read::proc(value:int)->int{return value+1}
main::proc(){_=read(2)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        hover=json.dumps(self.hover(client,main,text,'no_alloc'))
        self.assertIn('allocation',hover)
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'no_alloc',0)})
        items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
        self.assertTrue(any(item.get('label')=='no_alloc' for item in items),items)
        changed=text.replace('return value+1','values:=make([]int,1);return value+len(values)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['severity'],1)
        self.assertEqual(error['range']['start'],self.position(changed,'make([]int,1)',0))
        self.assertEqual(error['range']['end'],self.position(changed,'make([]int,1)',len('make([]int,1)')))
        self.assertNotIn('__thor_',json.dumps(errors))
        self.assertTrue(error.get('relatedInformation'),error)

    def test_sdk_sort_and_factory_edits_keep_original_diagnostics(self):
        text='''package main
import "core:slice"
import "core:strings"
import "core:sync"
import "core:time"
v:u64
less::proc(a,b:$T)->bool{return a<b}
@(no_alloc)
main::proc(){values:=[3]int{3,2,1};slice.sort_by(values[:],less);_=strings.compare("a","b");_=sync.atomic_load(&v);_=time.now()}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('return a<b','_=make([]int,1);return a<b')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'make([]int,1)',0))
        self.assertEqual(error['range']['end'],self.position(changed,'make([]int,1)',len('make([]int,1)')))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_file_tag_and_unsaved_recovery(self):
        text='#+vet no_alloc\npackage main\nmain::proc(){values:=make([]int,1);_=values}\n'
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),contains='no_alloc:')
        changed=text.replace('make([]int,1)','[1,2]')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_call_path_and_edited_callee(self):
        text='''package main
helper::proc(){value:=7;_=value}
@(no_alloc)
entry::proc(){helper()}
main::proc(){entry()}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('value:=7','value:=make([]int,1)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertIn('entry',error['message'])
        related=error.get('relatedInformation',[])
        self.assertGreaterEqual(len(related),2,error)
        self.assertTrue(all(note['message'] for note in related),related)
        self.assertTrue(all(tuple(note['location']['range']['start'][k] for k in ('line','character'))
            <=tuple(note['location']['range']['end'][k] for k in ('line','character')) for note in related),related)
        self.assertTrue(any(note['location']['range']['start']==self.position(changed,'helper()',0) for note in related),related)

    def test_library_violation_is_reported_at_user_call(self):
        text='''package main
import "core:strings"
@(no_alloc)
main::proc(){value:=strings.clone("hello");_=value}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'strings.clone("hello")',0))
        self.assertTrue(any('library implementation' in note['message'] for note in error.get('relatedInformation',[])),error)

    def test_io_and_unsaved_allocating_writer(self):
        text='''package main
import "core:fmt"
import "core:io"
sink::proc(data:rawptr,mode:io.Stream_Mode,bytes:[]byte,offset:i64,whence:io.Seek_From)->(i64,io.Error){return i64(len(bytes)),nil}
@(no_alloc)
main::proc(){writer:=io.Writer{procedure=sink};fmt.wprintln(writer,7)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('return i64(len(bytes)),nil','_=make([]int,1);return i64(len(bytes)),nil')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'make([]int,1)',0))
        self.assertTrue(any(note['location']['range']['start']==self.position(changed,'fmt.wprintln(writer,7)',0)
                            for note in error.get('relatedInformation',[])),error)
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_rng_callbacks_unsaved_allocation_and_recovery(self):
        text='''package main
import "base:runtime"
import "core:math/rand"
fill::proc(data:rawptr,mode:runtime.Random_Generator_Mode,buffer:[]byte){
 if mode==.Read {for &byte in buffer {byte=7}}
}
@(no_alloc)
main::proc(){
 _=rand.int_max(17)
 ctor::rand.default_random_generator
 state:rand.Default_Random_State;seeded:=ctor(&state);_=rand.uint64(seeded)
 generator:=rand.Generator{procedure=fill};_=rand.uint64(generator)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('byte=7','_=make([]int,1);byte=7')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'make([]int,1)',0))
        self.assertTrue(any(note['location']['range']['start']==self.position(changed,'rand.uint64(generator)',0)
                            for note in error.get('relatedInformation',[])),error)
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_ambient_rng_reports_the_context_replacement(self):
        text='''package main
import "core:math/rand"
input:rand.Generator
@(no_alloc)
main::proc(){context.random_generator=input;_=rand.uint64()}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='ambient RNG')
        error=next(e for e in errors if 'ambient RNG' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'rand.uint64()',0))
        self.assertTrue(any('context write' in note['message'] for note in error.get('relatedInformation',[])),error)

    def test_standard_input_and_print_do_not_report_namespace_errors(self):
        text='''#+vet no_alloc
package main
import "core:os"
import "core:fmt"
main::proc(){buffer:[32]byte;n,err:=os.read(os.stdin,buffer[:]);_=err;fmt.println(n)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)

    def test_panic_only_allocation_and_unsaved_normal_path_violation(self):
        text='''package main
import "core:fmt"
@(no_alloc)
main::proc(){
 value:=7
 fmt.ensuref(value>0,"bad value: %d",value)
 if value<0 {panic(f"bad value {value}")}
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('if value<0 {panic(f"bad value {value}")}','text:=f"value {value}";fmt.println(text)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'f"value {value}"',0))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_variadic_reductions_and_unsaved_allocation(self):
        text='''package main
import "core:fmt"
import "thor:basic" / and, or
sum::proc(values:..$T)->(result:T){for value in values {result+=value};return}
@(no_alloc)
main::proc(){
 values:>[true,false,true]
 numbers:=[2]int{2,3}
 fmt.println(and(..values[:]),or(..values[:]),and(true,false),or(),sum(..numbers[:]))
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)

        changed=text.replace('result+=value','_=make([]int,1);result+=value')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'make([]int,1)',0))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_generic_views_and_native_branches(self):
        text='''package main
import "core:strings"
neg::proc(value:$T)->T{return -value}
@(no_alloc)
main::proc(){
 values:=[1,2,3];pointer:=&values;tail:=pointer[:][1:]
 z:=complex(f64(1),f64(2));_=real(z)
 for i in 0..<3 {if i==1 {continue};if i==2 {break}}
 _=strings.trim_space(" abc ");_=neg(tail[0])
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('return -value','_=make([]int,1);return -value')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='no_alloc:')
        error=next(e for e in errors if 'no_alloc:' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'make([]int,1)',0))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_conditional_expression_effects_follow_unsaved_conditions(self):
        text='''package main
allocate::proc()->bool{_=make([]int,1);return true}
@(no_alloc)
main::proc(){_=false&&allocate()}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('false&&','true&&')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),contains='no_alloc:')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)



if __name__=='__main__':unittest.main()
