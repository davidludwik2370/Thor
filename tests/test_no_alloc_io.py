"""Allocation-free I/O: source identity, modes, aliases and real allocator counts."""
import json
import os
import subprocess
import shutil
import unittest
from tests import test_rust_compiler as base


class NoAllocIOTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke
    @property
    def engines(self):return base.compiler_engines()

    def check(self,body,imports='',decl='',fail=None,flags=(),header=''):
        if '@(init)' in decl:header+='#+feature global-context\n'
        self.source.write_text(header+'package main\n'+imports+'\n'+decl+'\n@(no_alloc)\nchecked::proc(){'+body+'}\nmain::proc(){checked()}\n')
        for engine in self.engines:
            result=self.invoke(engine,'check',flags=['-thread-count:1',*flags])
            with self.subTest(engine=engine,body=body):
                if fail:
                    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn(fail,result.stderr)
                else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def test_reviewed_sdk_crlf_checkout_preserves_effects_but_other_edits_fail(self):
        sdk=__import__('pathlib').Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        original=sdk/'core/fmt/fmt.odin'
        replacement=self.root/'format-crlf.odin'
        raw=original.read_bytes().replace(b'\r\n',b'\n')
        replacement.write_bytes(raw.replace(b'\n',b'\r\n'))
        overlay=self.root/'format-overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
        self.check('fmt.println(7)','import "core:fmt"',flags=['--overlay',str(overlay)])
        replacement.write_bytes(raw+b'\n// unreviewed change\n')
        self.check('fmt.println(7)','import "core:fmt"',flags=['--overlay',str(overlay)],fail='effect profile does not match')

    def test_relocated_runtime_collection_uses_actual_reviewed_sources(self):
        copied=self.root/'moved installation'
        for name in ['runtime','frontend']:
            shutil.copytree(base.ROOT/name,copied/name)
        imports='import basic "thor:basic"\nimport "core:fmt"'
        from unittest.mock import patch
        with patch.dict(os.environ,{'THOR_ROOT':str(copied)}):
            self.check('fmt.println(7);basic.print(8)',imports)
            source=copied/'runtime/basic/basic.thor'
            source.write_bytes(source.read_bytes()+b'\n// changed source snapshot\n')
            self.check('basic.print(8)',imports,fail='effect profile does not match')

    def test_user_thor_collection_does_not_replace_compiler_io_proofs(self):
        custom=self.root/'custom';(custom/'basic').mkdir(parents=True)
        source=custom/'basic/basic.odin'
        source.write_text('package custom_basic\nprint::proc(value:int){_=value}\n')
        flags=['-collection:thor='+str(custom)]
        imports='import custom "thor:basic"\nimport "core:fmt"'
        self.check('custom.print(1);fmt.println(7)',imports,flags=flags)
        source.write_text('package custom_basic\nprint::proc(value:int){_=make([]int,value)}\n')
        self.check('custom.print(1);fmt.println(7)',imports,flags=flags,fail="builtin 'make'")

    def test_test_only_effects_do_not_invalidate_an_ordinary_build(self):
        # Odin accepts ordinary sources named *_test; only registered test
        # procedures are inactive in a normal build, not their whole file.
        self.source.write_text('''package main
import "core:fmt"
import "core:os"
import "core:testing"
@(test) changes_stream::proc(t:^testing.T){ptr:=&os.stdout;_=ptr;_=t}
@(no_alloc) checked::proc(){fmt.println(7)}
main::proc(){checked()}
''')
        for engine in self.engines:
            good=self.invoke(engine,'check')
            self.assertEqual(good.returncode,0,good.stdout+good.stderr)
            bad=self.invoke(engine,'build',flags=['-build-mode:test'])
            self.assertNotEqual(bad.returncode,0,bad.stderr)
            self.assertIn('native file metadata escapes',bad.stderr)
        self.source.write_text(self.source.read_text().replace('main::proc(){checked()}',
            'main::proc(){changes_stream(nil);checked()}'))
        bad=self.invoke('rust','check')
        self.assertNotEqual(bad.returncode,0,bad.stderr)
        self.assertIn('native file metadata escapes',bad.stderr)

    def test_native_test_runner_terminal_queries_preserve_no_alloc_printing(self):
        self.source.write_text('''package main
import "core:fmt"
import "core:testing"
@(no_alloc) show::proc(){fmt.println(7)}
@(test) verify::proc(t:^testing.T){show();_=t}
main::proc(){show()}
''')
        for engine in self.engines:
            for flags in [[],base.UNCHECKED]:
                result=self.invoke(engine,'test',flags=flags)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('Finished 1 test',result.stdout+result.stderr)

    def test_print_families_aliases_and_named_arguments(self):
        for body in ['fmt.print(7);fmt.println(8,sep=",",flush=false)',
                     'fmt.printf("%d %.2f %v %%\\n",7,2.5,true);fmt.printfln("%s","ok")',
                     'fmt.eprint(1);fmt.eprintln(2);fmt.eprintf("%d",3);fmt.eprintfln("%d",4)',
                     'fmt.fprint(os.stdout,1);fmt.fprintln(f=os.stderr,flush=false)',
                     'fmt.fprintf(os.stdout,"%d",1);fmt.fprintfln(os.stdout,"%d",2)',
                     'fmt.wprint(os.to_writer(os.stdout),1);fmt.wprintln(os.to_writer(os.stderr),2)',
                     'fmt.wprintf(os.to_writer(os.stdout),"%d",3);fmt.wprintfln(os.to_writer(os.stderr),"%d",4)',
                     'a::fmt.println;b::a;c:=b;c(7)',
                     'wrap(1,"ok")']:
            with self.subTest(body=body):self.check(body,'import "core:fmt"\nimport "core:os"',
                'wrap::proc(args:..any){fmt.println(..args)}')

    def test_runtime_print_alias_uses_declared_defaults_and_native_slot_meanings(self):
        decl='''prototype::proc(stream:io.Writer,format:string,args:..any,newline:=true,flush:=false)->int{return 0}
sink::proc(data:rawptr,mode:io.Stream_Mode,bytes:[]byte,offset:i64,whence:io.Seek_From)->(i64,io.Error){
 #partial switch mode {case .Write:return i64(len(bytes)),nil;case:_=make([]int,1);return 0,.Unsupported}
}'''
        body='callback:=prototype;callback=fmt.wprintf;writer:=io.Writer{procedure=sink};'
        imports='import "core:io"\nimport "core:fmt"'
        self.check(body+'_=callback(writer,"%d",1)',imports,decl,fail="builtin 'make'")
        self.check(body+'_=callback(writer,"%d",1,newline=false)',imports,decl)

    def test_namespace_is_not_runtime_storage(self):
        self.check('_=math.PI;fmt.println(math.PI)','import "core:math"\nimport "core:fmt"')
        self.check('fmt:=Record{2};_=fmt.value',decl='Record::struct{value:int}')

    def test_basic_print_concrete_and_forwarded_arguments(self):
        self.check('print(1,true,"hello");wrapped(2,"x")','import * "thor:basic"',
                   'wrapped::proc(args:..any){print(..args)}')
        self.check('opaque(value)','import * "thor:basic"','value:any\nopaque::proc(value:any){print(value)}',
                   fail='cannot prove allocation freedom')

    def test_fixed_buffer_native_and_stream_reads(self):
        for body in ['buf:[16]byte;_,_=os.read(os.stdin,buf[:])',
                     'buf:[16]byte;_,_=io.read(os.to_reader(os.stdin),buf[:])',
                     'buf:[16]byte;_,_=io.read_at(os.to_reader(os.stdin),buf[:],0)',
                     '_,_=os.write_string(os.stdout,"ok");_=os.flush(os.stdout)']:
            with self.subTest(body=body):self.check(body,'import "core:os"\nimport "core:io"')

    def test_known_stream_callback_only_checks_reachable_mode(self):
        decl='''sink::proc(data:rawptr,mode:io.Stream_Mode,bytes:[]byte,offset:i64,whence:io.Seek_From)->(i64,io.Error){
 #partial switch mode {case .Write: return i64(len(bytes)),nil;case: _=make([]int,1);return 0,.Unsupported}
}'''
        self.check('writer:=io.Writer{procedure=sink};_,_=io.write(writer,[]byte{1,2})','import "core:io"',decl)
        for op in ['io.read','io.read_at','io.write_at']:
            args=',0' if op.endswith('_at') else ''
            self.check('writer:=io.Writer{procedure=sink};_,_='+op+'(writer,[]byte{1}'+args+')',
                       'import "core:io"',decl,fail="builtin 'make'")
        self.check('writer:=io.Writer{procedure=sink};fmt.wprint(writer,7,flush=false)',
                   'import "core:io"\nimport "core:fmt"',decl)
        self.check('writer:=io.Writer{procedure=sink};fmt.wprint(writer,7)',
                   'import "core:io"\nimport "core:fmt"',decl,fail="builtin 'make'")

    def test_callback_alias_writes_and_control_joins_fail_closed(self):
        decl='''safe::proc(){}
bad::proc(){_=make([]int,1)}
Pair::struct{callback:proc()}
change::proc(p:^Pair){p.callback=bad}
'''
        for body in ['p:=Pair{safe};p.callback=bad;p.callback()',
                     'p:=Pair{safe};change(&p);p.callback()',
                     'p:=Pair{safe};using p;callback=bad;p.callback()',
                     'p:=Pair{safe};for i in 0..<2 {p.callback();p.callback=bad}',
                     'callback:=safe;for i in 0..<2 {callback();callback=bad}']:
            with self.subTest(body=body):self.check(body,decl=decl,fail='no_alloc:')
        self.check('p:=Pair{safe};p.callback()',decl=decl)

    def test_qualified_modes_and_dynamic_modes(self):
        decl='''apply::proc(mode:io.Stream_Mode){#partial switch mode {case .Write: case: _=make([]int,1)}}'''
        self.check('apply(io.Stream_Mode.Write)','import "core:io"',decl)
        self.check('apply(.Read)','import "core:io"',decl,fail="builtin 'make'")

    def test_fixed_buffer_buffered_writer(self):
        self.check('b:bufio.Writer;buf:[32]byte;bufio.writer_init_with_buf(&b,os.to_writer(os.stdout),buf[:]);'
                   'fmt.wprintln(bufio.writer_to_writer(&b),42);_=bufio.writer_flush(&b)',
                   'import "core:bufio"\nimport "core:os"\nimport "core:fmt"')
        self.check('b:bufio.Writer;bufio.writer_init(&b,os.to_writer(os.stdout))',
                   'import "core:bufio"\nimport "core:os"',fail='no_alloc:')

    def test_allocating_io_and_formatting_stay_rejected(self):
        for body in ['_=fmt.aprint(1)','_=fmt.tprint(1)','_=fmt.aprintf("%d",1)',
                     'text:=f"{7}";fmt.println(text)',
                     'fmt.printf("%v",..make([]any,65))']:
            with self.subTest(body=body):self.check(body,'import "core:fmt"',
                'format:string\ndynamic_format::proc(format:string){fmt.printf(format,1)}',fail='no_alloc:')

    def test_opaque_writers_and_mutable_global_callbacks_rejected(self):
        self.check('writer:io.Writer;_,_=io.write(writer,[]byte{1})','import "core:io"',fail='stream callback target')
        self.check('invoke()',decl='callback:proc()\ninvoke::proc(){callback()}',fail='indirect or unresolved')

    def test_native_stream_replacement_aliases_and_escapes_rejected(self):
        for setup in ['os.stdout.stream.procedure=nil',
                      'alias:=os.stdout;alias.stream.procedure=nil',
                      'ptr:=&os.stdout;_=ptr',
                      'ptr:=rawptr(os.stdout);_=ptr',
                      'using os.stdout.stream;procedure=nil',
                      'using os.stdout.stream;ptr:=&procedure;_=ptr']:
            self.check('fmt.println(7)','import "core:os"\nimport "core:fmt"',
                       '@(init)\nconfigure::proc(){'+setup+'}',fail='cannot prove allocation freedom')

    def test_formatter_registry_is_not_assumed_empty(self):
        self.check('fmt.println(7)','import "core:fmt"',
            'registry:map[typeid]fmt.User_Formatter\n@(init)\nconfigure::proc(){fmt.set_user_formatters(&registry)}',
            fail='mutable user-formatter registry')

    def test_omitted_allocating_default_is_checked(self):
        self.check('fmt.println(7);helper()','import "core:fmt"',
                   'helper::proc(data:[]int=make([]int,1)){}',fail="builtin 'make'")

    def test_readonly_buffer_permissions_are_preserved(self):
        self.check('values:>[]byte{1,2};fmt.println(values)','import "core:fmt"')
        self.check('values:>[]byte{1,2};_,_=os.read(os.stdin,values)','import "core:os"',fail='read-only slice')

    def test_modified_sdk_overlay_cannot_inherit_profile(self):
        from pathlib import Path
        root=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        original=root/'core/fmt/fmt.odin'
        replacement=self.root/'changed-fmt.odin'
        replacement.write_bytes(original.read_bytes()+b'\n// changed source snapshot\n')
        packet=self.root/'overlay.json'
        packet.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
        self.check('fmt.println(7)','import "core:fmt"',flags=['--overlay',str(packet)],fail='effect profile does not match')

    def test_registry_aliases_and_generic_native_file_writes(self):
        self.check('fmt.println(7)','import "core:fmt"',
            'registry:map[typeid]fmt.User_Formatter\n@(init)\nconfigure::proc(){setter:=fmt.set_user_formatters;setter(&registry)}',
            fail='mutable user-formatter registry')
        self.check('fmt.println(7)','import "core:fmt"\nimport "core:os"',
            'mutate::proc(f:^$T){f.stream.procedure=nil}\n@(init)\nconfigure::proc(){mutate(os.stdout)}',
            fail='cannot prove allocation freedom')

    def test_loop_hidden_stores_switch_initializers_and_defer(self):
        decl="""safe::proc(){}
bad::proc(){_=make([]int,1)}
change::proc(p:^proc()){p^=bad}
flag:int
"""
        for body in ['callback:=safe;for i in 0..<2 {callback();change(&callback)}',
                     'callback:=bad;#partial switch flag {case 1: callback=safe;case:callback()}',
                     'switch values:=make([]int,1); flag {case 1: _=values;case:}',
                     'p:Pair;defer init(&p);p.callback()']:
            with self.subTest(body=body):self.check(body,decl=decl+'Pair::struct{callback:proc()}\ninit::proc(p:^Pair){p.callback=safe}',fail='no_alloc:')
        self.check('callback:=safe;_=7;callback()',decl=decl)

    def test_fixed_buffer_reader_and_buffered_flush_modes(self):
        self.check('reader:bufio.Reader;buf:[32]byte;bufio.reader_init_with_buf(&reader,os.to_reader(os.stdin),buf[:]);'
                   '_,_=bufio.reader_read_slice(&reader,10)',
                   'import "core:bufio"\nimport "core:os"')
        decl="""sink::proc(data:rawptr,mode:io.Stream_Mode,bytes:[]byte,offset:i64,whence:io.Seek_From)->(i64,io.Error){
 #partial switch mode {case .Flush: return 0,nil;case: _=make([]int,1);return 0,.Unsupported}
}"""
        self.check('b:bufio.Writer;buf:[32]byte;bufio.writer_init_with_buf(&b,io.Writer{procedure=sink},buf[:]);_=bufio.writer_flush(&b)',
                   'import "core:bufio"\nimport "core:io"',decl,fail="builtin 'make'")

    def test_spread_printf_and_implicit_numeric_width_are_checked(self):
        self.check('values:[65]any;fmt.printf("%v",..values[:])','import "core:fmt"')
        self.check('fmt.printf("%.200b",1)','import "core:fmt"')
        self.check('fmt.printf("%0.200b",1)','import "core:fmt"')
        self.check('values:=[1,2];for value in values {fmt.println(value)}','import "core:fmt"')

    def test_printf_dynamic_formats_and_failure_only_limits(self):
        self.check('render("%d",8,2)', 'import "core:fmt"',
            'render::proc(format:string,width,precision:int){fmt.printf(format,7);fmt.printf("%*.*f",width,precision,2.5)}')
        self.check('fmt.printf("%v",'+','.join('1' for _ in range(65))+')','import "core:fmt"')
        self.check('writer:=os.to_writer(os.stdout);radix:=10;_,_=io.write_i64(writer,42,radix)',
                   'import "core:io"\nimport "core:os"')

    def test_checked_and_unchecked_first_io_uses_no_context_allocation(self):
        code='''package main
import "core:os"
import "core:fmt"
import "core:mem"
import * "thor:basic"
@(no_alloc)
work::proc()->int{
 buffer:[32]byte
 n,err:=os.read(os.stdin,buffer[:])
 _=err
 fmt.printf("%s",string(buffer[:n]))
 print(7,true,end="!")
 return n
}
main::proc(){
 backing:=context.allocator;temp:=context.temp_allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 context.allocator=mem.tracking_allocator(&tracker);context.temp_allocator=context.allocator
 n:=work();count:=tracker.total_allocation_count
 context.allocator=backing;context.temp_allocator=temp
 mem.tracking_allocator_destroy(&tracker)
 if n<0 || count!=0 {panic("I/O allocated")}
}
'''
        self.source.write_text(code)
        for engine in self.engines:
            for flags in [[],base.UNCHECKED,['-debug']]:
                output=self.root/(engine+'-io-program'+('.exe' if os.name=='nt' else ''))
                result=self.invoke(engine,'build',flags=['-thread-count:1',f'-out:{output}',*flags])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                for data in [b'hello',b'',b'\xc3\xa9abc',b'\xffabc',b'x'*32,b'y'*33]:
                    run=subprocess.run([str(output)],input=data,capture_output=True,timeout=10)
                    self.assertEqual(run.returncode,0,run.stderr)
                    self.assertEqual(run.stdout,data[:32]+b'7 true!')

    def test_io_proof_is_cached_within_request(self):
        self.source.write_text('package main\nimport "core:fmt"\n@(no_alloc)\nmain::proc(){'+''.join('fmt.println(7);' for _ in range(100))+'}')
        profile=self.root/'profile.json'
        result=self.invoke('rust','check',env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(profile.read_text())
        self.assertEqual(info['counters']['no_alloc.io_graphs'],1,info)
        self.assertEqual(info['counters']['no_alloc.io_sdk_snapshots'],1,info)


if __name__=='__main__':unittest.main()
