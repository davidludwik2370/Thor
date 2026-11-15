"""Observable I/O is a source-located purity error, never a backend crash."""
import json
from pathlib import Path
import shutil
import unittest
from tests import test_thor as base


class PureIOTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_unused_print_board_reports_every_call_in_checked_and_release_builds(self):
        source='''// 😀 header
package main
import "core:fmt"
Board::[9]int
print_board::fn(board:Board){
    for card in board{
        if card>0 { /* 😀 */ fmt.print(card," ") }
        else {fmt.print("X "," ")}
    }
    fmt.println()
}
main::proc(){}
'''
        for command,flags in [('check',['-debug']),('build',['-debug']),
                              ('build',['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'])]:
            with self.subTest(command=command,flags=flags):
                root,result=self.invoke(source,command=command,flags=[*flags,'-json-errors'],fail='performs I/O')
                self.assertEqual(result.returncode,1,result.stderr)
                errors=json.loads(result.stderr)['errors']
                self.assertEqual(len(errors),3,errors)
                expected=[];start=0
                for name in ['fmt.print','fmt.print','fmt.println']:
                    offset=source.encode().index(name.encode(),start)
                    expected.append((offset,offset+len(name)))
                    start=offset+len(name)
                self.assertEqual([(e['pos']['offset'],e['pos']['end_offset']) for e in errors],expected)
                self.assertTrue(all(Path(e['pos']['file'])==root.resolve()/'input/main.thor' for e in errors))
                self.assertTrue(all(e['stage']=='checker' for e in errors),errors)
                self.assertTrue(all("Pure function 'print_board'" in ' '.join(e['msgs']) for e in errors),errors)
                self.assertNotIn('SIGBUS',result.stderr)
                self.assertNotIn('Thor expression',result.stderr)

    def test_io_error_follows_import_and_constant_aliases(self):
        for imports,target in [
            ('import f "core:fmt"','f.println'),
            ('import "core:fmt" / println','println'),
            ('import _ "core:fmt" / println','println'),
            ('import * "core:fmt"','println'),
            ('import "core:fmt"\noutput::fmt.println\nalias::(output)','alias'),
        ]:
            with self.subTest(imports=imports):
                source=self.program('',imports+'\nwork::fn(){'+target+'("no output")}\n')
                _,result=self.invoke(source,command='check',flags=['-json-errors'],fail='performs I/O')
                errors=json.loads(result.stderr)['errors']
                self.assertTrue(any(e.get('pos') and e['pos']['offset']==source.encode().index((target+'(').encode()) for e in errors),errors)
                self.assertNotIn('SIGBUS',result.stderr)

    def test_stdout_stderr_file_and_stream_printing_are_effects(self):
        for call in ['fmt.print("x")','fmt.println()','fmt.printf("%d",7)','fmt.printfln("%d",7)',
                     'fmt.eprint("x")','fmt.eprintln("x")','fmt.eprintf("%d",7)','fmt.eprintfln("%d",7)',
                     'fmt.fprint(nil,"x")','fmt.fprintln(nil,"x")','fmt.fprintf(nil,"%d",7)',
                     'fmt.fprintfln(nil,"%d",7)','fmt.wprint({},"x")','fmt.wprintln({},"x")',
                     'fmt.wprintf({},"%d",7)','fmt.wprintfln({},"%d",7)']:
            with self.subTest(call=call):
                self.invoke(self.program('', 'import "core:fmt"\nwork::fn(){'+call+'}\n'),
                            command='check',fail='performs I/O')

    def test_transitive_ordinary_helper_links_actual_io_from_the_fn_call(self):
        source=self.program('n:=3;work(n)','''import "core:fmt"
leaf::proc(n:int){/* 😀 */fmt.println(n)}
middle::proc(n:int){leaf(n)}
work::fn(n:int){middle(n)}
''')
        _,result=self.invoke(source,command='build',flags=['-debug','-json-errors'],fail='performs I/O')
        errors=json.loads(result.stderr)['errors']
        self.assertTrue(any(e.get('pos') and e['pos']['offset']==source.encode().index(b'middle(n)',source.encode().index(b'work::fn')) for e in errors),errors)
        self.assertTrue(any(note['pos']['offset']==source.encode().index(b'fmt.println')
                            for error in errors for note in error.get('related',[])),errors)
        self.assertNotIn('__thor_',result.stderr)

    def test_basic_io_is_rejected_without_inspecting_its_implementation(self):
        for call in ['basic.print("x")','_=basic.input()','_=basic.read("file")','_=basic.write("file","x")']:
            with self.subTest(call=call):
                self.invoke(self.program('', 'import "thor:basic"\nwork::fn(){'+call+'}\n'),
                            command='check',fail='performs I/O')

    def test_inactive_branches_and_type_queries_remain_unchecked_and_unexecuted(self):
        self.invoke(self.program('''
number:=3
if work(number)!=3 || query()!=size_of(int) {panic("pure values")}
''','''import "core:fmt"
when false{unused::fn(){fmt.println("inactive")}}
work::fn(value:$T)->T{when T==int{return value}else{fmt.println(value);return value}}
query::fn()->int{return size_of(type_of(fmt.println("unevaluated")))}
'''),expected='')
        self.invoke(self.program('_=work("active")','''import "core:fmt"
work::fn(value:$T)->T{when T==int{return value}else{fmt.println(value);return value}}
'''),command='check',fail='performs I/O')

    def test_plain_procedures_can_print_and_fn_can_still_format_strings(self):
        source=self.program('''
text:=describe(7)
output(text)
delete(text)
''','''import "core:fmt"
output::proc(text:string){fmt.println(text)}
describe::fn(value:int)->string{return f"value {value}"}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags,expected='value 7')
                self.assertNotIn('still allocated',result.stderr)

    def test_user_package_and_overridden_collection_names_are_not_io_contracts(self):
        self.invoke(self.program('n:=3;if work(n)!=4 {panic("user print")}', '''import fmt "library"
work::fn(n:int)->int{return fmt.print(n)}
'''),files={'library/main.odin':'package library\nprint::proc(n:int)->int{return n+1}\n'})
        root,_=self.invoke(self.program(''),command='transpile')
        collection=root/'support';package=collection/'basic';package.mkdir(parents=True)
        (package/'main.odin').write_text('package basic\nprint::proc(n:int)->int{return n+1}\n')
        shutil.copytree(base.ROOT/'runtime/pure',collection/'pure')
        shutil.copytree(base.ROOT/'runtime/typeinfo',collection/'typeinfo')
        shutil.copytree(base.ROOT/'runtime/sets',collection/'sets')
        self.invoke(self.program('n:=3;if work(n)!=4 {panic("custom basic")}', '''import "thor:basic"
work::fn(n:int)->int{return basic.print(n)}
'''),flags=['-collection:thor='+str(collection)])

    def test_overloads_only_reject_the_selected_io_target(self):
        prefix='''import "core:fmt"
pure_text::proc(text:string)->int{return len(text)}
choose::proc{fmt.print,pure_text}
work::fn(value:$T)->int{return choose(value)}
'''
        self.invoke(self.program('text:="hello";if work(text)!=5 {panic("pure overload")}',prefix),expected='')
        self.invoke(self.program('number:=7;_=work(number)',prefix),command='check',fail='performs I/O')

    def test_explicit_run_cannot_force_io(self):
        for prefix in ['VALUE::#run fmt.println("not executed")',
                       'log::proc()->int{return fmt.println("not executed")}\nVALUE::#run log()']:
            with self.subTest(prefix=prefix):
                _,result=self.invoke(self.program('_=VALUE','import "core:fmt"\n'+prefix+'\n'),
                    command='check',flags=['-json-errors'],fail='performs I/O')
                self.assertEqual(result.stdout,'')
                self.assertNotIn('SIGBUS',result.stderr)
                self.assertTrue(any(e.get('pos') for e in json.loads(result.stderr)['errors']))


if __name__=='__main__':unittest.main()
