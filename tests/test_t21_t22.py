"""Persistent helper caching and independently owned pure results."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests import test_thor as base
from thorlang.cache import EvaluationCache


class CacheStorageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-cache-test-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.cache=EvaluationCache(self.root)
        self.key='a'*64

    def test_atomic_concurrent_publication_and_clear_preserves_unrelated_files(self):
        packets=[{'kind':'integer','value':str(i)} for i in range(12)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda value:self.cache.put(self.key,value),packets))
        self.assertIn(self.cache.get(self.key),packets)
        keep=self.cache.directory/'keep.json';keep.write_text('mine')
        self.cache.clear()
        self.assertEqual(keep.read_text(),'mine')
        self.assertIsNone(self.cache.get(self.key))
        self.assertFalse(list(self.cache.directory.glob('.entry-*')))

    def test_corrupt_checksum_schema_and_partial_packets_are_misses(self):
        self.cache.put(self.key,{'kind':'integer','value':'7'})
        path=self.cache.directory/(self.key+'.json')
        original=json.loads(path.read_text())
        for contents in ['{',json.dumps({**original,'schema':999}),
                         json.dumps({**original,'value':{'kind':'integer','value':'9'}})]:
            path.write_text(contents)
            self.assertIsNone(self.cache.get(self.key))
        self.cache.put(self.key,original['value'])
        self.assertEqual(self.cache.get(self.key),original['value'])

    def test_toolchain_and_support_source_edits_change_identity(self):
        toolchain=self.root/'native';toolchain.mkdir()
        support=self.root/'support';(support/'runtime').mkdir(parents=True)
        library=toolchain/'core';library.mkdir()
        stdlib=library/'math.odin';stdlib.write_text('package math\n')
        runtime=support/'runtime/pure.odin';runtime.write_text('package pure\n')
        compiler=toolchain/'odin'
        compiler.write_text('#!/usr/bin/env python3\nimport sys\n'+
            f'print({str(toolchain)!r} if sys.argv[1]=="root" else "test compiler")\n')
        compiler.chmod(0o755)
        def identity():return EvaluationCache(self.root).identity(str(compiler),support)
        before=identity();stdlib.write_text('package math\nPI::3\n')
        self.assertNotEqual(before,identity())
        before=identity();runtime.write_text('package pure\nSCHEMA::2\n')
        self.assertNotEqual(before,identity())
        before=identity();compiler.write_text(compiler.read_text()+'# locally rebuilt compiler\n')
        self.assertNotEqual(before,identity())


class CacheTests(unittest.TestCase):
    program=base.ThorTests.program

    def invoke(self, source, *, flags=(), **kwargs):
        return base.ThorTests.invoke(self,source,flags=[*flags],**kwargs)

    def report(self,result):
        return next(json.loads(line)['thor_cache'] for line in result.stdout.splitlines() if line.startswith('{"thor_cache"'))

    def again(self,root,*,flags=(),fail=None):
        result=subprocess.run([str(base.THOR),'run',str(root/'input'),'--output-dir',str(root/'output'),
                               '--cache-report',*flags],capture_output=True,text=True,timeout=45)
        if fail:self.assertNotEqual(result.returncode,0);self.assertIn(fail,result.stderr)
        else:self.assertEqual(result.returncode,0,result.stderr)
        return result

    def test_warm_cache_bypass_clear_and_corruption_recovery(self):
        source=self.program('assert(VALUE==25)','square::fn(x:int)->int{return x*x}\nVALUE::#run square(5)\n')
        root,first=self.invoke(source,flags=['--cache-report'])
        self.assertEqual(self.report(first)['writes'],1)
        second=self.again(root)
        self.assertEqual(self.report(second)['hits'],1)
        bypass=self.again(root,flags=['--no-cache'])
        self.assertEqual(self.report(bypass)['hits'],0)
        self.assertGreater(self.report(bypass)['bypasses'],0)
        cleared=self.again(root,flags=['--clear-cache'])
        self.assertEqual(self.report(cleared)['writes'],1)
        entry=next((root/'input'/'.thor-cache'/'eval-v1').glob('*.json'))
        entry.write_text('{broken')
        recovered=self.again(root)
        self.assertEqual(self.report(recovered)['corrupt'],1)
        self.assertEqual(self.report(recovered)['writes'],1)

    def test_transitive_constant_callee_type_and_argument_changes_invalidate(self):
        prefix='import "library"\nVALUE::#run library.work("ab")\n'
        source=self.program('assert(VALUE==4)',prefix)
        library='package library\nItem::i32\nOFFSET::0\nhelper::fn(s:string)->int{return len(s)+OFFSET}\nwork::fn(s:string)->int{return helper(s)+size_of(Item)/2}\n'
        root,_=self.invoke(source,files={'library/main.thor':library},flags=['--cache-report'])
        self.assertEqual(self.report(self.again(root))['hits'],1)
        for old,new,expected in [('OFFSET::0','OFFSET::1',5),('len(s)+OFFSET','len(s)*2+OFFSET',7),('Item::i32','Item::i64',9)]:
            library=library.replace(old,new)
            (root/'input/library/main.thor').write_text(library)
            (root/'input/main.thor').write_text(self.program(f'assert(VALUE=={expected})',prefix))
            self.assertEqual(self.report(self.again(root))['hits'],0)
        prefix=prefix.replace('"ab"','"abc"')
        (root/'input/main.thor').write_text(self.program('assert(VALUE==11)',prefix))
        self.assertEqual(self.report(self.again(root))['hits'],0)

    def test_defines_and_resource_contents_invalidate(self):
        source=self.program('assert(VALUE==len(DATA)+MODE)','''
DATA::#load("data.txt",string)
MODE::#config(MODE,1)
work::fn()->int{return len(DATA)+MODE}
VALUE::#run work()
''')
        root,_=self.invoke(source,files={'data.txt':'abc'},flags=['--cache-report'])
        self.assertEqual(self.report(self.again(root))['hits'],1)
        self.assertEqual(self.report(self.again(root,flags=['-define:MODE=2']))['hits'],0)
        (root/'input/data.txt').write_text('different data')
        self.assertEqual(self.report(self.again(root))['hits'],0)

    def test_failed_evaluations_are_not_published_and_unwritable_cache_is_optional(self):
        source=self.program('_=VALUE','bad::fn()->int{assert(false);return 0}\nVALUE::#run bad()\n')
        root,result=self.invoke(source,command='check',flags=['--cache-report'],fail='#run execution failed')
        self.assertEqual(self.report(result)['writes'],0)
        # A valid toolchain fingerprint may be recorded before execution fails;
        # neither result packets nor records that reuse results may be published.
        for namespace in ['eval-v1','prepared-v1','scalar-v1']:
            self.assertFalse(list((root/'input/.thor-cache'/namespace).glob('*.json')))
        (root/'input/main.thor').write_text(source.replace('assert(false);',''))
        blocked=root/'blocked';blocked.write_text('not a directory')
        self.assertEqual(self.report(self.again(root,flags=['--cache-dir',str(blocked)]))['writes'],0)

    def test_warm_cache_skips_the_native_helper_build(self):
        source=self.program('assert(VALUE==49)','square::fn(n:int)->int{return n*n}\nVALUE::#run square(7)\n')
        root,_=self.invoke(source)
        marker=root/'block-helper'
        wrapper=root/'odin-wrapper'
        wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\n'
            f'if Path({str(marker)!r}).exists() and "build" in sys.argv and any("thor-run-" in x for x in sys.argv):\n'
            ' print("helper build blocked by test",file=sys.stderr);sys.exit(1)\n'
            f'os.execv({shutil.which("odin")!r},[{shutil.which("odin")!r},*sys.argv[1:]])\n')
        wrapper.chmod(0o755)
        self.assertEqual(self.report(self.again(root,flags=['--odin',str(wrapper)]))['writes'],1)
        marker.touch()
        self.assertEqual(self.report(self.again(root,flags=['--odin',str(wrapper)]))['hits'],1)
        self.again(root,flags=['--odin',str(wrapper),'--no-cache'],fail='helper build blocked by test')

    def test_allocated_cache_packets_are_contents_and_rematerialize(self):
        source=self.program('a:=#run work();b:=#run work();a[0]="changed";assert(b[0]=="hello");delete(b[0]);delete(a);delete(b)',
            'work::fn()->[]string{data:=make([]string,1);data[0]="hello";return data}\n')
        # Free the original a[0] before replacing its independently owned value.
        source=source.replace('a[0]="changed"','delete(a[0]);a[0]="changed"')
        root,_=self.invoke(source,flags=['--cache-report'])
        self.assertEqual(self.report(self.again(root))['hits'],2)
        packets=[json.loads(p.read_text())['value'] for p in (root/'input/.thor-cache/eval-v1').glob('*.json')]
        self.assertTrue(all(p['kind']=='sequence' and p['values'][0]['bytes']=='68656c6c6f' for p in packets))

    def test_resource_existence_and_dynamic_resource_paths(self):
        source=self.program('assert(VALUE==(1 when EXISTS else 0))',
            'EXISTS::#exists("optional.txt")\nwork::fn()->int{return 1 when EXISTS else 0}\nVALUE::#run work()\n')
        root,_=self.invoke(source,flags=['--cache-report'])
        self.assertEqual(self.report(self.again(root))['hits'],1)
        (root/'input/optional.txt').write_text('present')
        self.assertEqual(self.report(self.again(root))['hits'],0)
        path=root/'input/main.thor'
        path.write_text(source.replace('EXISTS::#exists("optional.txt")','PATH::"optional.txt"\nEXISTS::#exists(PATH)'))
        result=self.again(root)
        self.assertGreater(self.report(result)['bypasses'],0)

    def test_loaded_directory_contents_and_membership_invalidate(self):
        # Native #load_directory is runtime-initialized rather than an Odin
        # constant. Its static files still participate in conservative keys.
        source=self.program('data:=#load_directory("assets");assert(len(data)>0&&VALUE==7)',
            'work::fn()->int{return 7}\nVALUE::#run work()\n')
        root,_=self.invoke(source,files={'assets/a.txt':'abc'},flags=['--cache-report'])
        self.assertEqual(self.report(self.again(root))['hits'],1)
        (root/'input/assets/a.txt').write_text('edited')
        self.assertEqual(self.report(self.again(root))['hits'],0)
        (root/'input/assets/b.txt').write_text('second')
        self.assertEqual(self.report(self.again(root))['hits'],0)


class AllocatedResults(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def memory(self,body,prefix=''):
        return self.invoke('package main\nimport "core:testing"\nimport pure "thor:pure"\n'+prefix+
            '\n@(test) lifetime::proc(t:^testing.T){\n'+body+'\n}\n',command='test',
            flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_string_results_and_input_views_are_independent(self):
        self.memory('''
input:="hello"
a:=view(input);b:=view(input)
testing.expect(t,a=="ello"&&b==a&&raw_data(a)!=raw_data(input[1:])&&raw_data(a)!=raw_data(b))
delete(a);delete(b)
text:=message(7);testing.expect(t,text=="value 7");delete(text)
''','view::fn(s:string)->string{return s[1:]}\nmessage::fn(n:int)->string{return f"value {n}"}\n')

    def test_slice_results_and_return_defers(self):
        self.memory('''
input:=[1,2,3]
a:=duplicate(input[:]);b:=duplicate(input[:]);a[0]=9
testing.expect(t,input[0]==1&&b[0]==1&&a[0]==9)
pure.destroy(a);pure.destroy(b)
later:=deferred();testing.expect(t,later[0]==9);pure.destroy(later)
''','''
duplicate::fn(values:[]int)->[]int{return values}
deferred::fn()->[]int{data:=make([]int,1);data[0]=1;defer data[0]=9;return data}
''')

    def test_nested_aggregates_shared_input_aliases_and_recursive_cleanup(self):
        self.memory('''
input:=["one","two"]
value:=make_record(input[:])
testing.expect(t,value.rows[0][0]=="one"&&value.rows[1][1]=="two")
testing.expect(t,raw_data(value.rows[0])!=raw_data(value.rows[1]))
testing.expect(t,raw_data(value.rows[0][0])!=raw_data(value.rows[1][0]))
delete(value.rows[0][0]);value.rows[0][0]=pure.clone("edited")
testing.expect(t,value.rows[1][0]=="one"&&input[0]=="one")
pure.destroy(value)
''','''
Record::struct{rows:[2][]string}
make_record::fn(input:[]string)->Record{return {[input,input]}}
''')

    def test_nested_fn_calls_recursion_generics_and_multiple_results(self):
        self.memory('''
value:=recurse("abc",3);testing.expect(t,value=="abc");delete(value)
a,b:=both("text");testing.expect(t,a=="text"&&b[1]==2);delete(a);pure.destroy(b)
array:=repeat(2,"hi");testing.expect(t,array[0]=="hi"&&raw_data(array[0])!=raw_data(array[1]));pure.destroy(array)
''','''
identity::fn(input:$T)->T{return input}
recurse::fn(input:string,n:int)->string{if n==0{return identity(input)};return recurse(input,n-1)}
both::fn(input:string)->(text:string,values:[]int){data:=make([]int,2);data[1]=2;return input,data}
repeat::fn($N:int,input:string)->[N]string{result:[N]string;for i in 0..<N{result[i]=input};return result}
''')

    def test_run_materializes_fresh_storage_in_each_loop_iteration(self):
        self.memory('''
for i in 0..<3{
 text:=#run message(7);testing.expect(t,text=="value 7");delete(text)
 a:=#run numbers();b:=#run numbers();a[0]=9
 testing.expect(t,b[0]==1&&a[0]==9);pure.destroy(a);pure.destroy(b)
}
''','''
message::fn(n:int)->string{return f"value {n}"}
numbers::fn()->[]int{result:=make([]int,2);result[0]=1;result[1]=2;return result}
''')

    def test_run_nested_records_and_union_variants(self):
        self.memory('''
value:=#run work();testing.expect(t,value.name=="abc"&&value.values[1]==2);pure.destroy(value)
choice:=#run choose();data,ok:=choice.([]string);testing.expect(t,ok&&data[0]=="hello");pure.destroy(choice)
''','''
Record::struct{name:string,values:[]int}
Choice::union{int,[]string}
work::fn()->Record{data:=make([]int,2);data[1]=2;return {"abc",data}}
choose::fn()->Choice{data:=make([]string,1);data[0]="hello";return data}
''')

    def test_constant_strings_and_slice_constant_diagnostic(self):
        self.invoke(self.program('assert(VALUE=="hello")','work::fn()->string{return "hello"}\nVALUE::#run work()\n'))
        self.invoke(self.program('_=VALUE','work::fn()->[]int{return nil}\nVALUE::#run work()\n'),command='check',fail='slice-containing #run result needs a runtime binding')

    def test_nil_empty_and_zero_size_slice_elements(self):
        self.memory('''
a:=empty(false);b:=empty(true);c:=#run empty(false);d:=#run empty(true)
testing.expect(t,raw_data(a)==nil&&raw_data(c)==nil&&raw_data(b)!=nil&&raw_data(d)!=nil)
pure.destroy(a);pure.destroy(b);pure.destroy(c);pure.destroy(d)
e:=zero();f:=#run zero();testing.expect(t,len(e)==3&&len(f)==3);pure.destroy(e);pure.destroy(f)
''','''
Empty::struct{}
empty::fn(nonnull:bool)->[]int{if nonnull{data:=[7];return data[:0]};return nil}
zero::fn()->[]Empty{return make([]Empty,3)}
''')

    def test_enum_arrays_private_imported_types_and_raw_bytes(self):
        self.memory('''
a:=letters();b:=#run letters()
testing.expect(t,a[.One]=="one"&&b[.Two]=="\\x00\\xff");pure.destroy(a);pure.destroy(b)
''','''
Key::enum{One,Two}
letters::fn()->[Key]string{return {.One="one",.Two="\\x00\\xff"}}
''')
        self.invoke(self.program('a:=#run library.work();assert(a.text=="private");pure.destroy(a)',
            'import "library"\nimport pure "thor:pure"\n'),files={'library/main.thor':
                'package library\n@(private) Hidden::struct{text:string}\nwork::fn()->Hidden{return {"private"}}\n'})

    def test_constants_nested_runs_and_type_witness_dependencies(self):
        self.invoke(self.program('assert(COUNT==3);value:=#run work();assert(value=="abc");delete(value)', '''
work::fn()->string{return "abc"}
TEXT::#run work()
count::fn(input:string)->int{return len(input)}
COUNT::#run count(TEXT)
'''))
        self.memory('value:=#run outer();testing.expect(t,value=="nested");delete(value)', '''
inner::fn()->string{return "nested"}
outer::fn()->string{return #run inner()}
''')
        self.invoke(self.program('assert(COUNT==3)', '''
numbers::fn()->[]int{data:=[1,2,3];return data[:]}
count::fn(input:[]int)->int{return len(input)}
COUNT::#run count(#run numbers())
'''))

    def test_defaults_blank_parameters_named_returns_and_procedure_name(self):
        self.memory('''
a:=defaults();b:=blank(7);c:=named();d:=procedure_name()
testing.expect(t,a=="default"&&b=="blank"&&c=="named"&&d=="procedure_name")
delete(a);delete(b);delete(c);delete(d)
''','''
defaults::fn(input:string="default")->string{return input}
blank::fn(_:int)->string{return "blank"}
named::fn()->(result:string){result="named";return}
procedure_name::fn()->string{return #procedure}
''')

    def test_variadic_where_local_types_and_early_returns(self):
        self.memory('''
a:=constrained("where");b:=varargs("first","second");c:=local_type(true)
testing.expect(t,a=="where"&&b=="first"&&c.text=="early")
delete(a);delete(b);pure.destroy(c)
''','''
constrained::fn(input:$T)->T where T in [string,int]{return input}
varargs::fn(input:..string)->string{return input[0]}
local_type::fn(early:bool)->struct{text:string}{
 Local::struct{text:string};item:=Local{"early"}
 if early{return {item.text}};return {"late"}
}
''')

    def test_returned_input_becomes_mutable_without_mutating_input(self):
        self.memory('a:=outer();testing.expect(t,a[0]==9);pure.destroy(a)', '''
identity::fn(input:[]int)->[]int{return input}
outer::fn()->[]int{input:=[1,2];result:=identity(input[:]);result[0]=9;assert(input[0]==1);return result}
''')

    def test_local_array_view_is_promoted_after_user_defers(self):
        self.memory('''
a:=local();b:=#run local();testing.expect(t,a[0]==9&&b[0]==9);pure.destroy(a);pure.destroy(b)
''','local::fn()->[]int{data:=[1,2];defer data[0]=9;return data[:]}\n')
        self.invoke(self.program('a:=#run local();assert(a[0]==9);pure.destroy(a)',
            'import pure "thor:pure"\nlocal::fn()->[]int{data:=[1,2];defer data[0]=9;return data[:]}\n'),
            flags=['-warnings-as-errors'])

    def test_or_return_preserves_named_results_and_error_payloads(self):
        self.memory('''
a,ok:=outer(false);testing.expect(t,!ok&&a=="before");delete(a)
b,good:=outer(true);testing.expect(t,good&&b=="success");delete(b)
c,err:=outer_error();testing.expect(t,c=="prefix"&&err.(string)=="failed");delete(c);pure.destroy(err)
''','''
read::fn(ok:bool)->(value:string,success:bool){return "success",ok}
outer::fn(ok:bool)->(text:string,success:bool){text="before";value:=read(ok) or_return;return value,true}
Error::union{string}
read_error::fn()->(string,Error){return "unused",Error("failed")}
outer_error::fn()->(text:string,err:Error){text="prefix";value:=read_error() or_return;return value,nil}
''')

    def test_missing_returns_and_invalid_return_arity_still_fail(self):
        self.invoke(self.program('_=bad(true)','bad::fn(flag:bool)->string{if flag{return "yes"}}\n'),command='check',fail='Missing return')
        self.invoke(self.program('_=bad()','bad::fn()->string{return "yes",7}\n'),command='check',fail='count mismatch')

    def test_out_of_memory_promotion_fails_instead_of_returning_empty(self):
        self.invoke(self.program('context.allocator=mem.nil_allocator();a:=work();_=a',
            'import "core:mem"\nwork::fn()->string{return "hello"}\n'),fail='Pure result allocation failed')

    def test_ordinary_eligible_proc_results_and_runtime_local_capture(self):
        self.memory('a:=#run work();testing.expect(t,a[0]=="proc");pure.destroy(a)', '''
inner::proc()->[]string{data:=make([]string,1);data[0]="proc";return data}
work::proc()->[]string{return inner()}
''')
        self.memory('''
PREFIX::"local"
local::fn(input:string)->string{return f"{PREFIX}{input}"}
a:=local(" fn");testing.expect(t,a=="local fn");delete(a)
''')
