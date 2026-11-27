"""Result provenance, physical allocation transfer and logical SOA materialization."""
import json
import unittest

from tests import test_rust_compiler as native


class FnResultOwnershipTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare

    def test_borrowed_results_require_explicit_ownership(self):
        prefix = '''package main
import "core:slice"
Rows :: []int
Box :: struct { rows: Rows }
TABLE :: []int{1,2}
'''
        cases = [
            'f::fn(x:Rows)->Rows{x}',
            'f::fn(x:Rows)->Rows{alias:=x;alias[1:]}',
            'f::fn(x:Rows)->Box{Box{x}}',
            'f::fn(x:Box)->Rows{x.rows}',
            'f::fn(x:[][]int)->[][]int{slice.clone(x)}',
            'f::fn(x:Rows,b:bool)->(int,Rows){switch{case b:1,x;case:0,nil}}',
            'f::fn(x:Rows)->(out:Rows){out=x;return}',
            'f::fn()->Rows{TABLE}',
            'f::fn()->Rows{[]int{1,2}}',
            'f::fn(x:Integer)->Integer{x}',
            'f::fn(x:int)->Rows{return switch x{case 0:{1,2};case:nil}}',
            'f::fn()->Rows{a:Rows;a={1,2};return a}',
            'f::fn(a:[][]int)->Rows{for row in a{return row};return nil}',
            'f::fn()->[dynamic]string{items:[dynamic]string;append(&items,f"temporary");items}',
            'f::fn()->Rows{buf:[4]int;buf[:]}',
            'f::fn(x:string)->string{x[1:]}',
            'f::fn(x,y:string)->string{min(x,y)}',
            'view::proc(x:Rows)->Rows{return x}\nalias::view\nf::fn(x:Rows)->Rows{alias(x)}',
        ]
        for case in cases:
            with self.subTest(case=case):
                self.compare(prefix+case+'\nmain::proc(){}\n', command='check', fail='cannot return borrowed storage')

    def test_generic_specializations_callbacks_and_run_cannot_erase_borrows(self):
        prefix = '''package main
id::fn(x:$T)->T{x}
alias::id
S::[]int
'''
        for use in ['r:=alias([]int{1});delete(r)',
                    'r:=#run alias([]int{1});delete(r)',
                    'cb:proc(S)->S=alias;r:=cb([]int{1});delete(r)',
                    'cb:fn(S)->S=alias;r:=cb([]int{1});delete(r)']:
            with self.subTest(use=use):
                self.compare(prefix+'main::proc(){'+use+'}\n', command='check', fail='cannot return borrowed storage')
        self.compare(prefix+'main::proc(){cb:fn(int)->int=alias;if cb(7)!=7{panic("scalar")}}\n')

    def test_owned_helpers_forward_one_payload_allocation(self):
        source = '''package main
import "core:mem"
import "core:slice"
duplicate::fn(x:[]$T)->[]T{slice.clone(x)}
alias::duplicate
copy_tail::fn(x:[]int)->[]int{alias(x[1:])}
wrapper::fn(x:[]int)->[]int{owned:=copy_tail(x);return owned}
Counter::struct{backing:mem.Allocator,payloads:int}
count::proc(data:rawptr,mode:mem.Allocator_Mode,size,align:int,old:rawptr,old_size:int,loc:=#caller_location)->([]byte,mem.Allocator_Error){
 c:=cast(^Counter)data
 if (mode==.Alloc || mode==.Alloc_Non_Zeroed) && size==997*size_of(int){c.payloads+=1}
 a:=c.backing
 return a.procedure(a.data,mode,size,align,old,old_size,loc)
}
main::proc(){
 c:=Counter{backing=context.allocator};context.allocator={count,&c}
 source:[998]int;source[1]=9
 v:=wrapper(source[:]);defer delete(v)
 if c.payloads!=1 || len(v)!=997 || v[0]!=9{panic("expected one payload allocation")}
 v[0]=5;if source[1]!=9{panic("borrowed storage escaped")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=['--comptime=required', *flags])

    def test_soa_inputs_results_and_cached_run(self):
        source = '''package main
