"""Borrowed headers must survive stores, spreads, pop results and redirection."""
import unittest
from tests import test_rust_compiler as native


class RustHelperGraphEdgeTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    compare=native.RustCompilerTests.compare
    program=native.RustCompilerTests.program

    def test_append_and_pop_forms_preserve_borrowed_element_storage(self):
        appends=['append(&deck,input)','append(array=&deck,arg=input)',
                 'source:=[input];append(&deck,..source[:])',
                 'source:=[input];append(&deck,**source)',
                 'source:=[input];append(array=&deck,args=source[:])',
                 'source:=struct{row:[]int}{input};append(&deck,**source)']
        pops=['value:=pop(&deck)','value,ok:=pop_safe(&deck);_=ok',
              'value:=pop_front(&deck)','value,ok:=pop_front_safe(&deck);_=ok']
        for append in appends:
            for pop in pops:
                with self.subTest(append=append,pop=pop):
                    self.compare(self.program('data:=[1,2];_=work(data[:])','''
change::proc(data:[]int){data[0]=99}
work::fn(input:[]int)->int{deck:[dynamic;2][]int;'''+append+';'+pop+''';change(value);return input[0]}
'''),command='check',fail='borrowed from an input')

    def test_generic_record_and_array_spreads_preserve_borrowed_fields(self):
        for value in ('[input]','struct{row:[]int}{input}'):
            with self.subTest(value=value):
                self.compare(self.program('data:=[1,2];_=work(data[:])','''
put::proc(out:^$A,source:$S){append(out,**source)}
change::proc(data:[]int){data[0]=99}
work::fn(input:[]int)->int{deck:[dynamic;2][]int;put(&deck,'''+value+''');value:=pop(&deck);change(value);return input[0]}
'''),command='check',fail='borrowed from an input')

    def test_copying_headers_through_a_helper_preserves_their_backing_alias(self):
        prefix='replace::proc(dst,src:[][]int){copy(dst,src)}\n'
        self.compare(self.program('data:=[1,2];_=work(data[:])',prefix+'''
work::fn(input:[]int)->int{local:=[9,10];rows:=[local[:]];source:=[input];replace(rows[:],source[:]);rows[0][0]=7;return 0}
'''),command='check',fail='borrowed from an input')
        source=self.program('n:=3;if work(n)!=7{panic("copied aliases")}',prefix+'''
work::fn(n:int)->int{first:=[n,n];second:=[n+1,n+1];rows:=[first[:]];source:=[second[:]];replace(rows[:],source[:]);rows[0][0]=7;return second[0]}
''')
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_local_stack_storage_cannot_escape_through_out_parameters(self):
        cases=[
            ('escape::proc(out:^^int){local:=7;out^=&local}','pointer:^int;escape(&pointer);return 0'),
            ('escape::proc(out:^[]int){local:=[7,8];out^=local[:]}','data:[]int;escape(&data);return 0'),
            ('escape::proc(out:^[]int){out^=[]int{7,8}}','data:[]int;escape(&data);return 0'),
            ('escape::proc(out:^[]int){out^={7,8}}','data:[]int;escape(&data);return 0'),
            ('escape::proc(out:^[]int){out^=([7,8])[:]}','data:[]int;escape(&data);return 0'),
            ('Box::struct{data:[]int}\nescape::proc(out:^Box){out^=Box{{7,8}}}','data:Box;escape(&data);return 0'),
            ('escape::proc(out:[][]int){copy(out,[][]int{[]int{7,8}})}','rows:=[[]int{}];escape(rows[:]);return 0'),
            ('put::proc(out:^[]int,value:[]int){out^=value}\nescape::proc(out:^[]int){put(out,({7,8}))}','data:[]int;escape(&data);return 0'),
            ('put::proc(out:^[]int,value:[]int){out^=value}\nescape::proc(out:^[]int){put(value={7,8},out=out)}','data:[]int;escape(&data);return 0'),
            ('put::proc(out:^^int,value:^int){out^=value}\nescape::proc(out:^^int){local:=7;put(out,&local)}','pointer:^int;escape(&pointer);return 0'),
        ]
        for prefix,body in cases:
            with self.subTest(prefix=prefix):
                self.compare(self.program('_=work()',prefix+'\nwork::fn()->int{'+body+'}\n'),
                             command='check',flags=['-debug'],fail='local stack storage')

    def test_pointer_redirection_keeps_the_callers_original_target(self):
        source=self.program('n:=3;if work(n)!=11{panic("redirected target")}', '''
put::proc(out:^^int,value:^int){out^=value}
set::proc(out:^int){out^+=1}
work::fn(n:int)->int{local:=n;p:^int;put(&p,&local);set(p);return local+7}
''')
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')
        self.compare(self.program('data:=[1,2];_=work(data[:])','''
put::proc(out:^^int,value:^int){out^=value}
set::proc(out:^int){out^+=1}
work::fn(input:[]int)->int{p:^int;put(&p,&input[0]);set(p);return 0}
'''),command='check',fail='borrowed from an input')

    def test_variadic_scalar_copies_are_local_to_the_call(self):
        source=self.program('n:=3;if work(n)!=9||n!=3{panic("variadic scalar copy")}', '''
change::proc(values:..int)->int{values[0]=9;return values[0]}
work::fn(n:int)->int{return change(n,n+1)}
''')
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(source,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_variadic_reference_elements_preserve_owned_and_borrowed_targets(self):
        prefix='change::proc(rows:..[]int){rows[0][0]=9}\n'
        for flags in (['-debug'],native.UNCHECKED+['--track-allocations=basic']):
            with self.subTest(flags=flags):
                result=self.compare(self.program('n:=3;if work(n)!=9{panic("variadic reference")}',prefix+
                    'work::fn(n:int)->int{data:=[n,n];change(data[:]);return data[0]}\n'),flags=flags)
                self.assertEqual(result.stderr,'')
        for annotation,call,actual in [('[]int','change(input)','data[:]'),
                                        ('[][]int','change(..input)','rows[:]'),
                                        ('[1][]int','change(**input)','rows')]:
            with self.subTest(call=call):
                self.compare(self.program('data:=[1,2];rows:=[data[:]];_=rows;_=work('+actual+')',prefix+
                    'work::fn(input:'+annotation+')->int{'+call+';return 0}\n'),
                    command='check',fail='borrowed from an input')


if __name__=='__main__':unittest.main()
