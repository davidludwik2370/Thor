"""Library-managed arena lifetimes, parent ownership, and native scope exits."""
import unittest

from tests import test_thor as base


UNCHECKED = ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert']
IMPORTS = 'import "thor:basic"\nimport "base:runtime"\nimport "core:mem"\nimport "core:mem/virtual"\n'
AUDIT = '''
Audit::struct{
 backing:mem.Allocator,
 allocations,frees,live:int,
 pointer:rawptr,
 size:int,
}
audit::proc(data:rawptr,mode:mem.Allocator_Mode,size,alignment:int,
 old:rawptr,old_size:int,loc:=#caller_location)->([]byte,mem.Allocator_Error){
 state:=cast(^Audit)data
 if mode==.Free && old!=nil {
  if old!=state.pointer || old_size!=state.size {panic("management free used wrong pointer/size")}
  arena:=cast(^virtual.Arena)old
  if arena.curr_block!=nil || arena.total_reserved!=0 {panic("arena blocks not released before management free")}
  state.live-=1;state.frees+=1
 }
 result,err:=state.backing.procedure(state.backing.data,mode,size,alignment,old,old_size,loc)
 if err==nil && (mode==.Alloc || mode==.Alloc_Non_Zeroed) {
  if state.live!=0 {panic("unexpected management allocation")}
  state.allocations+=1;state.live+=1;state.pointer=raw_data(result);state.size=size
 }
 return result,err
}
check_live::proc(state:^Audit,wanted:int){if state.live!=wanted {panic("defer order")}}
'''


class ScopedArenaTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def both(self, text, **options):
        for flags in [[], UNCHECKED]:
            with self.subTest(flags=flags): self.invoke(text, flags=flags, **options)

    def test_example_allocates_in_arena_and_restores_outer_context(self):
        self.both(self.program('''
outer,temp:=context.allocator,context.temp_allocator
{
 context.allocator=basic.scoped_arena()
 values:=make([]int,100)
 text:=f"Created {len(values)} values"
 if text!="Created 100 values" {panic("fstring")}
 basic.print(text)
 if context.temp_allocator!=temp {panic("temporary allocator changed")}
 if (cast(^virtual.Arena)context.allocator.data).curr_block==nil {panic("not arena backed")}
}
if context.allocator!=outer || context.temp_allocator!=temp {panic("context restoration")}
''', IMPORTS), expected='Created 100 values')

    def test_cleanup_frees_blocks_and_management_using_saved_parent(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
parent:=mem.Allocator{audit,&state}
context.allocator=parent
{
 defer check_live(&state,0)
 context.allocator=basic.scoped_arena()
 if state.live!=1 {panic("premature cleanup")}
 defer check_live(&state,1)
 values:=make([]byte,3*1024*1024)
 values[0],values[len(values)-1]=7,9
 // Replacing the context must not change which parent owns management state.
 context.allocator=mem.nil_allocator()
 if values[0]!=7 || values[len(values)-1]!=9 {panic("arena data")}
}
if state.live!=0 || state.allocations!=1 || state.frees!=1 {panic("management leak")}
if context.allocator!=parent {panic("outer context changed")}
''', IMPORTS+AUDIT))

    def test_nested_scopes_keep_outer_allocations_alive(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
context.allocator=mem.Allocator{audit,&state}
{
 context.allocator=basic.scoped_arena()
 outer:=context.allocator
 value:=new(int);value^=7
 {
  context.allocator=basic.scoped_arena()
  inner:=new(int);inner^=13
  if inner^!=13 || value^!=7 {panic("nested values")}
 }
 if context.allocator!=outer || value^!=7 || state.live!=1 {panic("nested restoration")}
}
if state.live!=0 || state.allocations!=1 || state.frees!=1 {panic("nested cleanup")}
''', IMPORTS+AUDIT))

    def test_return_break_continue_and_repeated_creation(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
context.allocator=mem.Allocator{audit,&state}
if early()!=42 || state.live!=0 {panic("early return")}
for i in 0..<4 {
 context.allocator=basic.scoped_arena()
 value:=new(int);value^=i
 if i==0 {continue}
 if i==2 {break}
}
if state.live!=0 || state.allocations!=4 || state.frees!=4 {panic("loop exit cleanup")}
''', IMPORTS+AUDIT+'''
early::proc()->int{
 context.allocator=basic.scoped_arena()
 value:=new(int);value^=42
 return value^
}
'''))

    def test_free_all_retains_management_and_arena_can_allocate_again(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
context.allocator=mem.Allocator{audit,&state}
{
 context.allocator=basic.scoped_arena()
 first:=make([]int,128);first[0]=3
 _=free_all(context.allocator)
 second:=make([]int,128);second[0]=9
 if second[0]!=9 || state.live!=1 {panic("reset/reuse")}
}
if state.live!=0 || state.frees!=1 {panic("reset cleanup")}
''', IMPORTS+AUDIT))

    def test_explicit_parent_allocations_can_survive_inner_scope(self):
        self.both(self.program('''
outer:=context.allocator
kept:[]int
existing:=make([dynamic]int,allocator=outer)
defer delete(existing)
{
 context.allocator=basic.scoped_arena()
 values:=[]int{3,7,9}
 kept=make([]int,len(values),outer)
 copy(kept,values)
 append(&existing,13)
}
if kept[1]!=7 || existing[0]!=13 || existing.allocator!=outer {panic("explicit allocator/retained container")}
delete(kept,outer)
''', IMPORTS))

    def test_fstrings_big_numbers_sets_and_basic_helpers_share_the_arena(self):
        self.both(self.program('''
{
 context.allocator=basic.scoped_arena()
 values:=basic.range(10)
 ordered:=basic.sorted(values)
 set:=basic.to_set(values)
 integer:Integer=123456789012345678901234567890
 rational:=Rational(22,13)
 text:=f"{integer:,} {rational} {len(set)}"
 if len(ordered)!=10 || len(set)!=10 || len(text)==0 {panic("arena helpers")}
 basic.print(text)
}
''', IMPORTS), expected='123,456,789,012,345,678,901,234,567,890 22/13 10')

    def test_namespaced_wildcard_selected_and_aliased_calls(self):
        for declaration, call in [('import "thor:basic"','basic.scoped_arena()'),
                                  ('import * "thor:basic"','scoped_arena()'),
                                  ('import _ "thor:basic" / scoped_arena','scoped_arena()'),
                                  ('import "thor:basic"\ncreate::basic.scoped_arena','create()')]:
            with self.subTest(declaration=declaration):
                self.invoke(self.program('''
state:=Audit{backing=context.allocator}
context.allocator=mem.Allocator{audit,&state}
{context.allocator='''+call+''';if state.live!=1{panic("factory lifetime")}}
if state.live!=0 || state.frees!=1{panic("alias cleanup")}
''', declaration+'\nimport "core:mem"\nimport "core:mem/virtual"\n'+AUDIT))

    def test_native_odin_consumer_uses_the_same_automatic_cleanup(self):
        self.invoke(self.program('native()'), directory=True, files={'native.odin': '''package main
import "thor:basic"
native::proc(){
 outer:=context.allocator
 {context.allocator=basic.scoped_arena();data:=make([]int,12);data[0]=7;if data[0]!=7{panic("native data")}}
 if context.allocator!=outer{panic("native restoration")}
}
'''})

    def test_initial_management_allocation_failure_panics_in_release_too(self):
        self.both(self.program('context.allocator=mem.nil_allocator();context.allocator=basic.scoped_arena()',
                               IMPORTS), fail='scoped_arena: could not allocate arena management state')

    def test_private_state_is_not_exported(self):
        for body in ['basic.scoped_arena()', '_=basic._Scoped_Arena', '_=basic._destroy_scoped_arena']:
            with self.subTest(body=body):
                self.invoke(self.program(body, IMPORTS), command='check', fail='Error:')

    def test_explicit_allocator_and_discarded_result_cleanup_without_changing_context(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
parent:=mem.Allocator{audit,&state}
context.allocator=parent
{
 local:=basic.scoped_arena()
 data:=make([]int,7,local)
 if len(data)!=7 || state.live!=1 || context.allocator!=parent {panic("explicit allocator")}
}
{_=basic.scoped_arena();if state.live!=1{panic("discarded allocator lifetime")}}
if state.live!=0 || state.allocations!=2 || state.frees!=2 {panic("explicit/discarded cleanup")}
''', IMPORTS+AUDIT))

    def test_lowered_expression_cannot_shorten_automatic_cleanup_lifetime(self):
        for expression in ['switch{case true:basic.scoped_arena();case:parent}',
                           'f"{basic.scoped_arena()}"', '([basic.scoped_arena()])[index]']:
            with self.subTest(expression=expression):
                self.invoke(self.program('parent:=context.allocator;index:=-1;_='+expression, IMPORTS),
                            command='check', fail='automatic scope cleanup')

    def test_immutable_and_native_constant_index_preserve_caller_scope(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
parent:=mem.Allocator{audit,&state}
context.allocator=parent
{
 local :> basic.scoped_arena()
 data:=make([]int,7,local)
 if len(data)!=7 || state.live!=1 {panic("immutable allocator lifetime")}
}
{
 // A statically known array index stays in the original scope.
 local:=([basic.scoped_arena()])[-1]
 data:=make([]int,7,local)
 if len(data)!=7 || state.live!=1 {panic("constant index allocator lifetime")}
}
if state.live!=0 || state.allocations!=2 || state.frees!=2 {panic("binding cleanup")}
''', IMPORTS+AUDIT))

    def test_debug_tracking_multiple_factories_and_early_exit(self):
        _, result=self.invoke(self.program('''
outer:=context.allocator
for i in 0..<3 {
 first,second:=basic.scoped_arena(),basic.scoped_arena()
 a,b:=make([]int,100,first),make([]int,200,second)
 a[0],b[0]=7,13
 if a[0]+b[0]!=20 {panic("multiple allocator lifetime")}
 context.allocator=first
 text:=f"Created {len(a)} values"
 if text!="Created 100 values" {panic("debug fstring")}
 {context.allocator=basic.scoped_arena();_=make([]int,10000)}
 if i==0 {continue}
 if i==1 {break}
}
if context.allocator!=outer {panic("debug context restoration")}
basic.print("debug arena cleanup passed")
''', IMPORTS), flags=['-debug'], expected='debug arena cleanup passed')
        self.assertNotIn('Thor allocation tracker:',result.stderr)
        self.assertNotIn('AddressSanitizer:',result.stderr)

    def test_named_allocator_can_be_used_in_switch_and_type_queries(self):
        self.both(self.program('''
state:=Audit{backing=context.allocator}
parent:=mem.Allocator{audit,&state}
context.allocator=parent
{
 local:=basic.scoped_arena()
 context.allocator=switch{case true:local;case:parent}
 data:=make([]int,10)
 if len(data)!=10 || state.live!=1 {panic("named switch allocator")}
 _=type_of(basic.scoped_arena())
 text:=f"{size_of(basic.scoped_arena())}"
 delete(text)
}
if state.live!=0 || state.frees!=1 {panic("type query executed factory")}
''', IMPORTS+AUDIT))

    def test_inactive_branches_and_nested_user_procedure_keep_native_scopes(self):
        self.both(self.program('''
when false {
 _=f"{basic.scoped_arena()}"
}
// This factory belongs to the user procedure, whose scope stays intact.
text:=f"{(proc()->int{context.allocator=basic.scoped_arena();values:=make([]int,7);return len(values)})()}"
if text!="7" {panic("nested user procedure")}
delete(text)
''', IMPORTS))


if __name__=='__main__': unittest.main()