import pure "thor:pure"
Row::struct{x:u8,y:i64}
Rows::#soa[]Row
copy_rows::fn(rows:Rows)->#soa[]Row{
 dst:=make(#soa[]Row,len(rows));for i in 0..<len(rows){dst[i]=rows[i]};dst
}
build::fn(x:int)->Rows{
 fixed:#soa[3]Row;fixed[1]={3,i64(x)}
 copy_rows(fixed[:])
}
at::fn(rows:$T,i:int)->i64{rows[i].y}
alias::build
dynamic_rows::fn(n:int)->#soa[dynamic]Row{
 dst:=make(#soa[dynamic]Row,1,7);dst[0]={2,i64(n)};dst
}
Empty::struct{x,y:struct{}}
empty_columns::fn(n:int)->#soa[]Empty{make(#soa[]Empty,n)}
Payload::struct{x:[]int,y:i64}
nested::fn(n:int)->#soa[dynamic]Payload{
 dst:=make(#soa[dynamic]Payload,1,7);dst[0]={make([]int,2),i64(n)};dst[0].x[1]=n;dst
}
fixed_rows::fn(n:int)->#soa[2]Row{dst:#soa[2]Row;dst[1]={4,i64(n)};dst}
main::proc(){
 n:=71;a:=alias(n);defer pure.destroy(a)
 b:=#run alias(71);defer pure.destroy(b)
 if len(a)!=3 || a[1].x!=3 || at(b,1)!=71{panic("SOA contents")}
 a[1].y=8;if b[1].y!=71{panic("shared SOA")}
 c:=#run dynamic_rows(19);defer pure.destroy(c)
 if len(c)!=1 || cap(c)!=7 || c[0].y!=19{panic("SOA capacity")}
 z:=#run empty_columns(5);defer pure.destroy(z);if len(z)!=5{panic("zero-sized columns")}
 e:=#run nested(17);defer pure.destroy(e);if e[0].x[1]!=17 || cap(e)!=7{panic("nested SOA")}
 d:=#run fixed_rows(23);if d[1].x!=4 || d[1].y!=23{panic("fixed SOA")}
}
'''
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(source, flags=flags)
        # Reuse the unchecked build's cache and execute the materialized rows again.
        warm = self.invoke('rust', flags=[*native.UNCHECKED, '--cache-report'])
        self.assertEqual(warm.returncode, 0, warm.stderr)
        report = next(json.loads(line)['thor_cache'] for line in warm.stdout.splitlines()
                      if line.startswith('{"thor_cache"'))
        self.assertGreaterEqual(report['hits'], 5, report)

    def test_soa_rows_and_nested_fields_keep_input_provenance(self):
        prefix = 'package main\nRow::struct{x:int,items:[]int}\nS::#soa[]Row\n'
        for body, message in [
            ('f::fn()->[]int{p:=make(S,5);a,_:=soa_unzip(p);return a}', 'cannot return borrowed storage'),
            ('f::fn(a:S)->S{a}', 'cannot return borrowed storage'),
            ('f::fn(a:S)->[]int{row:=a[0];row.items}', 'cannot return borrowed storage'),
            ('f::fn(a:S)->int{a[0].x+=1;0}', 'borrowed from an input'),
            ('f::fn(a:S)->int{a.x[0]=1;0}', 'borrowed from an input'),
            ('f::fn(a:$T)->int{a.x[0]=1;0}\ng::proc(a:S){_=f(a)}', 'borrowed from an input'),
            ('f::fn(a:#soa[2]Row)->[]int{a[0].items}', 'cannot return borrowed storage'),
            ('f::fn(a:#soa[dynamic]Row)->int{len(a)}','slice view'),
            ('R::struct{p:^int}\nf::fn(a:#soa[]R)->int{len(a)}','Unsupported pure value type')
        ]:
            with self.subTest(body=body):
                self.compare(prefix+body+'\nmain::proc(){}\n',command='check',fail=message)
