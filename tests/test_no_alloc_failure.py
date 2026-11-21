"""Fatal reports may allocate; ordinary paths and eager check arguments may not."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as base

class NoAllocFailureTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    compare=base.RustCompilerTests.compare
    invoke=base.RustCompilerTests.invoke

    def check(self,body,decl='',*,fail=None,flags=()):
        return self.compare('package main\n'+decl+'\n@(no_alloc)\nprobe::proc(){'+body+'}\nmain::proc(){}\n',
            command='check',flags=['-thread-count:1',*flags],fail=fail)

    def test_builtin_panics_aliases_and_runtime_exports(self):
        for body in ['panic("fatal")','panic_contextless("fatal")','unimplemented("todo")',
                     'unimplemented_contextless("todo")','stop::panic;alias::stop;alias("fatal")',
                     'runtime.panic("fatal")','runtime.unimplemented("todo")']:
            with self.subTest(body=body):self.check(body,'import "base:runtime"')

    def test_fatal_message_construction_and_optional_failure(self):
        self.check('panic(f"failure {7}")')
        self.check('panic(fmt.aprintf("%d",7))','import "core:fmt"')
        self.check('panic(message())','import "core:fmt"\nmessage::proc()->string{return fmt.aprint(7)}')
        self.check('v:=get() or_panic;_=v','get::proc()->(int,bool){return 7,true}')
        self.check('v:=get() or_panic;_=v','get::proc()->(int,bool){_=make([]int,1);return 7,true}',fail="builtin 'make'")
        self.check('values:=make([]int,1);panic("fatal")',fail="builtin 'make'")

    def test_assertions_and_formatted_checks_only_exempt_their_failure_body(self):
        for body in ['assert(flag)','ensure(flag)','assert_contextless(flag)','ensure_contextless(flag)',
                     'fmt.assertf(flag,"%d",7)','fmt.ensuref(flag,"%d",7)',
                     'log.assert(flag)','log.ensure(flag)','log.assertf(flag,"%d",7)','log.ensuref(flag,"%d",7)']:
            with self.subTest(body=body):self.check(body,'import "core:fmt"\nimport "core:log"\nflag:bool')
        self.check('ensure(false,f"failure {7}")')
        for body in ['ensure(true,f"message {7}")','ensure(flag,f"message {7}")',
                     'fmt.ensuref(true,"%v",make([]int,1))', 'assert(allocates())']:
            with self.subTest(body=body):self.check(body,'import "core:fmt"\nflag:bool\nallocates::proc()->bool{_=make([]int,1);return true}',fail='no_alloc:')
        self.check('assert(allocates(),f"message {7}")','allocates::proc()->bool{_=make([]int,1);return true}',flags=['-disable-assert'])
        self.check('ensure(allocates())','allocates::proc()->bool{_=make([]int,1);return true}',flags=['-disable-assert'],fail="builtin 'make'")

    def test_formatted_panic_and_logging_aliases(self):
        for body in ['fmt.panicf("%v",7)','log.panic("failure",7)','log.panicf("%v",7)',
                     'stop::fmt.panicf;stop("%v",7)','stop:=fmt.panicf;stop("%v",7)']:
            with self.subTest(body=body):self.check(body,'import "core:fmt"\nimport "core:log"')
        self.check('log.fatal(7)','import "core:log"',fail='no_alloc:')

    def test_same_names_and_arbitrary_divergent_procedures_still_checked(self):
        self.check('panic("bad")','panic::proc(message:string)->!{_=make([]int,1);for{}}',fail="builtin 'make'")
        self.check('spin()','spin::proc()->!{_=make([]int,1);for{}}',fail="builtin 'make'")
        self.check('invoke(callback)','callback: proc() -> ! = nil\ninvoke::proc(cb:proc() -> !){cb()}',fail='indirect or unresolved')
        self.check('stop:=fmt.panicf;stop=spin;stop("%v",7)',
            'import "core:fmt"\nspin::proc(format:string,args:..any)->!{_=make([]int,1);for{}}',fail="builtin 'make'")

    def test_eager_message_arguments_do_not_use_mutable_condition_facts(self):
        for condition in ['flag','(flag)','!flag']:
            initial='true' if condition=='!flag' else 'false'
            with self.subTest(condition=condition):
                self.check(f'flag:={initial};ensure({condition},f"message {{7}}")',fail='no_alloc:')
        self.check('assert(flag,message=f"message {7}")','flag:bool',fail='no_alloc:')

    def test_runtime_check_alias_uses_its_declared_argument_names_and_defaults(self):
        decl='''import "core:fmt"
message::proc()->string{return fmt.aprintf("%d",7)}
prototype::proc(flag:bool=true,text:string="ok",values:..any,loc:=#caller_location){}
'''
        self.check('check:=prototype;check=fmt.ensuref;check(text=message())',decl,fail='no_alloc:')
        self.check('check:=prototype;check=fmt.ensuref;check(flag=true,text="ok")',decl)
        self.check('check:=prototype;check=fmt.ensuref;check()',decl)
        self.check('check:=prototype;check=fmt.ensuref;check(flag=false,text=message())',decl)
        self.check('check:=prototype;check=fmt.assertf;check(flag=true,text=message())',decl,flags=['-disable-assert'],fail='no_alloc:')

    def test_failure_summaries_validate_the_actual_sdk_overlay(self):
        root=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        for relative,body,imports in [('core/fmt/fmt.odin','fmt.ensuref(true,"ok")','import "core:fmt"'),
                                      ('core/log/log.odin','log.panic("bad")','import "core:log"'),
                                      ('base/runtime/core.odin','ensure(true)',''),
                                      ('base/runtime/core_builtin.odin','panic("bad")','')]:
            with self.subTest(relative=relative):
                original=root/relative;replacement=self.root/'changed.odin'
                replacement.write_bytes(original.read_bytes()+b'\n// changed source snapshot\n')
                packet=self.root/'overlay.json'
                packet.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
                self.check(body,imports,flags=['--overlay',str(packet)],fail='fatal-reporting SDK profile does not match')

    def test_collection_override_does_not_acquire_reporting_effects(self):
        self.compare('package main\nimport "core:fmt"\n@(no_alloc)\nmain::proc(){fmt.ensuref(true,"ok")}\n',
            files={'core/fmt/fmt.odin':'package fmt\nensuref::proc(condition:bool,format:string,args:..any){_=make([]int,1)}\n'},
            flags=['-thread-count:1','-collection:core='+str(self.app/'core')],command='check',fail="builtin 'make'")

    def test_native_vet_and_file_contract_accept_fatal_fstrings(self):
        self.compare('''#+vet no_alloc, unused
package main
main::proc(){value:=7;if value<0 {panic(f"bad value {value}")}}
''',command='check',flags=['-thread-count:1','-vet'])

    def test_switch_match_mutation_cannot_keep_a_stale_callback_target(self):
        decl='''safe::proc(){}
bad::proc(){_=make([]int,1)}
change::proc(p:^proc())->int{p^=bad;return 1}
flag:int
'''
        self.check('callback:=safe;switch flag {case change(&callback):panic("bad");case:callback()}',
                   decl,fail='no_alloc:')

    def test_switch_fallthrough_does_not_restore_old_callback_targets(self):
        decl='''safe::proc(){}
bad::proc(){_=make([]int,1)}
change::proc(p:^proc()){p^=bad}
flag:int
'''
        for effect in ['callback=bad','change(&callback)']:
            with self.subTest(effect=effect):
                self.check('callback:=safe;switch flag {case 1:'+effect+';fallthrough;case 2:callback()}',
                           decl,fail='no_alloc:')

    def test_fatal_paths_do_not_hide_other_branches_or_loop_continuations(self):
        decl='flag:bool\nbad::proc(){_=make([]int,1)}\n'
        for body in ['if flag {panic("bad")} else {bad()}',
                     'for flag {panic("bad")};bad()',
                     'switch flag {case true:panic("bad");case false:bad()}',
                     '_=switch flag {case true:panic("bad");case false:alloc()}',
                     'if flag {defer panic("bad")};bad()',
                     'value:=get() or_else panic("bad");_=value;bad()',
                     'value:=get() or_panic;_=value;bad()',
                     '_=flag?panic("bad"):alloc()']:
            with self.subTest(body=body):self.check(body,decl+'get::proc()->(int,bool){return 1,true}\nalloc::proc()->int{bad();return 1}',fail="builtin 'make'")
        self.check('panic("fatal");bad()',decl,fail='Statements after a diverging')
        self.check('if flag {panic("fatal")};good()','flag:bool\ngood::proc(){}')
        self.check('for flag {panic("fatal")};good()','flag:bool\ngood::proc(){}')

    def test_unbound_compile_time_branches_are_independent(self):
        for expression in ['when flag {panic("bad")} else {bad()}',
                           '_=panic("bad") when flag else alloc()']:
            with self.subTest(expression=expression):
                self.compare('package main\nbad::proc(){_=make([]int,1)}\nalloc::proc()->int{bad();return 1}\n@(no_alloc)\ncheck::proc($flag:bool){'+expression+'}\nmain::proc(){check(true)}',
                    command='check',flags=['-thread-count:1'],fail="builtin 'make'")

    def test_runtime_success_paths_are_allocation_free_checked_and_unchecked(self):
        source='''package main
import "core:mem"
import "core:fmt"
@(no_alloc)
calculate::proc(value:int)->int{
    ensure(value>0)
    fmt.ensuref(value>0,"bad value: %d",value)
    fmt.assertf(value>0,"bad value: %d",value)
    result:=get(value) or_else panic("impossible")
    _=result
    return get_one(value) or_panic
}
get::proc(value:int)->(int,bool){return value,true}
get_one::proc(value:int)->(int,bool){return value,true}
main::proc(){
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    previous:=context.allocator
    previous_temp:=context.temp_allocator
    context.allocator=mem.tracking_allocator(&tracker)
    context.temp_allocator=context.allocator
    result:=calculate(7)
    count:=tracker.total_allocation_count
    context.allocator=previous
    context.temp_allocator=previous_temp
    if result!=7||count!=0 {panic("failure-path regression")}
    fmt.println(result)
}
'''
        for flags in [[],base.UNCHECKED]:
            with self.subTest(flags=flags):self.compare(source,flags=['-thread-count:1',*flags],expected='7')

    def test_runtime_fatal_reporting_can_allocate_without_a_crash_dialog(self):
        source='''package main
import "base:runtime"
import "core:fmt"
import "core:os"
handler::proc(prefix,message:string,loc:runtime.Source_Code_Location)->!{
    text:=fmt.aprintf("handled: %s",message)
    fmt.println(text)
    delete(text)
    os.exit(23)
}
@(no_alloc)
fail::proc(){fmt.panicf("value %d",7)}
main::proc(){context.assertion_failure_proc=handler;fail()}
'''
        for expression in ['fmt.panicf("value %d",7)','panic(f"value {7}")',
                           'stop::panic;stop(f"value {7}")',
                           'stop:=fmt.panicf;stop(f"value {7}")']:
            self.source.write_text(source.replace('fmt.panicf("value %d",7)',expression))
            for engine in ['python','rust']:
                for flags in [[],base.UNCHECKED]:
                    result=self.invoke(engine,flags=['-thread-count:1','--track-allocations','off','--no-sanitize',*flags])
                    self.assertEqual((result.returncode,result.stdout),(23,'handled: value 7\n'),result.stderr)

if __name__=='__main__':unittest.main()
