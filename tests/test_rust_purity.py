"""Independent native purity contracts; compile-time execution remains separate."""
import unittest
from tests import test_rust_compiler as native
UNCHECKED=native.UNCHECKED

class RustPurityTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program
    def test_local_scalar_mutation_and_transitive_recursion(self):
        source=self.program('n:=4;fmt.println(work(n))','''import "core:fmt"
set::proc(out:^int,n:int){out^=n+3}
recurse::proc(out:^int,n:int){if n==0{return};out^+=1;recurse(out,n-1)}
work::fn(n:int)->int{value:int;set(&value,n);recurse(&value,2);return value}
''')
        for flags in (['-debug'],UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):self.compare(source,flags=flags,expected='9')

    def test_scalar_results_release_local_allocations(self):
        source=self.program('n:=4;fmt.println(work(n))','''import "core:fmt"
fill::proc(values:[]int){for i in 0..<len(values){values[i]=i+1}}
work::fn(n:int)->int{values:=make([]int,n);fill(values);return values[n-1]}
''')
        for flags in (['-debug'],UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags,expected='4')
                self.assertEqual(result.stderr,'')

    def test_runtime_storage_context_io_and_borrow_writes_are_rejected(self):
        cases=[('state:int\n','return state+n','outside the function'),
               ('state:>3\n','return state+n','outside the function'),
               ('','_=context;return n','ambient context'),
               ('','fmt.println(n);return n','performs I/O'),
               ('','value:int=---;return value+n','uninitialized storage')]
        for prefix,body,message in cases:
            with self.subTest(body=body):
                self.compare(self.program('n:=4;_=work(n)','import "core:fmt"\n'+prefix+'work::fn(n:int)->int{'+body+'}\n'),command='check',fail=message)
        self.compare(self.program('data:=[]int{1};defer delete(data);_=work(data)','''
change::proc(data:[]int){data[0]=7}
work::fn(data:[]int)->int{change(data);return 0}
'''),command='check',fail='borrowed from an input')

    def test_purity_effect_rejection_inventory(self):
        from tests.purity_cases import EFFECTS
        names={'global_read','global_write','immutable_global','context','file_identity',
               'line_identity','directory_identity','caller_identity','project_identity',
               'address','uninitialized','aggregate_transmute','pointer_conversion',
               'local_any','local_map','local_set','allocator','record_formatter','io',
               'foreign','indirect','borrow_write','borrow_copy','borrow_copy_reordered',
               'borrow_delete','default_global','default_context'}
        for case in EFFECTS:
            if case.name in names:
                with self.subTest(case=case.name):
                    self.compare(case.source(),command='check',fail=case.message)

    def test_pointer_observation_and_source_identity_aliases(self):
        for body in ['a:=n;p:=&a;return int(uintptr(p))',
                     'a:=n;b:=n;p:=&a;q:=&b;switch p{case q:return 1;case:return 0}',
                     'return len(LOCATION)+n']:
            with self.subTest(body=body):
                message='Source-location-dependent' if 'LOCATION' in body else 'pointer identities'
                self.compare(self.program('n:=4;_=work(n)','LOCATION::#file\nwork::fn(n:int)->int{'+body+'}\n'),command='check',fail=message)

    def test_delete_of_borrowed_named_string_is_rejected(self):
        self.compare(self.program('_=work("hello")','work::fn(text:string)->int{alias:=text;delete(alias);return 0}\n'),command='check',fail='borrowed from an input')

    def test_multiple_io_calls_and_transitive_cause_have_original_locations(self):
        import json
        text=self.program('n:=4;_=work(n)','''import "core:fmt"
leaf::proc(n:int){fmt.print(n);fmt.println(n)}
work::fn(n:int)->int{leaf(n);return n}
''')
        self.source.write_text(text)
        result=self.invoke('rust',command='check',flags=['-json-errors'])
        self.assertEqual(result.returncode,1,result.stderr)
        errors=json.loads(result.stderr)['errors']
        self.assertEqual(len(errors),2,errors)
        self.assertTrue(all(e['pos']['offset']==text.index('leaf(n)') for e in errors),errors)
        self.assertTrue(all(e.get('related') for e in errors),errors)

    def test_owned_strings_slices_and_dynamic_results_survive_scratch_cleanup(self):
        source=self.program('''n:=3
text:=words(n);defer delete(text)
values:=build(n);defer delete(values)
growing:=grow(n);defer delete(growing)
source:=make([]int,2);source[0],source[1]=8,9;defer delete(source);frozen :> source
copy:=view(frozen);defer delete(copy);copy[0]=12
if text!="number 3"||values[2]!=9||len(growing)!=3||growing[2]!=5||copy[0]!=12||source[0]!=8 {panic("pure owned result")}
''','''words::fn(n:int)->string{return f"number {n}"}
build::fn(n:int)->[]int{result:=make([]int,n);for i in 0..<n{result[i]=i+n};defer result[n-1]=9;return result}
grow::fn(n:int)->[dynamic]int{result:[dynamic]int;for i in 0..<n {append(&result,i+n)};return result}
view::fn(values:[]int)->[]int{return values}
''')
        for flags in (['-debug'],UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_owned_named_multi_returns_and_bare_returns_preserve_defers(self):
        source=self.program('''n:=3
a,b:=both(n);defer delete(a);defer delete(b)
if a[0]!=9||b!="value 3" {panic("defer results")}
''','''both::fn(n:int)->(values:[]int,text:string){values=make([]int,1);values[0]=n;text=f"value {n}";defer values[0]=9;return}
''')
        for flags in (['-debug'],UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_nested_pure_results_use_the_enclosing_scratch_lifetime(self):
        source=self.program('n:=7;if consume(n)!=7 {panic("nested pure")};text:=named(n);defer delete(text);fmt.println(text)','''import "core:fmt"
build::fn(n:int)->[]int{values:=make([]int,1);values[0]=n;return values}
consume::fn(n:int)->int{values:=build(n);return values[0]}
named::fn(n:int)->string{_=n;return #procedure}
''')
        for flags in (['-debug'],UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_transitive_delete_checks_do_not_depend_on_ordinary_summary_cache(self):
        for annotation,argument in [('string','"hello"'),('[]int','[]int{1}')]:
            prefix='erase::proc(value:'+annotation+'){delete(value)}\nforward::proc(value:'+annotation+'){erase(value)}\nwork::fn(value:'+annotation+')->int{forward(value);return 0}\n'
            with self.subTest(annotation=annotation):
                self.compare(self.program('_=work('+argument+')',prefix),command='check',fail='borrowed from an input')

    def test_locally_owned_readonly_views_can_be_deleted_and_write_effects_dominate(self):
        source=self.program('n:=3;if work(n)!=3{panic("local readonly deletion")}','''
erase::proc(values:[]int){delete(values)}
work::fn(n:int)->int{a :> make([]int,n);delete(a);b :> make([]int,n);erase(b);return n}
''')
        for flags in (['-debug'],UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')
        prefix='change::proc(values:[]int){if false{delete(values)};values[0]=9}\nwork::fn(n:int)->int{values :> make([]int,n);change(values);return 0}\n'
        self.compare(self.program('n:=3;_=work(n)',prefix),command='check',fail='read-only slice')

    def test_opaque_casts_and_inferred_procedure_storage_are_rejected(self):
        for body,message in [('callback:=helper;_=callback;return n','Pure local storage'),
                             ('_=cast(rawptr)nil;return n','convert values to pointers'),
                             ('_=transmute(typeid)uintptr(n);return n','Pure transmute'),
                             ('value:=transmute(any)u128(0);_=value;return n','Pure')]:
            with self.subTest(body=body):
                self.compare(self.program('n:=3;_=work(n)','helper::proc()->int{return 1}\nwork::fn(n:int)->int{'+body+'}\n'),command='check',fail=message)
