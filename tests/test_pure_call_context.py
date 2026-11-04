"""Purity imposed by fn callers: valid local mutation and contextual errors."""
import json
import unittest

from tests import test_thor as base


class PureCallContextTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def errors(self,text,**kwargs):
        _,result=self.invoke(text,command='check',flags=['-json-errors',*kwargs.pop('flags',[])],
                             fail='Pure',**kwargs)
        return json.loads(result.stderr)['errors']

    def assert_context(self,text,errors,call,root,cause):
        start=text.encode().index(call.encode())
        relevant=[error for error in errors if error['pos']['offset']==start]
        self.assertTrue(relevant,errors)
        for error in relevant:
            self.assertEqual(error['pos']['end_offset'],start+len(call.encode()))
            self.assertIn("Pure function '"+root+"'",' '.join(error['msgs']))
            notes=error.get('related',[])
            offset=text.encode().index(cause.encode())
            self.assertTrue(any(note['pos']['offset']<=offset<note['pos']['end_offset']
                                and note['pos']['file']==error['pos']['file'] for note in notes),error)
            self.assertTrue(any("fn '"+root+"' requires" in note['message'] for note in notes),error)
            for hidden in ['__thor_','.thor-sdk','pure_validate','checked_call','Thor expression','SIGBUS']:
                self.assertNotIn(hidden,json.dumps(error))

    def test_hilo_fixed_capacity_shuffle_reports_fn_call_not_pointer_parameter(self):
        text=self.program('n:=7;_=simulate_game(n)', '''import "core:math/rand"
DECK_LEN::8
Deck::[dynamic;DECK_LEN]int
reset_shuffled::proc(deck:^Deck){
    clear(deck)
    for i in 0..<DECK_LEN{append(deck,i)}
    rand.shuffle(deck[:])
}
simulate_game::fn(n:int)->int{
    deck:Deck
    reset_shuffled(&deck)
    return pop(&deck)+n
}
''')
        errors=self.errors(text)
        self.assertEqual(len(errors),1,errors)
        self.assert_context(text,errors,'reset_shuffled(&deck)','simulate_game','rand.shuffle(deck[:])')
        self.assertIn('ambient context',' '.join(errors[0]['msgs']))
        self.assertIn('simulate_game -> reset_shuffled -> rand.shuffle',' '.join(errors[0]['msgs']))
        self.assertTrue(any('standard-library' in note['message'] for note in errors[0]['related']))
        self.assertNotIn('Unsupported pure value type',json.dumps(errors))
        # The same helper is valid in an ordinary call graph.
        self.invoke(text.replace('simulate_game::fn','simulate_game::proc'),command='check')

    def test_transitive_user_effect_preserves_actual_cause_and_pure_root(self):
        text=self.program('n:=7;_=work(n)', '''state:int
leaf::proc(value:^int){value^=state}
alias::leaf
middle::proc(value:^int){alias(value)}
work::fn(n:int)->int{local:=n;middle(&local);return local}
''')
        errors=self.errors(text)
        self.assertEqual(len(errors),1,errors)
        self.assert_context(text,errors,'middle(&local)','work','state}')
        self.assertIn('work -> middle -> alias',' '.join(errors[0]['msgs']))
        self.invoke(text.replace('work::fn','work::proc'),command='check')

    def test_generic_inactive_and_ordinary_callers_are_not_blamed(self):
        text=self.program('n:=7;_=bad(n);_=good("safe");_=ordinary(n)', '''state:int
leaf::proc(value:$T)->T{when T==int{return state}else{return value}}
ordinary::proc(value:int)->int{return leaf(value)}
bad::fn(n:int)->int{return leaf(n)}
good::fn(n:string)->string{return leaf(n)}
''')
        errors=self.errors(text)
        self.assertEqual(len(errors),1,errors)
        # Match the first leaf(n), in the fn rather than leaf(value) above.
        self.assert_context(text,errors,'leaf(n)','bad','state}')
        self.assertNotIn("Pure function 'good'",json.dumps(errors))
        self.assertNotIn("fn 'ordinary'",json.dumps(errors))
        self.invoke(text.replace('bad::fn','bad::proc'),command='check')

    def test_recursive_helper_chain_terminates_and_keeps_root_call(self):
        text=self.program('n:=7;_=work(n)', '''state:int
leaf::proc(value:^int,n:int){if n==0{value^=state;return};middle(value,n-1)}
middle::proc(value:^int,n:int){leaf(value,n)}
work::fn(n:int)->int{local:=n;middle(&local,n);return local}
''')
        errors=self.errors(text)
        self.assert_context(text,errors,'middle(&local,n)','work','state;')

    def test_imported_helper_notes_retain_both_original_files(self):
        text=self.program('n:=7;_=work(n)', '''import "library"
work::fn(n:int)->int{local:=n;library.reset(&local);return local}
''')
        library='package library\nstate:int\nreset::proc(value:^int){value^=state}\n'
        errors=self.errors(text,files={'library/main.odin':library})
        self.assertEqual(len(errors),1,errors)
        error=errors[0]
        self.assertEqual(error['pos']['offset'],text.index('library.reset(&local)'))
        self.assertTrue(any(note['pos']['file'].endswith('/library/main.odin')
                            and note['pos']['offset']==library.index('state}') for note in error['related']),error)
        self.assertTrue(any("fn 'work' requires" in note['message'] for note in error['related']))

    def test_fixed_capacity_local_mutation_runtime_and_run_in_checked_and_release(self):
        text=self.program('n:=4;result:=work(n);if result!=7||VALUE!=7{panic("wrong result")}', '''
Deck::[dynamic;4]int
reset::proc(deck:^Deck){clear(deck);append(deck,1,2,3)}
read::proc(deck:^Deck)->int{return pop(deck)}
work::fn(n:int)->int{deck:Deck;reset(&deck);return n+read(&deck)}
VALUE::#run work(4)
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_append_and_pop_cannot_launder_borrowed_element_storage(self):
        for append in ['append(&deck,input)', 'append(array=&deck,arg=input)',
                       'source:=[input];append(&deck,..source[:])', 'source:=[input];append(&deck,**source)',
                       'source:=[input];append(array=&deck,args=source[:])',
                       'source:=struct{row:[]int}{input};append(&deck,**source)']:
            for pop in ['value:=pop(&deck)', 'value,ok:=pop_safe(&deck);_=ok',
                        'value:=pop_front(&deck)', 'value,ok:=pop_front_safe(&deck);_=ok']:
                with self.subTest(append=append,pop=pop):
                    text=self.program('data:=[1,2];_=work(data[:])', '''
change::proc(data:[]int){data[0]=99}
work::fn(input:[]int)->int{deck:[dynamic;2][]int;'''+append+';'+pop+''';change(value);return input[0]}
''')
                    errors=self.errors(text)
                    self.assertTrue(any('borrowed from an input' in ' '.join(error['msgs']) for error in errors),errors)
                    self.assert_context(text,errors,'change(value)','work','data[0]=99')

    def test_generic_appender_cannot_hide_references_in_struct_spreads(self):
        for source in ['[input]','struct{row:[]int}{input}']:
            with self.subTest(source=source):
                text=self.program('data:=[1,2];_=work(data[:])','''
put::proc(out:^$A,source:$S){append(out,**source)}
change::proc(data:[]int){data[0]=99}
work::fn(input:[]int)->int{deck:[dynamic;2][]int;put(&deck,'''+source+''');value:=pop(&deck);change(value);return input[0]}
''')
                errors=self.errors(text)
                self.assert_context(text,errors,'change(value)','work','data[0]=99')

    def test_public_fixed_capacity_inputs_still_fail_at_the_fn_signature(self):
        text=self.program('data:[dynamic;2]int;_=work(data)',
                          'work::fn(input:[dynamic;2]int)->int{return len(input)}\n')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='Unsupported pure value type')
        errors=json.loads(result.stderr)['errors']
        self.assertEqual(errors[0]['pos']['offset'],text.index('[dynamic;2]int'))

    def test_fixed_capacity_output_elements_retain_heap_lifetime_but_reject_stack_escape(self):
        prefix='''Rows::[dynamic;2][]int
publish::proc(out:^Rows,n:int){data:=make([]int,n);data[0]=9;append(out,data)}
work::fn(n:int)->int{rows:Rows;publish(&rows,n);value:=pop(&rows);return value[0]}
VALUE::#run work(3)
'''
        text=self.program('n:=3;if work(n)!=9||VALUE!=9{panic("output lifetime")}',prefix)
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertEqual(result.stderr,'')
        broken=text.replace('data:=make([]int,n)','data:=[1,2]').replace('append(out,data)','append(out,data[:])')
        errors=self.errors(broken)
        self.assertTrue(any('local stack storage' in ' '.join(error['msgs']) for error in errors),errors)
        self.assert_context(broken,errors,'publish(&rows,n)','work','append(out,data[:])')

    def test_contextual_purity_errors_suppress_only_dependent_index_cascades(self):
        prefix='state:int\nleaf::proc(n:int)->int{data:=[1,2];return data[state]}\nwork::fn(n:int)->int{return leaf(n)}\n'
        text=self.program('n:=7;_=work(n)',prefix)
        errors=self.errors(text)
        self.assertEqual(len(errors),1,errors)
        self.assert_context(text,errors,'leaf(n)','work','state]}')
        independent=text.replace('return data[state]','_=data[1.5];return data[state]')
        errors=self.errors(independent)
        self.assertTrue(any('must have an integer type' in ' '.join(error['msgs']) for error in errors),errors)
        self.assertFalse(any('Thor expression.normalize' in ' '.join(error['msgs']) for error in errors),errors)

    def test_no_argument_fn_and_explicit_run_keep_the_inner_fn_call_context(self):
        prefix='''import "core:math/rand"
reset::proc(value:^int){value^=rand.int_max(10)}
work::fn()->int{local:=0;reset(&local);return local}
'''
        for usage in ['main::proc(){_=work()}\n','VALUE::work()\nmain::proc(){}\n',
                      'VALUE::#run work()\nmain::proc(){}\n']:
            with self.subTest(usage=usage):
                text='package main\n'+prefix+usage
                errors=self.errors(text)
                self.assert_context(text,errors,'reset(&local)','work','rand.int_max(10)')


if __name__=='__main__':unittest.main()
